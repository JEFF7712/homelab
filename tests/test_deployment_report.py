import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.ci.deployment_report import (
    PRESENCE,
    SNAPSHOT,
    Reporter,
    build_plan,
    closure,
    main,
    markdown,
    snapshot,
)
from scripts.ci.operation import pack
from scripts.deploy_fleet import FLEET_HOSTS

ROOT = "/nix/store/" + "0" * 32 + "-source"
CURRENT = "/nix/store/" + "1" * 32 + "-nixos-system-homelab-04-26.11"
CANDIDATE = "/nix/store/" + "2" * 32 + "-nixos-system-homelab-04-26.11"
ROLLBACK = "/nix/store/" + "3" * 32 + "-nixos-system-homelab-04-26.11"
OLD_PACKAGE = "/nix/store/" + "4" * 32 + "-python3-3.13.8"
NEW_PACKAGE = "/nix/store/" + "5" * 32 + "-python3-3.14.0"
DRV = CANDIDATE + ".drv"


def tree(root: str, dependency: str, size: int) -> dict[str, object]:
    return {
        root: {"narSize": 100, "references": [root, dependency]},
        dependency: {"narSize": size, "references": []},
    }


class Target:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.snapshot_count = 0
        self.snapshots = f"homelab-04\n{CURRENT}\nsystem-10-link\nsystem-8-link\t{ROLLBACK}\nsystem-10-link\t{CURRENT}\n"
        self.after: str | None = None
        self.closures = {
            CURRENT: tree(CURRENT, OLD_PACKAGE, 900),
            CANDIDATE: tree(CANDIDATE, NEW_PACKAGE, 1900),
            ROLLBACK: tree(ROLLBACK, OLD_PACKAGE, 900),
        }
        self.missing_roots: set[str] = set()
        self.drv_exists = True
        self.source_revision: str | None = "a" * 40
        self.configuration_revision: str | None = "b" * 40
        self.plan_stdout = json.dumps([{"drvPath": DRV, "outputs": {"out": CANDIDATE}}])
        self.plan_stderr = ""
        self.error_host: str | None = None

    def run(
        self, argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(argv)
        if argv[:3] == ["nix", "flake", "metadata"]:
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps(
                    {
                        "path": ROOT,
                        "locked": {"dir": "flake", "narHash": "sha256-retained-source"},
                        "revision": self.source_revision,
                        "url": f"git+file:///source?dir=flake&rev={self.source_revision}",
                        "fingerprint": "c" * 64,
                    }
                ),
                "",
            )
        if argv[:2] == ["nix", "eval"]:
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"drv_path": DRV, "store_path": CANDIDATE}), ""
            )
        if argv[0] != "ssh":
            raise AssertionError(argv)
        self.assert_query_options(kwargs)
        if self.error_host and self.error_host in argv:
            raise subprocess.TimeoutExpired(argv, 1)
        command = shlex.split(argv[-1])
        if command == ["sh", "-c", SNAPSHOT]:
            self.snapshot_count += 1
            output = (
                self.after
                if self.snapshot_count > 1 and self.after is not None
                else self.snapshots
            )
            return subprocess.CompletedProcess(argv, 0, output, "")
        if command == ["nixos-version", "--json"]:
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps(
                    {
                        "configurationRevision": self.configuration_revision,
                        "nixpkgsRevision": "d" * 40,
                    }
                ),
                "",
            )
        if command[:2] == ["nix", "path-info"]:
            data = self.closures.get(command[-1])
            return subprocess.CompletedProcess(
                argv,
                0 if data else 1,
                json.dumps(data),
                "path is not valid" if not data else "",
            )
        if command[:3] == ["sh", "-c", PRESENCE]:
            root = command[-1]
            return subprocess.CompletedProcess(
                argv,
                int(root in self.missing_roots),
                "\n".join(self.closures[root]),
                "Missing closure path" if root in self.missing_roots else "",
            )
        if command[:2] == ["nix-store", "--check-validity"]:
            return subprocess.CompletedProcess(
                argv,
                0 if self.drv_exists else 1,
                "",
                "candidate derivation absent" if not self.drv_exists else "",
            )
        if command[:5] == ["env", "LC_ALL=C", "NO_COLOR=1", "nix", "build"]:
            return subprocess.CompletedProcess(
                argv, 0, self.plan_stdout, self.plan_stderr
            )
        raise AssertionError(command)

    @staticmethod
    def assert_query_options(options: dict[str, object]) -> None:
        assert options["capture_output"] is True
        assert options["check"] is False
        assert float(str(options["timeout"])) > 0


