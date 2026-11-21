from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.ci import registry_advance
from scripts.ci.registry_advance import (
    REVERTED,
    VERIFIED,
    adopt_candidates,
    adopted_repositories,
    bump_declaration,
    flux_applied,
    ineligible,
    mirror_pairs,
    rebuild_mirror,
    renovate_bump,
    reverted_digests,
    unsettled_adopts,
    workloads_using,
)

OLD = "sha256:" + "1" * 64
NEW = "sha256:" + "2" * 64
REPOSITORY = "upstream/ghcr.io/flaresolverr/flaresolverr"
DECLARATION = "gitops/media/registry-sources.yaml"
POLICY = {
    "deny_namespaces": ["home-assistant"],
    "deny_repositories": ["upstream/docker.io/library/postgres"],
}

DIFF = f"""diff --git a/{DECLARATION} b/{DECLARATION}
--- a/{DECLARATION}
+++ b/{DECLARATION}
@@ -1 +1 @@
-  - image: ghcr.io/flaresolverr/flaresolverr:v3.5.0@{OLD}
+  - image: ghcr.io/flaresolverr/flaresolverr:v3.5.2@{NEW}
diff --git a/registry/images.lock.json b/registry/images.lock.json
--- a/registry/images.lock.json
+++ b/registry/images.lock.json
@@ -1 +1 @@
+      "reference": "ghcr.io/other/image:1@{NEW}",
"""


def deployment(
    *,
    kind: str = "Deployment",
    strategy: str | None = None,
    probe: bool = True,
    namespace: str = "media",
    init: bool = False,
    digest: str = OLD,
) -> str:
    image = f"registry.rupan.dev/{REPOSITORY}@{digest}"
    container = f"        - name: app\n          image: {image}\n"
    if probe:
        container += (
            "          readinessProbe:\n            httpGet: {path: /, port: 8191}\n"
        )
    pod = (
        "      initContainers:\n" + container
        if init
        else "      containers:\n" + container
    )
    rolled = f"  strategy:\n    type: {strategy}\n" if strategy else ""
    return (
        f"apiVersion: apps/v1\nkind: {kind}\nmetadata:\n  name: flaresolverr\n"
        f"  namespace: {namespace}\nspec:\n{rolled}  template:\n    spec:\n{pod}"
    )


def cronjob(
    *,
    init: bool = False,
    digest: str = OLD,
    schedule: str = "0 * * * *",
    suspend: bool = False,
) -> str:
    image = f"registry.rupan.dev/{REPOSITORY}@{digest}"
    key = "initContainers" if init else "containers"
    suspended = "  suspend: true\n" if suspend else ""
    return (
        "apiVersion: batch/v1\nkind: CronJob\nmetadata:\n  name: sweep\n"
        f"  namespace: media\nspec:\n  schedule: '{schedule}'\n{suspended}"
        "  jobTemplate:\n"
        "    spec:\n      template:\n        spec:\n"
        f"          {key}:\n            - name: sweep\n              image: {image}\n"
    )


def check(
    manifest: str,
    old_tag: str = "v3.5.0",
    new_tag: str = "v3.5.2",
    repository: str = REPOSITORY,
):
    import yaml

    documents = [("gitops/media/arr.yaml", yaml.safe_load(manifest))]
    return ineligible(
        repository, old_tag, new_tag, documents, f"{repository}@{OLD}", POLICY
    )


class RenovateBumpTest(unittest.TestCase):
    def test_reads_the_one_declaration_and_ignores_record_files(self) -> None:
        files = [
            DECLARATION,
            "registry/images.lock.json",
        ]
        self.assertEqual(
            renovate_bump(files, DIFF),
            (
                f"ghcr.io/flaresolverr/flaresolverr:v3.5.0@{OLD}",
                f"ghcr.io/flaresolverr/flaresolverr:v3.5.2@{NEW}",
            ),
        )

    def test_other_files_or_several_declarations_are_not_taken(self) -> None:
        self.assertIsNone(renovate_bump([DECLARATION, "gitops/media/arr.yaml"], DIFF))
        self.assertIsNone(
            renovate_bump([DECLARATION, "gitops/voice/registry-sources.yaml"], DIFF)
        )


