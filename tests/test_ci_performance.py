import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class SecretScanTest(unittest.TestCase):
    def setUp(self) -> None:
        if not shutil.which("gitleaks"):
            self.skipTest("gitleaks is required")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        scripts = self.repo / "scripts/checks"
        scripts.mkdir(parents=True)
        shutil.copy(ROOT / "scripts/checks/secrets.sh", scripts)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        self.git("add", "scripts")
        self.git("commit", "-qm", "baseline")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=self.repo, text=True, capture_output=True, check=True
        )

    def commit(self, content: str) -> None:
        (self.repo / "credentials.txt").write_text(content)
        self.git("add", "credentials.txt")
        self.git("commit", "-qm", "change")

    def scan(
        self, source: str, base: str, tag: str = ""
    ) -> subprocess.CompletedProcess[str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("CI_")}
        env.update(
            CI_PIPELINE_SOURCE=source,
            CI_COMMIT_BEFORE_SHA=base,
            CI_MERGE_REQUEST_DIFF_BASE_SHA=base,
            CI_COMMIT_TAG=tag,
        )
        return subprocess.run(
            ["bash", "scripts/checks/secrets.sh"],
            cwd=self.repo,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_incremental_scan_detects_added_then_deleted_secret(self) -> None:
        self.commit('password = "' + "aB9xK2mP7qR4sT8vW1yZ6nC3dF5hJ0lG" + '"')
        self.commit("removed")
        for source in ("push", "merge_request_event"):
            with self.subTest(source=source):
                result = self.scan(source, self.base)
                self.assertEqual(result.returncode, 1, result.stderr)

    def test_incremental_scan_excludes_old_history_but_schedule_scans_it(self) -> None:
        self.commit('password = "' + "aB9xK2mP7qR4sT8vW1yZ6nC3dF5hJ0lG" + '"')
        self.commit("removed")
        base = self.git("rev-parse", "HEAD").stdout.strip()
        self.commit("clean")
        self.assertEqual(self.scan("push", base).returncode, 0)
        for source in ("schedule", "web", "api"):
            with self.subTest(source=source):
                self.assertEqual(self.scan(source, base).returncode, 1)
        self.assertEqual(self.scan("push", base, tag="release").returncode, 1)

    def test_missing_base_fails_instead_of_scanning_incomplete_range(self) -> None:
        result = self.scan("push", "a" * 40)
        self.assertNotEqual(result.returncode, 0)

    def test_new_branch_scans_full_history(self) -> None:
        self.commit("clean")
        result = self.scan("push", "0" * 40)
        self.assertEqual(result.returncode, 0, result.stderr)


class CancellationTest(unittest.TestCase):
    def test_dataplane_depends_on_validated_plan_without_manual_registry_jobs(
        self,
    ) -> None:
        pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        dataplane = pipeline["opnsense_dataplane"]
        self.assertEqual(
            dataplane["needs"], [{"job": "opnsense_plan", "artifacts": False}]
        )
        ancestors: set[str] = set()
        pending = ["opnsense_dataplane"]
        while pending:
            for dependency in pipeline[pending.pop()].get("needs", []):
                name = dependency if isinstance(dependency, str) else dependency["job"]
                if name not in ancestors:
                    ancestors.add(name)
                    pending.append(name)
        self.assertTrue(
            {
                "repository_tests",
                "flake_check",
                "secret_scan",
                "nix_format",
                "yaml_schema",
                "registry_lock",
                "opnsense_inventory",
            }
            <= ancestors
        )
        for name in ancestors:
            self.assertFalse(
                any(
                    rule.get("when") == "manual"
                    for rule in pipeline[name].get("rules", [])
                ),
                name,
            )
        self.assertEqual(
            dataplane["resource_group"], pipeline["opnsense_plan"]["resource_group"]
        )
        self.assertEqual(pipeline["opnsense_apply"]["rules"][0]["when"], "manual")

    def test_consolidated_checks_remain_required_by_consumers(self) -> None:
        pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        for name, job in pipeline.items():
            if isinstance(job, dict) and "repository_tests" in job.get("needs", []):
                with self.subTest(job=name):
                    for gate in ("nix_format", "yaml_schema", "registry_lock"):
                        self.assertIn(gate, job["needs"])

    def test_optional_cache_population_preserves_fleet_build_gate(self) -> None:
        pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        self.assertIn("nix_cache", pipeline[".deploy_fleet_base"]["needs"])
        cache = pipeline["nix_cache"]
        self.assertEqual(cache["rules"][-1]["when"], "manual")
        self.assertFalse(cache["rules"][-1]["allow_failure"])
        self.assertEqual(
            cache["rules"][0]["if"], "$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH"
        )
        self.assertIn(
            "scripts.ci.validate fleet",
            pipeline[".deploy_fleet_base"]["before_script"][0],
        )
        self.assertEqual(
            pipeline[".nix_cache"]["resource_group"],
            pipeline[".flake_check"]["resource_group"],
        )

    def test_sandbox_job_owns_unit_reports_and_consumers_require_it(self) -> None:
        pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        self.assertEqual(
            pipeline[".flake_check"]["artifacts"]["reports"]["junit"],
            "artifacts/ci/tests.xml",
        )
        self.assertEqual(
            pipeline[".repository_tests"]["artifacts"]["reports"]["junit"],
            "artifacts/ci/nix-integration.xml",
        )
        self.assertNotIn("CI_TEST_REPORT", pipeline[".repository_tests"]["variables"])
        for name, job in pipeline.items():
            if isinstance(job, dict) and "repository_tests" in job.get("needs", []):
                self.assertIn("flake_check", job["needs"], name)

    def test_mirror_is_serialized_and_mutation_jobs_do_not_retry(self) -> None:
        pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        self.assertEqual(pipeline["sync_to_github"]["resource_group"], "github-mirror")
        for name, job in pipeline.items():
            if isinstance(job, dict) and job.get("stage") == "deploy":
                with self.subTest(job=name):
                    self.assertEqual(job["retry"], 0)
        self.assertEqual(
            pipeline["default"]["retry"]["when"],
            ["runner_system_failure", "runner_external_dependency_failure"],
        )

    def test_main_and_feature_flake_checks_share_resource_group(self) -> None:
        pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        self.assertEqual(
            pipeline[".flake_check"]["resource_group"], "nix-flake-evaluation"
        )
        for name in ("flake_check", "flake_check_feature"):
            self.assertIn(".flake_check", pipeline[name]["extends"])
            self.assertNotIn("resource_group", pipeline[name])

    def test_repository_test_consumers_retain_secret_scan_gate(self) -> None:
        pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        for name, job in pipeline.items():
            if isinstance(job, dict) and "repository_tests" in job.get("needs", []):
                with self.subTest(job=name):
                    self.assertIn("secret_scan", job["needs"])
        self.assertEqual(pipeline[".secret_scan"]["stage"], "test")

    def test_scheduled_jobs_are_not_shadowed_by_a_manual_rule(self) -> None:
        pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        # A scheduled pipeline also runs on the default branch, so a
        # `when: manual` rule listed before the schedule rule matches first and
        # the nightly run never happens. Both registry maintenance jobs shipped
        # with the order reversed and had never run automatically.
        shadowed = set()
        for name, job in pipeline.items():
            if not isinstance(job, dict):
                continue
            rules = job.get("rules")
            if not isinstance(rules, list):
                continue
            seen_manual = False
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                if rule.get("when") == "manual":
                    seen_manual = True
                if "schedule" in str(rule.get("if", "")) and seen_manual:
                    shadowed.add(name)
                    break
        self.assertEqual(shadowed, set())

    def test_one_registry_client_at_a_time(self) -> None:
        pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())

        # The drift check ran in the test stage with needs: [] while the registry
        # jobs ran in the registry stage, so all three hit the registry from the
        # runner's one address at once. skopeo issues an unauthenticated /v2/
        # challenge before every read and zot counts those against failDelay, so
        # ~1100 failed auth attempts in a couple of minutes tripped the
        # brute-force protection and zot started answering 401 to valid
        # credentials partway through a run, which read as missing images.
        def effective(name: str, key: str):
            """A job's own value, or the one it inherits from a template."""
            job = pipeline[name]
            if key in job:
                return job[key]
            for template in job.get("extends") or []:
                if key in pipeline.get(template, {}):
                    return pipeline[template][key]
            return None

        # Every job that authenticates against the registry must share the group,
        # so they queue instead of competing.
        for name in (
            "registry_drift_check",
            "registry_lock_import",
            "registry_promote_first_party",
            "registry_retention_reconcile",
        ):
            with self.subTest(job=name):
                self.assertEqual(effective(name, "resource_group"), "registry-content")
        # All four must also sit in the same stage, or the earlier stage would
        # still overlap the later one regardless of the group.
        for name in (
            "registry_drift_check",
            "registry_lock_import",
            "registry_promote_first_party",
            "registry_retention_reconcile",
        ):
            with self.subTest(stage=name):
                self.assertEqual(effective(name, "stage"), "registry")
        # Moving drift_check into the registry stage must not create a wait for a
        # job that itself needs drift_check, which would deadlock the stage.
        self.assertEqual(effective("registry_drift_check", "needs"), [])

    def test_registry_writes_depend_on_specific_gates_not_the_whole_stage(self) -> None:
        pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())

        # A job without `needs` inherits the previous stage, so any red job in
        # it skips this one. That froze promotion twice: once on a gitleaks false
        # positive and once on a pyright error, both unrelated to the registry.
        advancing = ("registry_promote_first_party", "registry_update_import")
        # Promotion also validates the lock it writes in-job via
        # `just check-registry`, so dropping repository_tests is safe.
        self.assertIn(
            "just check-registry",
            "\n".join(pipeline["registry_promote_first_party"]["script"]),
        )
        for name in advancing:
            with self.subTest(job=name):
                needs = pipeline[name]["needs"]
                names = {n if isinstance(n, str) else n["job"] for n in needs}
                # repository_tests is intentionally absent: it would pull the
                # unit suite into every promotion, and the policy test above
                # requires anything consuming it to consume the whole check
                # suite, which is the coupling being removed.
                self.assertIn("secret_scan", names)
                self.assertNotIn("repository_tests", names)

        # registry_lock_import keeps only the lock it re-imports; the retention
        # reconciler stays fully ungated. Both restore an invariant, so a red
        # repository must not be able to pause them.
        self.assertEqual(
            [
                n if isinstance(n, str) else n["job"]
                for n in pipeline["registry_lock_import"]["needs"]
            ],
            ["registry_lock"],
        )
        reconcile = pipeline[".registry_retention_reconcile"]["needs"]
        self.assertEqual(reconcile, [])

    def test_live_verification_never_gates_registry_writes(self) -> None:
        pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        # drift_check verifies a live registry behind an edge that intermittently
        # answers for it. It alerts on its own, so letting it gate writes would
        # couple every write to that flakiness, which is how a phantom failure
        # once stopped the pipeline outright.
        for name, job in pipeline.items():
            if not isinstance(job, dict) or name.startswith("."):
                continue
            needs = job.get("needs")
            if not isinstance(needs, list):
                continue
            names = {n if isinstance(n, str) else n.get("job") for n in needs}
            with self.subTest(job=name):
                self.assertNotIn("registry_drift_check", names)

    def test_upstream_import_gates_stay_asymmetric(self) -> None:
        pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())

        # Repairs the already-committed, already-reviewed lock, so it is safe to
        # run unattended and self-heals a partial import.
        lock_import = pipeline["registry_lock_import"]["rules"]
        self.assertIn("schedule", str(lock_import[0]["if"]))
        self.assertNotEqual(lock_import[0].get("when"), "manual")

        # It also runs unattended when the lock itself changes, because a lock
        # entry is only a declaration that the content is in the registry and
        # this job is the only thing that fetches it. 5d01b10 added an image to
        # the inventory and deployed a manifest referencing it, and with the job
        # manual nothing ever copied the bytes.
        on_lock_change = lock_import[1]
        self.assertEqual(on_lock_change["if"], '$CI_COMMIT_BRANCH == "main"')
        self.assertNotIn("when", on_lock_change)
        self.assertEqual(
            set(on_lock_change["changes"]),
            {"registry/images.inventory.json", "registry/images.lock.json"},
        )

        # Imports a candidate that is still awaiting review. Running it nightly
        # would put unreferenced content in the registry and remove the human
        # checkpoint on an upstream change, so it must stay manual-only.
        update_import = pipeline["registry_update_import"]["rules"]
        self.assertEqual(
            update_import, [{"if": '$CI_COMMIT_BRANCH == "main"', "when": "manual"}]
        )
        # The candidate is only proposed by a job that never writes, so this
        # review gate is the only thing between resolve and the copy.
        self.assertEqual(
            pipeline["registry_update_import"]["needs"][0]["job"], "registry_resolve"
        )

    def test_nightly_registry_maintenance_actually_runs_on_a_schedule(self) -> None:
        pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        for name in (".registry_retention_reconcile", "registry_gc_fixture"):
            with self.subTest(job=name):
                rules = pipeline[name]["rules"]
                self.assertIn("schedule", str(rules[0]["if"]))
                self.assertNotEqual(rules[0].get("when"), "manual")

    def test_only_validation_jobs_are_interruptible(self) -> None:
        pipeline = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
        self.assertEqual(
            pipeline["workflow"]["auto_cancel"]["on_new_commit"], "interruptible"
        )
        allowed = {
            ".nix_format",
            ".repository_tests",
            "jev_decision_gate",
            ".flake_check",
            ".yaml_schema",
            ".secret_scan",
            ".registry_lock",
            ".registry_auth_consistency",
            ".registry_drift_check",
            ".registry_gc_fixture",
        }
        self.assertEqual(
            {
                name
                for name, job in pipeline.items()
                if isinstance(job, dict) and job.get("interruptible")
            },
            allowed,
        )