class DeploymentReportTest(unittest.TestCase):
    def report(self, target: Target) -> dict[str, object]:
        return Reporter(runner=target.run).report_host(
            FLEET_HOSTS["homelab-04"], f"path:{ROOT}?dir=flake"
        )

    def test_realized_target_closures_report_identities_sizes_and_changes(self) -> None:
        target = Target()
        report = self.report(target)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["deployed"]["generation"], 10)
        self.assertEqual(report["candidate"]["flake_attr"], "homelab-04-registry")
        self.assertEqual(report["current_closure"]["nar_bytes"], 1000)
        self.assertEqual(report["candidate_closure"]["nar_bytes"], 2000)
        self.assertEqual(report["closure_delta"]["nar_bytes"], 1000)
        self.assertEqual(report["closure_delta"]["percent"], 100.0)
        self.assertEqual(
            report["packages"]["version_changed"],
            [{"name": "python3", "before": ["3.13.8"], "after": ["3.14.0"]}],
        )
        self.assertEqual(report["rollback"]["generation"], 8)
        self.assertTrue(report["rollback"]["closure_available"])
        self.assertEqual(report["plan"]["build_derivations"], [])

    def test_absent_candidate_is_unavailable_not_zero_and_never_realized(self) -> None:
        target = Target()
        del target.closures[CANDIDATE]
        target.drv_exists = False
        report = self.report(target)
        self.assertEqual(report["status"], "partial")
        for field in ("candidate_closure", "closure_delta", "packages", "plan"):
            self.assertEqual(report[field]["confidence"], "unavailable")
        self.assertNotIn("nar_bytes", report["candidate_closure"])
        self.assertNotIn("estimated_download_bytes", report["plan"])
        remote = [shlex.split(c[-1]) for c in target.commands if c[0] == "ssh"]
        self.assertFalse(
            any(
                c[:5] == ["env", "LC_ALL=C", "NO_COLOR=1", "nix", "build"]
                for c in remote
            )
        )

    def test_only_native_planning_and_read_commands_run_remotely(self) -> None:
        target = Target()
        self.report(target)
        for argv in target.commands:
            if argv[0] != "ssh":
                continue
            self.assertIn("StrictHostKeyChecking=yes", argv)
            command = shlex.split(argv[-1])
            if command[0] == "sh":
                self.assertIn(command[2], (SNAPSHOT, PRESENCE))
            elif command[0] == "nix":
                self.assertEqual(command[1], "path-info")
                self.assertIn("false", command)
                self.assertEqual(command[command.index("--store") + 1], "daemon")
            elif command[0] == "env":
                self.assertIn("--dry-run", command)
                self.assertIn("--no-link", command)
                self.assertEqual(command[command.index("--store") + 1], "daemon")
            else:
                self.assertIn(
                    command[:2],
                    (["nixos-version", "--json"], ["nix-store", "--check-validity"]),
                )

    def test_missing_rollback_dependency_is_not_a_verified_rollback(self) -> None:
        target = Target()
        target.missing_roots.add(ROLLBACK)
        report = self.report(target)
        self.assertEqual(report["rollback"]["generation"], 8)
        self.assertEqual(report["rollback"]["store_path"], ROLLBACK)
        self.assertEqual(report["rollback"]["confidence"], "unavailable")
        self.assertIsNone(report["rollback"]["closure_available"])
        self.assertEqual(report["status"], "partial")

    def test_registration_without_references_is_incomplete(self) -> None:
        for data in (
            {CURRENT: {"narSize": 1, "references": [OLD_PACKAGE]}},
            {CURRENT: None},
            {CURRENT: {"narSize": -1, "references": []}},
            {CURRENT: {"narSize": True, "references": []}},
            [],
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                closure(json.dumps(data), CURRENT)

    def test_changed_generation_invalidates_report(self) -> None:
        target = Target()
        target.after = target.snapshots.replace("system-10-link", "system-11-link")
        report = self.report(target)
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["stable_observation"]["confidence"], "unavailable")
        self.assertIn("Discard", report["stable_observation"]["reason"])

    def test_wrong_host_is_an_error_without_followup_probes(self) -> None:
        target = Target()
        target.snapshots = target.snapshots.replace("homelab-04", "homelab-05", 1)
        report = self.report(target)
        self.assertEqual(report["status"], "error")
        self.assertEqual(len(target.commands), 1)

    def test_profile_and_running_system_are_not_assumed_equal(self) -> None:
        target = Target()
        target.snapshots = (
            target.snapshots.replace(
                "system-10-link\nsystem-8", "system-11-link\nsystem-8"
            )
            + f"system-11-link\t{CANDIDATE}\n"
        )
        state = snapshot(target.snapshots, FLEET_HOSTS["homelab-04"])
        self.assertEqual(state["generation"], 10)
        self.assertEqual(state["profile_generation"], 11)
        self.assertEqual(state["rollback_generation"], 8)

    def test_nixpkgs_revision_is_not_reported_as_configuration_source(self) -> None:
        target = Target()
        target.configuration_revision = None
        report = self.report(target)
        self.assertIsNone(report["deployed_source"]["revision"])
        self.assertEqual(report["deployed_source"]["confidence"], "unavailable")

    def test_duplicate_running_closures_do_not_invent_a_generation_or_rollback(
        self,
    ) -> None:
        target = Target()
        target.snapshots = (
            target.snapshots.replace(
                "system-10-link\nsystem-8", "system-12-link\nsystem-8"
            )
            + f"system-11-link\t{CURRENT}\nsystem-12-link\t{CANDIDATE}\n"
        )
        report = self.report(target)
        self.assertIsNone(report["deployed"]["generation"])
        self.assertEqual(report["deployed"]["matching_generations"], [10, 11])
        self.assertEqual(report["deployed"]["generation_confidence"], "unavailable")
        self.assertEqual(report["rollback"]["confidence"], "unavailable")

    def test_exact_paths_detect_rebuild_without_version_change(self) -> None:
        target = Target()
        replacement = NEW_PACKAGE.replace("3.14.0", "3.13.8")
        target.closures[CANDIDATE] = tree(CANDIDATE, replacement, 900)
        report = self.report(target)
        self.assertEqual(report["packages"]["version_changed"], [])
        self.assertIn(replacement, report["packages"]["added_store_paths"])
        self.assertIn(OLD_PACKAGE, report["packages"]["removed_store_paths"])

    def test_native_plan_uses_remote_results_and_rounded_download_size(self) -> None:
        target = Target()
        target.plan_stderr = f"this derivation will be built:\n  {DRV}\nthese 2 paths will be fetched (1.25 MiB download, 2.00 MiB unpacked):\n  {NEW_PACKAGE}\n  {OLD_PACKAGE}\n"
        report = self.report(target)
        self.assertEqual(report["plan"]["build_derivations"], [DRV])
        self.assertEqual(report["plan"]["substitute_paths"], [NEW_PACKAGE, OLD_PACKAGE])
        self.assertEqual(report["plan"]["estimated_download_bytes"], 1310720)
        self.assertEqual(report["plan"]["confidence"], "estimated")

    def test_unknown_or_truncated_native_plan_fails_unavailable(self) -> None:
        target = Target()
        for output in (
            "these 2 derivations will be built:\n  " + DRV,
            "unexpected planner output",
            "this path will be fetched (1 TB download, 2 TB unpacked):",
            "don't know how to build these paths",
        ):
            with self.subTest(output=output), self.assertRaises(ValueError):
                build_plan(target.plan_stdout, output, DRV, CANDIDATE)
        with self.assertRaises(ValueError):
            build_plan("[]", "", DRV, CANDIDATE)

    def test_cli_freezes_source_and_writes_both_formats(self) -> None:
        target = Target()
        with (
            tempfile.TemporaryDirectory() as directory,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            code = main(
                ["--target", "homelab-04", "--json", "--output-dir", directory],
                runner=target.run,
            )
            self.assertEqual(code, 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["schema_version"], 1)
            self.assertEqual(report["candidate_source"]["revision"], "a" * 40)
            self.assertEqual(
                json.loads((Path(directory) / "report.json").read_text()), report
            )
            self.assertEqual(
                (Path(directory) / "report.md").read_text(), markdown(report)
            )
        evaluation = next(c for c in target.commands if c[:2] == ["nix", "eval"])
        self.assertIn(
            "git+file:///source?dir=flake&rev="
            + "a" * 40
            + "#nixosConfigurations.homelab-04-registry.config.system.build.toplevel",
            evaluation,
        )
        self.assertIn("allow-import-from-derivation", evaluation)
        self.assertIn("false", evaluation)

    def test_dirty_source_cannot_masquerade_as_a_clean_commit(self) -> None:
        target = Target()
        target.source_revision = None
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--json"], runner=target.run), 1)
        report = json.loads(output.getvalue())
        self.assertIn("clean committed Git flake", report["errors"][0])
        self.assertFalse(any(c[0] == "ssh" for c in target.commands))

    def test_unavailable_host_does_not_suppress_other_host_reports(self) -> None:
        target = Target()
        target.error_host = "rupan@10.0.30.13"
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = main(
                ["--target", "homelab-03,homelab-04", "--json"], runner=target.run
            )
        report = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual([h["status"] for h in report["hosts"]], ["error", "complete"])

    def test_malformed_metadata_and_unknown_host_produce_json_error(self) -> None:
        for target_args, runner in (
            (["--target", "unknown", "--json"], Target().run),
            (
                ["--json"],
                lambda *args, **kwargs: subprocess.CompletedProcess([], 0, "null", ""),
            ),
        ):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(target_args, runner=runner), 1)
            self.assertEqual(json.loads(output.getvalue())["status"], "error")

    def test_strict_host_key_policy_precedes_extra_options(self) -> None:
        reporter = Reporter(ssh_opts=["-o", "StrictHostKeyChecking=no"])
        command = reporter.ssh(FLEET_HOSTS["homelab-04"], ["hostname"])
        self.assertLess(
            command.index("StrictHostKeyChecking=yes"),
            command.index("StrictHostKeyChecking=no"),
        )

    def test_snapshot_shells_are_valid_and_presence_check_detects_missing_paths(
        self,
    ) -> None:
        for script in (SNAPSHOT, PRESENCE):
            subprocess.run(["sh", "-n"], input=script, text=True, check=True)
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "nix-store"
            executable.write_text(
                f"#!{sys.executable}\nprint({str(Path(directory) / 'missing')!r})\n"
            )
            executable.chmod(0o700)
            with patch.dict(
                "os.environ", {"PATH": directory + os.pathsep + os.environ["PATH"]}
            ):
                result = subprocess.run(
                    ["sh", "-c", PRESENCE, "sh", CURRENT],
                    capture_output=True,
                    text=True,
                )
            self.assertEqual(result.returncode, 1)
            self.assertIn("Missing closure path", result.stderr)