class EligibilityTest(unittest.TestCase):
    def test_a_probed_rolling_deployment_on_the_same_major_is_eligible(self) -> None:
        self.assertIsNone(check(deployment()))
        self.assertIsNone(check(deployment(strategy="RollingUpdate")))

    def test_updates_that_could_take_a_service_down_are_refused(self) -> None:
        cases = {
            "major": check(deployment(), "v3.5.0", "v4.0.0"),
            "floating tag": check(deployment(), "latest", "latest"),
            "no probe": check(deployment(probe=False)),
            "statefulset": check(deployment(kind="StatefulSet")),
            "recreate without probe": check(
                deployment(strategy="Recreate", probe=False)
            ),
            "denied namespace": check(deployment(namespace="home-assistant")),
            "denied repository": check(
                deployment(), repository="upstream/docker.io/library/postgres"
            ),
        }
        for name, reason in cases.items():
            with self.subTest(case=name):
                self.assertIsNotNone(reason)

    def test_recreate_deployments_and_cronjobs_are_taken_with_a_backstop(
        self,
    ) -> None:
        # A Recreate restart is verified and reverted if it fails; a CronJob
        # is verified by its next scheduled run. Neither needs a probe.
        self.assertIsNone(check(deployment(strategy="Recreate")))
        self.assertIsNone(check(cronjob()))
        self.assertIsNone(check(cronjob(schedule="30 2 * * *")))
        self.assertIsNone(check(cronjob(schedule="@daily")))
        # Weekly jobs (the library restore drill: restic, alpine/k8s) settle
        # on their next run within the eight-day window.
        self.assertIsNone(check(cronjob(schedule="0 6 * * 0")))
        self.assertIsNone(check(cronjob(schedule="@weekly")))

    def test_windowed_namespaces_adopt_only_inside_their_hours(self) -> None:
        import datetime

        policy = {
            "deny_namespaces": [],
            "window_namespaces": {
                "namespaces": ["music-assistant"],
                "timezone": "America/Chicago",
                "start_hour": 1,
                "end_hour": 3,
            },
        }
        manifest = deployment(namespace="music-assistant")
        documents = [
            (path, doc)
            for path, doc in workloads_using(
                [("gitops/music-assistant/server.yaml", manifest)],
                f"{REPOSITORY}@{OLD}",
            )
        ]

        def reason(hour_utc: int) -> str | None:
            now = datetime.datetime(2026, 11, 20, hour_utc, 30, tzinfo=datetime.UTC)
            return ineligible(
                REPOSITORY,
                "v3.5.0",
                "v3.5.2",
                documents,
                f"{REPOSITORY}@{OLD}",
                registry_advance.effective_policy(policy, now),
            )

        # 07:30 UTC is 01:30 in Chicago in November; 12:30 UTC is 06:30.
        self.assertIsNone(reason(7))
        self.assertIn("adopts only between 01:00 and 03:00", reason(12) or "")
        # Mirrors import an image without restarting anything: never held.
        self.assertIsNone(
            ineligible(
                REPOSITORY,
                "v3.5.0",
                "v3.5.2",
                documents,
                f"{REPOSITORY}@{OLD}",
                policy,
            )
        )

    def test_the_policy_opens_cloudflare_and_windows_user_facing_apps(self) -> None:
        policy = json.loads(
            (Path(__file__).resolve().parents[1] / registry_advance.POLICY).read_text()
        )
        self.assertNotIn("cloudflare", policy["deny_namespaces"])
        self.assertEqual(
            sorted(policy["window_namespaces"]["namespaces"]),
            ["home-assistant", "music-assistant", "voice"],
        )
        # The window ends before fleet-reboot's 03:00-05:00 Chicago window.
        self.assertLessEqual(policy["window_namespaces"]["end_hour"], 3)
        self.assertIn("kube-system", policy["deny_namespaces"])

    def test_init_containers_are_taken_because_verify_checks_them(self) -> None:
        # alpine in slskd's init container waited on a person for weeks.
        self.assertIsNone(check(deployment(init=True)))
        self.assertIsNone(check(cronjob(init=True)))

    def test_cronjobs_no_run_can_verify_in_the_window_are_refused(self) -> None:
        # A CronJob is never run off schedule (backups, drills and deletions
        # among them), so a monthly one could not settle.
        self.assertIn("less often than weekly", check(cronjob(schedule="0 0 1 * *")))
        self.assertIn("suspended", check(cronjob(suspend=True)))

    def test_an_update_nothing_uses_is_not_taken(self) -> None:
        self.assertEqual(
            ineligible(REPOSITORY, "v1", "v1", [], f"{REPOSITORY}@{OLD}", POLICY),
            "no workload references the old mirror",
        )


