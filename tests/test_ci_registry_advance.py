from __future__ import annotations

import unittest

from scripts.ci.registry_advance import adopt_candidates, ineligible, renovate_bump

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
            "registry/images.inventory.json",
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
            "recreate": check(deployment(strategy="Recreate")),
            "no probe": check(deployment(probe=False)),
            "init container": check(deployment(init=True)),
            "cronjob": check(deployment(kind="CronJob")),
            "denied namespace": check(deployment(namespace="home-assistant")),
            "denied repository": check(
                deployment(), repository="upstream/docker.io/library/postgres"
            ),
        }
        for name, reason in cases.items():
            with self.subTest(case=name):
                self.assertIsNotNone(reason)

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

    def test_ineligible_or_current_consumers_are_not_adopted(self) -> None:
        for manifest, current in (
            (deployment(strategy="Recreate"), {NEW}),
            (deployment(digest=NEW), {NEW}),
            (deployment(), {OLD}),
        ):
            with self.subTest(current=current):
                files = [("gitops/media/arr.yaml", manifest)]
                self.assertEqual(
                    adopt_candidates(files, {REPOSITORY: current}, self.lock(), POLICY),
                    [],
                )


if __name__ == "__main__":
    unittest.main()