class ProtectedDeploymentReportTest(unittest.TestCase):
    def test_dry_runs_capture_and_pack_reports_for_the_same_targets(self) -> None:
        root = Path(__file__).resolve().parents[1]
        catalog = json.loads((root / "config/ci/operations.json").read_text())
        operations = {
            "deploy-fleet-dry-run": [],
            "deploy-ci-host-dry-run": ["--target", "homelab-04"],
            "deploy-agent-workspace-host-dry-run": ["--target", "homelab-01"],
        }
        for name, targets in operations.items():
            with (
                self.subTest(operation=name),
                tempfile.TemporaryDirectory() as directory,
            ):
                work = Path(directory)
                executable = work / "just"
                executable.write_text(
                    f"#!{sys.executable}\n"
                    + """import json, pathlib, sys
args = sys.argv[1:]
with pathlib.Path('events.jsonl').open('a') as out:
    out.write(json.dumps(args) + '\\n')
if args[0] == 'deployment-report':
    output = pathlib.Path(args[args.index('--output-dir') + 1])
    output.mkdir(parents=True)
    (output / 'report.json').write_text('{}')
    (output / 'report.md').write_text('# Deployment report\\n')
"""
                )
                executable.chmod(0o700)
                commands = catalog[name]["commands"]
                start = next(
                    i
                    for i, c in enumerate(commands)
                    if c.startswith("just deployment-report")
                )
                with patch.dict(
                    "os.environ", {"PATH": directory + os.pathsep + os.environ["PATH"]}
                ):
                    subprocess.run(
                        ["sh", "-e", "-c", "\n".join(commands[start:])],
                        cwd=work,
                        check=True,
                    )
                events = [
                    json.loads(line)
                    for line in (work / "events.jsonl").read_text().splitlines()
                ]
                self.assertEqual(events[0][:-2], ["deployment-report", *targets])
                self.assertEqual(events[1], ["deploy-fleet", "--dry-run", *targets])
                self.assertEqual(events[2][:-2], ["deployment-report", *targets])
                receipt = json.loads(
                    pack(work, catalog[name]["outputs"], "a" * 40, name)
                )
                self.assertEqual(len(receipt["files"]), 4)
                self.assertTrue(
                    any(p.endswith("/before/report.json") for p in receipt["files"])
                )
                self.assertTrue(
                    any(p.endswith("/after/report.md") for p in receipt["files"])
                )
                self.assertFalse(catalog[name]["mutation"])


if __name__ == "__main__":
    unittest.main()