class AdoptCandidatesTest(unittest.TestCase):
    def lock(self) -> dict:
        return {
            "images": [
                {
                    "kind": "upstream",
                    "destination_repository": REPOSITORY,
                    "digest": digest,
                    "source": {"tag": tag},
                }
                for digest, tag in ((OLD, "v3.5.0"), (NEW, "v3.5.2"))
            ]
        }

    def test_a_consumer_left_on_an_undeclared_digest_is_adopted(self) -> None:
        files = [("gitops/media/arr.yaml", deployment())]
        found = adopt_candidates(files, {REPOSITORY: {NEW}}, self.lock(), POLICY)
        self.assertEqual(
            found,
            [
                (
                    REPOSITORY,
                    {
                        "gitops/media/arr.yaml": {
                            f"registry.rupan.dev/{REPOSITORY}@{OLD}": f"registry.rupan.dev/{REPOSITORY}@{NEW}"
                        }
                    },
                    NEW,
                )
            ],
        )

    def test_an_ineligible_consumer_does_not_hold_back_the_others(self) -> None:
        files = [
            ("gitops/media/arr.yaml", deployment()),
            ("gitops/home-assistant/app.yaml", deployment(namespace="home-assistant")),
            ("gitops/media/sweep.yaml", deployment(kind="StatefulSet")),
        ]
        found = adopt_candidates(files, {REPOSITORY: {NEW}}, self.lock(), POLICY)
        self.assertEqual(
            [(r, sorted(paths), new) for r, paths, new in found],
            [(REPOSITORY, ["gitops/media/arr.yaml"], NEW)],
        )

    def test_each_tag_of_a_repository_is_its_own_candidate(self) -> None:
        other_old, other_new = "sha256:" + "3" * 64, "sha256:" + "4" * 64
        lock = {
            "images": [
                {
                    "kind": "upstream",
                    "destination_repository": REPOSITORY,
                    "digest": digest,
                    "source": {"tag": tag},
                }
                for digest, tag in (
                    (OLD, "3.14-alpine"),
                    (NEW, "3.14-alpine"),
                    (other_old, "3.12-slim"),
                    (other_new, "3.12-slim"),
                )
            ]
        }
        files = [
            ("gitops/media/arr.yaml", deployment()),
            ("gitops/media/other.yaml", deployment(digest=other_old)),
        ]
        found = adopt_candidates(files, {REPOSITORY: {NEW, other_new}}, lock, POLICY)
        self.assertEqual(
            sorted((sorted(paths), new) for _, paths, new in found),
            sorted(
                [
                    (["gitops/media/arr.yaml"], NEW),
                    (["gitops/media/other.yaml"], other_new),
                ]
            ),
        )

    def test_ineligible_or_current_consumers_are_not_adopted(self) -> None:
        for manifest, current in (
            (deployment(kind="StatefulSet"), {NEW}),
            (deployment(digest=NEW), {NEW}),
            (deployment(), {OLD}),
        ):
            with self.subTest(current=current):
                files = [("gitops/media/arr.yaml", manifest)]
                self.assertEqual(
                    adopt_candidates(files, {REPOSITORY: current}, self.lock(), POLICY),
                    [],
                )


class RebuildMirrorTest(unittest.TestCase):
    """Renovate's bump is rebuilt on current main, not merged from its branch."""

    OLD_REF = f"ghcr.io/flaresolverr/flaresolverr:v3.5.0@{OLD}"
    NEW_REF = f"ghcr.io/flaresolverr/flaresolverr:v3.5.2@{NEW}"

    def repository(self, directory: str) -> Path:
        root = Path(directory)

        def git(*command: str) -> None:
            subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@t", *command],
                cwd=root,
                check=True,
                capture_output=True,
            )

        git("init", "-q", "-b", "main")
        (root / "gitops/media").mkdir(parents=True)
        (root / DECLARATION).write_text(
            f"items:\n  - image: {self.OLD_REF}\n  - image: docker.io/other:1@{OLD}\n"
        )
        (root / "registry").mkdir()
        (root / "registry/images.lock.json").write_text("{}\n")
        git("add", "-A")
        git("commit", "-q", "-m", "main")
        return root

    def head(self, root: Path) -> str:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_only_the_declared_line_changes_and_a_moved_main_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.repository(directory)
            self.assertEqual(
                bump_declaration(root, self.OLD_REF, self.NEW_REF), DECLARATION
            )
            text = (root / DECLARATION).read_text()
            self.assertIn(self.NEW_REF, text)
            self.assertIn(f"docker.io/other:1@{OLD}", text)
            self.assertIsNone(bump_declaration(root, self.OLD_REF, self.NEW_REF))

    def test_a_bump_past_the_line_limit_gets_the_yamllint_exemption(self) -> None:
        """music-assistant 2.10.4 -> 2.10.12 grew its line from 120 to 121
        characters; #1946 failed fmt-check and every run rebuilt it."""
        old = f"ghcr.io/music-assistant/server:2.10.4@{OLD}"
        new = f"ghcr.io/music-assistant/server:2.10.12@{NEW}"
        text = f"items:\n  - image: {old}\n  - image: docker.io/other:1@{OLD}\n"
        fitted = registry_advance.fit_line_length(text.replace(old, new), new)
        line = next(line for line in fitted.splitlines() if new in line)
        self.assertTrue(line.endswith("# yamllint disable-line rule:line-length"))
        self.assertIn(f"  - image: docker.io/other:1@{OLD}\n", fitted)
        # Already exempt or short enough: unchanged.
        self.assertEqual(registry_advance.fit_line_length(fitted, new), fitted)
        short = "items:\n  - image: docker.io/a:1@sha256:abc\n"
        self.assertEqual(
            registry_advance.fit_line_length(short, "docker.io/a:1"), short
        )

    def test_rebuild_stages_the_bump_and_its_records_on_main(self) -> None:
        def fake_mirror(root: Path, base: str) -> list[str]:
            # The real mirror resolves what the commit added since base.
            added = subprocess.run(
                ["git", "diff", "--no-ext-diff", f"{base}...HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertIn(self.NEW_REF, added)
            (root / "registry/images.lock.json").write_text('{"images": ["new"]}\n')
            return ["new"]

        with tempfile.TemporaryDirectory() as directory:
            root = self.repository(directory)
            before = self.head(root)
            with patch.object(registry_advance, "mirror", side_effect=fake_mirror):
                paths = rebuild_mirror(root, self.OLD_REF, self.NEW_REF)
            self.assertEqual(self.head(root), before, "the scratch commit is undone")
            staged = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.split()
            self.assertEqual(
                sorted(staged), sorted([DECLARATION, "registry/images.lock.json"])
            )
            self.assertEqual(paths[0], DECLARATION)

    def test_a_failed_mirror_leaves_main_where_it_was(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.repository(directory)
            before = self.head(root)
            with (
                patch.object(
                    registry_advance, "mirror", side_effect=SystemExit("no record")
                ),
                self.assertRaises(SystemExit),
            ):
                rebuild_mirror(root, self.OLD_REF, self.NEW_REF)
            self.assertEqual(self.head(root), before)


MIRRORED_OLD = f"registry.rupan.dev/{REPOSITORY}@{OLD}"
MIRRORED_NEW = f"registry.rupan.dev/{REPOSITORY}@{NEW}"
ADOPT_DIFF = f"""diff --git a/gitops/media/arr.yaml b/gitops/media/arr.yaml
--- a/gitops/media/arr.yaml
+++ b/gitops/media/arr.yaml
@@ -1 +1 @@
-          image: {MIRRORED_OLD}  # yamllint disable-line rule:line-length
+          image: {MIRRORED_NEW}  # yamllint disable-line rule:line-length
"""
NOW = 1_800_000_000.0


def merged(number: int, branch: str, hours_ago: float, body: str = "") -> dict:
    import datetime

    stamp = datetime.datetime.fromtimestamp(NOW - hours_ago * 3600, datetime.UTC)
    return {
        "number": number,
        "head": {"ref": branch},
        "merged": True,
        "merged_at": stamp.isoformat(),
        "body": body,
    }


class SettleTest(unittest.TestCase):
    """Each adopt is verified once Flux applies it, or reverted for good."""

    def test_adopt_diff_maps_new_mirrors_back_to_old(self) -> None:
        self.assertEqual(
            mirror_pairs(ADOPT_DIFF),
            {"gitops/media/arr.yaml": {MIRRORED_NEW: MIRRORED_OLD}},
        )

    def test_every_unsettled_adopt_in_the_window_settles_oldest_first(self) -> None:
        """One adopt waiting on a weekly job no longer stalls every other one."""
        pulls = [
            merged(5, "registry-advance/adopt-aaa", 1),
            merged(4, "registry-advance/mirror-bbb", 0.5),
            merged(3, "registry-advance/adopt-ccc", 6 * 24),
        ]
        comments = {5: [], 3: []}
        picked = unsettled_adopts(pulls, lambda n: comments[n], NOW)
        self.assertEqual([p["number"] for p in picked], [3, 5])
        comments[5] = [{"body": VERIFIED}]
        comments[3] = [{"body": REVERTED}]
        self.assertEqual(unsettled_adopts(pulls, lambda n: comments[n], NOW), [])
        old = [merged(6, "registry-advance/adopt-ddd", 9 * 24)]
        self.assertEqual(unsettled_adopts(old, lambda n: [], NOW), [])

    def test_closed_pulls_are_read_back_through_the_whole_window(self) -> None:
        """One page held a day of PRs; an eight-day window needs more."""
        import datetime

        def page(start: int, hours_ago: float) -> list[dict]:
            stamp = datetime.datetime.fromtimestamp(
                NOW - hours_ago * 3600, datetime.UTC
            ).isoformat()
            return [{"number": start + i, "updated_at": stamp} for i in range(50)]

        pages = {1: page(0, 1), 2: page(50, 5 * 24), 3: page(100, 9 * 24), 4: []}
        with patch.object(
            registry_advance,
            "api",
            side_effect=lambda token, path: pages[int(path.rsplit("=", 1)[1])],
        ) as api:
            pulls = registry_advance.recent_closed("token", NOW)
        self.assertEqual(len(pulls), 150)
        self.assertEqual(api.call_count, 3)

    def test_an_adopt_holds_the_repository_it_moves(self) -> None:
        self.assertEqual(
            adopted_repositories(ADOPT_DIFF),
            {REPOSITORY},
        )

    def test_flux_must_apply_a_revision_at_least_as_new_as_the_merge(self) -> None:
        kustomizations = [
            {
                "spec": {"path": "./gitops/media"},
                "status": {"lastAppliedRevision": "main@sha1:abc"},
            },
            {
                "spec": {"path": "./gitops"},
                "status": {"lastAppliedRevision": "main@sha1:old"},
            },
        ]
        paths = ["gitops/media/arr.yaml"]
        dates = {"abc": NOW, "old": NOW - 3600}
        self.assertTrue(flux_applied(kustomizations, paths, NOW - 60, dates.get))
        self.assertFalse(flux_applied(kustomizations, paths, NOW + 60, dates.get))
        self.assertFalse(
            flux_applied(kustomizations, paths, NOW - 60, lambda sha: None)
        )

    def test_a_reverted_digest_is_never_adopted_again(self) -> None:
        body = f"#5 did not roll out\n\nreverted: {REPOSITORY}@{NEW}\n"
        revert = merged(6, "registry-advance/revert-5", 1, body)
        unmerged = dict(revert, merged=False)
        other = merged(7, "renovate/foo", 1, body)
        self.assertEqual(reverted_digests([revert]), {f"{REPOSITORY}@{NEW}"})
        self.assertEqual(reverted_digests([unmerged, other]), set())

    def test_a_cronjob_settles_on_its_next_scheduled_run(self) -> None:
        pull = merged(5, "registry-advance/adopt-aaa", 1)
        for runs, outcome in (
            (
                ([], ["media/cronjob/sweep: no scheduled run on the new image yet"]),
                "waiting",
            ),
            (([], []), "verified"),
            (
                (["media/cronjob/sweep: its run on the new image failed"], []),
                "reverted",
            ),
        ):
            with (
                self.subTest(outcome=outcome),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                (root / "gitops/media").mkdir(parents=True)
                (root / "gitops/media/arr.yaml").write_text(
                    f"image: {MIRRORED_NEW}  # yamllint disable-line rule:line-length\n"
                )
                comments = []

                def fake_api(token, path, *, data=None, raw=False, comments=comments):
                    if data:
                        comments.append(data["body"])
                    return ADOPT_DIFF if raw else {}

                kustomizations = subprocess.CompletedProcess(
                    [], 0, stdout='{"items": []}'
                )
                with (
                    patch.object(registry_advance, "api", side_effect=fake_api),
                    patch.object(registry_advance, "verify", return_value=[]),
                    patch.object(registry_advance, "scheduled_runs", return_value=runs),
                    patch.object(registry_advance, "publish") as publish,
                    patch.object(
                        registry_advance.subprocess, "run", return_value=kustomizations
                    ),
                ):
                    self.assertEqual(registry_advance.settle(root, "t", pull), outcome)
                if outcome == "waiting":
                    # Neither marked nor reverted: the next run checks again.
                    self.assertEqual(comments, [])
                    publish.assert_not_called()
                elif outcome == "verified":
                    self.assertIn(VERIFIED, comments[0])
                else:
                    self.assertIn(REVERTED, comments[0])
                    publish.assert_called_once()

    def test_a_failed_rollout_is_reverted_and_a_healthy_one_verified(self) -> None:
        pull = merged(5, "registry-advance/adopt-aaa", 1)
        for failures, outcome in (
            ([], "verified"),
            (["media/x: timed out"], "reverted"),
        ):
            with (
                self.subTest(outcome=outcome),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                (root / "gitops/media").mkdir(parents=True)
                (root / "gitops/media/arr.yaml").write_text(
                    f"image: {MIRRORED_NEW}  # yamllint disable-line rule:line-length\n"
                )
                calls = []

                def fake_api(token, path, *, data=None, raw=False):
                    calls.append((path, data))
                    return ADOPT_DIFF if raw else {}

                kustomizations = subprocess.CompletedProcess(
                    [], 0, stdout='{"items": []}'
                )
                with (
                    patch.object(registry_advance, "api", side_effect=fake_api),
                    patch.object(registry_advance, "verify", return_value=failures),
                    patch.object(registry_advance, "publish") as publish,
                    patch.object(
                        registry_advance.subprocess, "run", return_value=kustomizations
                    ),
                ):
                    self.assertEqual(registry_advance.settle(root, "t", pull), outcome)
                comment = next(data["body"] for path, data in calls if data)
                if outcome == "verified":
                    publish.assert_not_called()
                    self.assertIn(VERIFIED, comment)
                else:
                    self.assertIn(REVERTED, comment)
                    self.assertIn(
                        MIRRORED_OLD, (root / "gitops/media/arr.yaml").read_text()
                    )
                    body = publish.call_args.args[3]
                    self.assertIn(f"reverted: {REPOSITORY}@{NEW}", body)
                    self.assertEqual(
                        reverted_digests(
                            [merged(6, publish.call_args.args[1], 0, body)]
                        ),
                        {f"{REPOSITORY}@{NEW}"},
                    )


BASE = "gitops/producer-base-images/base-images.yaml"
NODE = "upstream/docker.io/library/node"


class BaseImageTest(unittest.TestCase):
    """Producer base images mirror without a workload; projectctl's CI steps adopt."""

    def test_a_base_images_bump_is_read(self) -> None:
        diff = (
            f"diff --git a/{BASE} b/{BASE}\n--- a/{BASE}\n+++ b/{BASE}\n@@ -1 +1 @@\n"
            f"-  - image: docker.io/library/node:24-slim@{OLD}\n"
            f"+  - image: docker.io/library/node:24-slim@{NEW}\n"
        )
        self.assertEqual(
            renovate_bump([BASE, "registry/images.lock.json"], diff),
            (
                f"docker.io/library/node:24-slim@{OLD}",
                f"docker.io/library/node:24-slim@{NEW}",
            ),
        )

    def test_a_base_image_mirrors_without_a_consumer_but_not_across_majors(
        self,
    ) -> None:
        reference = f"{NODE}@{OLD}"
        self.assertIsNone(
            ineligible(NODE, "24-slim", "24-slim", [], reference, POLICY, base=True)
        )
        self.assertIsNotNone(
            ineligible(NODE, "24-slim", "26-slim", [], reference, POLICY, base=True)
        )
        self.assertEqual(
            ineligible(NODE, "24-slim", "24-slim", [], reference, POLICY),
            "no workload references the old mirror",
        )

    def test_projectctl_step_images_are_adopted(self) -> None:
        contracts = (
            f'{{"node-build-v1": {{"image": "registry.rupan.dev/{NODE}@{OLD}"' + "}}"
        )
        lock = {
            "images": [
                {
                    "kind": "upstream",
                    "id": "node-new",
                    "digest": NEW,
                    "destination_repository": NODE,
                    "source": {"tag": "24-slim"},
                },
                {
                    "kind": "upstream",
                    "id": "node-old",
                    "digest": OLD,
                    "destination_repository": NODE,
                    "source": {"tag": "24-slim"},
                },
            ]
        }
        candidates = adopt_candidates(
            [(registry_advance.CONTRACTS, contracts)], {NODE: {NEW}}, lock, POLICY
        )
        self.assertEqual([c[0] for c in candidates], [NODE])

    def test_untagged_consumer_record_does_not_hide_declared_tag(self) -> None:
        lock = {
            "images": [
                {
                    "kind": "upstream",
                    "id": "app-new",
                    "digest": NEW,
                    "destination_repository": REPOSITORY,
                    "source": {"tag": "v3.5.2"},
                },
                {
                    "kind": "upstream",
                    "id": "app-old-declared",
                    "digest": OLD,
                    "destination_repository": REPOSITORY,
                    "source": {"tag": "v3.5.0"},
                },
                {
                    "kind": "upstream",
                    "id": "app-old-consumer",
                    "digest": OLD,
                    "destination_repository": REPOSITORY,
                    "source": {"tag": None},
                },
            ]
        }
        candidates = adopt_candidates(
            [("gitops/media/arr.yaml", deployment())],
            {REPOSITORY: {NEW}},
            lock,
            POLICY,
        )
        self.assertEqual([c[0] for c in candidates], [REPOSITORY])

    def test_the_rebuild_finds_a_base_image_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / BASE).parent.mkdir(parents=True)
            (root / BASE).write_text(
                f"items:\n  - image: docker.io/library/node:24-slim@{OLD}\n"
            )
            self.assertEqual(
                bump_declaration(
                    root,
                    f"docker.io/library/node:24-slim@{OLD}",
                    f"docker.io/library/node:24-slim@{NEW}",
                ),
                BASE,
            )


if __name__ == "__main__":
    unittest.main()
