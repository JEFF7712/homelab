from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.checks.flake_jobs import (
    Check,
    build_jobs,
    failure_report,
    nix_build,
    run,
)

ROOT = Path(__file__).resolve().parents[1]


def line(**job: str) -> str:
    return json.dumps(job)


class FlakeJobsTest(unittest.TestCase):
    def test_builds_checks_and_only_evaluates_hosts(self) -> None:
        built: list[Check] = []
        count, failures = run(
            [
                line(attr="checks.topology", drvPath="/nix/store/b-topology.drv"),
                line(attr="hosts.nas-01", drvPath="/nix/store/a-nas.drv"),
                "",
                line(attr="checks.opnsense-types", drvPath="/nix/store/c-types.drv"),
            ],
            lambda target: built.append(target) is None,
        )
        self.assertEqual(failures, [])
        self.assertEqual(count, 2)
        self.assertEqual(
            built,
            [
                Check("checks.topology", "/nix/store/b-topology.drv^*"),
                Check("checks.opnsense-types", "/nix/store/c-types.drv^*"),
            ],
        )

    def test_evaluation_and_build_failures_are_reported(self) -> None:
        _, failures = run(
            [
                line(attr="hosts.homelab-04", error="infinite recursion"),
                line(attr="checks.topology", drvPath="/nix/store/b-topology.drv"),
            ],
            lambda target: False,
        )
        self.assertEqual(
            failures,
            [
                "hosts.homelab-04: infinite recursion",
                "/nix/store/b-topology.drv^*: build failed",
            ],
        )

    def test_checks_build_concurrently_up_to_the_job_count(self) -> None:
        # Both builds must be in flight at once for the barrier to release;
        # a single builder would time out on the first build.
        barrier = threading.Barrier(2, timeout=5)
        lock = threading.Lock()
        active = 0
        peak = 0

        def build(target: Check) -> bool:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                if target.attr != "checks.fail":
                    barrier.wait()
                return target.attr != "checks.fail"
            finally:
                with lock:
                    active -= 1

        count, failures = run(
            [
                line(attr="checks.repository-contract", drvPath="/nix/store/a.drv"),
                line(attr="checks.packet-flow", drvPath="/nix/store/b.drv"),
                line(attr="checks.fail", drvPath="/nix/store/c.drv"),
            ],
            build,
            jobs=2,
        )
        self.assertEqual(count, 3)
        self.assertEqual(failures, ["/nix/store/c.drv^*: build failed"])
        self.assertEqual(peak, 2)

    def test_vm_tests_build_one_at_a_time_beside_other_checks(self) -> None:
        # The unit suite must still start while a VM test is building, but a
        # second VM test waits: two TCG guests in two CPUs miss their boot
        # timeouts (pipeline #12524).
        lock = threading.Lock()
        active = {"vm": 0, "other": 0}
        peaks = {"vm": 0, "overlap": 0}
        first_vm_running = threading.Event()
        contract_done = threading.Event()

        def build(target: Check) -> bool:
            kind = "vm" if target.vm else "other"
            with lock:
                active[kind] += 1
                peaks["vm"] = max(peaks["vm"], active["vm"])
                if active["vm"] and active["other"]:
                    peaks["overlap"] = 1
            try:
                if target.attr == "checks.libvirt":
                    first_vm_running.set()
                    contract_done.wait(5)
                elif target.attr == "checks.repository-contract":
                    first_vm_running.wait(5)
                    contract_done.set()
                return True
            finally:
                with lock:
                    active[kind] -= 1

        count, failures = run(
            [
                line(
                    attr="checks.libvirt",
                    drvPath="/nix/store/a-vm-test-run-libvirt.drv",
                ),
                line(
                    attr="checks.niks3",
                    drvPath="/nix/store/b-vm-test-run-niks3.drv",
                ),
                line(
                    attr="checks.repository-contract",
                    drvPath="/nix/store/c-repository-contract.drv",
                ),
            ],
            build,
            jobs=2,
            vm_jobs=1,
        )
        self.assertEqual((count, failures), (3, []))
        self.assertEqual(peaks, {"vm": 1, "overlap": 1})

    def test_build_jobs_follow_the_cpu_quota_with_an_override(self) -> None:
        self.assertEqual(build_jobs({}, cpus=2.0), (2, 1))
        self.assertEqual(build_jobs({}, cpus=1.0), (2, 1))
        self.assertEqual(build_jobs({}, cpus=4.0), (2, 1))
        self.assertEqual(build_jobs({}, cpus=8.0), (4, 2))
        self.assertEqual(build_jobs({"CI_FLAKE_BUILD_JOBS": "1"}, cpus=8.0), (1, 2))
        self.assertEqual(build_jobs({"CI_FLAKE_BUILD_JOBS": "x"}, cpus=6.0), (3, 1))

    def test_no_checks_is_a_failure(self) -> None:
        _, failures = run(
            [line(attr="hosts.nas-01", drvPath="/nix/store/a-nas.drv")],
            lambda target: True,
        )
        self.assertEqual(failures, ["no checks evaluated"])

    def test_build_timing_records_names_and_preserves_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            nix = directory / "nix"
            nix.write_text(
                f"#!{sys.executable}\n"
                "import sys\n"
                "assert sys.argv[1:3] == ['build', '--no-link']\n"
                "sys.exit(5 if 'failed' in sys.argv[3] else 0)\n"
            )
            nix.chmod(0o755)
            with patch.dict(
                os.environ,
                PATH=f"{directory}:{os.environ['PATH']}",
                CI_TIMING_DIR=str(directory),
            ):
                count, failures = run(
                    [
                        line(
                            attr="checks.repository-contract",
                            drvPath="/nix/store/ok.drv",
                        ),
                        line(attr="checks.topology", drvPath="/nix/store/failed.drv"),
                    ],
                    nix_build,
                )
            self.assertEqual(count, 2)
            self.assertEqual(failures, ["/nix/store/failed.drv^*: build failed"])
            records = [
                json.loads(entry)
                for entry in (directory / "timings.jsonl").read_text().splitlines()
            ]
            self.assertEqual(
                [(entry["label"], entry["exit_code"]) for entry in records],
                [("flake-repository-contract", 0), ("flake-topology", 5)],
            )
            self.assertTrue(all(entry["elapsed_seconds"] > 0 for entry in records))

    UNIT_LOG = "\n".join(
        [
            "running 3 tests",
            "=" * 70,  # printed by a test, not a failure
            "test_a (test_m.Case.test_a) ... ok",
            "test_b (test_m.Case.test_b) ... FAIL",
            "test_c (test_n.Case.test_c) ... ERROR",
            "=" * 70,
            "FAIL: test_b (test_m.Case.test_b)",
            "-" * 70,
            "Traceback (most recent call last):",
            "AssertionError: 1 != 2",
            "=" * 70,
            "ERROR: test_c (test_n.Case.test_c)",
            "FileNotFoundError: receipt",
            "-" * 70,
            "Ran 3 tests in 1.0s with 4 workers",
            "",
            "FAILED",
            "  test_m.Case.test_b",
        ]
    )

    def test_failure_report_keeps_every_traceback_of_a_unit_suite(self) -> None:
        report = failure_report(self.UNIT_LOG).splitlines()
        self.assertEqual(report[1], "FAIL: test_b (test_m.Case.test_b)")
        self.assertIn("AssertionError: 1 != 2", report)
        self.assertIn("FileNotFoundError: receipt", report)
        self.assertNotIn("test_a (test_m.Case.test_a) ... ok", report)
        self.assertFalse([line for line in report if line.startswith("Ran ")])

    def test_failure_report_tails_other_logs_and_bounds_its_size(self) -> None:
        other = "\n".join(f"line {n}" for n in range(500))
        self.assertEqual(failure_report(other).splitlines()[0], "line 420")
        huge = self.UNIT_LOG.replace(
            "AssertionError: 1 != 2", "\n".join(["frame"] * 1000)
        )
        report = failure_report(huge).splitlines()
        self.assertEqual(len(report), 401)
        self.assertRegex(report[-1], r"^\.\.\. \d+ more lines$")

    def test_failed_build_prints_the_tracebacks_from_its_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "build.log").write_text(self.UNIT_LOG)
            nix = directory / "nix"
            nix.write_text(
                f"#!{sys.executable}\n"
                "import sys\n"
                "if sys.argv[1] == 'log':\n"
                "    assert sys.argv[2] == '/nix/store/failed.drv', sys.argv\n"
                f"    print(open({str(directory / 'build.log')!r}).read())\n"
                "    sys.exit(0)\n"
                "sys.exit(1)\n"
            )
            nix.chmod(0o755)
            stderr = io.StringIO()
            with (
                patch.dict(
                    os.environ,
                    PATH=f"{directory}:{os.environ['PATH']}",
                    CI_TIMING_DIR=str(directory),
                ),
                patch("sys.stderr", stderr),
            ):
                ok = nix_build(
                    Check("checks.repository-contract", "/nix/store/failed.drv^*")
                )
        self.assertFalse(ok)
        output = stderr.getvalue()
        self.assertIn("===== flake-repository-contract failure =====", output)
        self.assertIn("ERROR: test_c (test_n.Case.test_c)", output)
        self.assertIn("FileNotFoundError: receipt", output)


@unittest.skipUnless(shutil.which("nix"), "requires Nix source evaluation")
class FlakeSourceTest(unittest.TestCase):
    def test_validation_matches_cache_source_despite_metadata_and_artifacts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            checks = repo / "scripts/checks"
            checks.mkdir(parents=True)
            shutil.copy(ROOT / "scripts/checks/flake.sh", checks)
            # Only source evaluation is needed; never build in this fixture.
            (checks / "flake_jobs.py").write_text(
                "import sys\nprint(sys.stdin.read().strip())\n"
            )
            (repo / "flake").mkdir()
            (repo / "flake/flake.nix").write_text(
                "{ outputs = { self }: { checks.x86_64-linux.fixture = "
                'builtins.derivation { name = "source-identity"; '
                'system = "x86_64-linux"; builder = "/bin/sh"; '
                "source = ../.; }; }; }\n"
            )
            (repo / ".gitignore").write_text("artifacts/\nbin/\n")
            env = dict(
                os.environ,
                NIX_CONFIG="experimental-features = nix-command flakes\n",
                GIT_AUTHOR_NAME="Fixture",
                GIT_AUTHOR_EMAIL="fixture@example.test",
                GIT_COMMITTER_NAME="Fixture",
                GIT_COMMITTER_EMAIL="fixture@example.test",
            )

            def command(*args: str) -> str:
                return subprocess.run(
                    args, cwd=repo, env=env, check=True, capture_output=True, text=True
                ).stdout.strip()

            command("git", "init", "-q")
            command("git", "add", ".")
            command("git", "-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture")
            nix = shutil.which("nix")
            assert nix is not None
            expected = command(
                nix,
                "eval",
                "--raw",
                "--offline",
                "./flake#checks.x86_64-linux.fixture.drvPath",
            )
            binaries = repo / "bin"
            binaries.mkdir()
            evaluator = binaries / "nix-eval-jobs"
            evaluator.write_text(
                f"#!{sys.executable}\n"
                "import subprocess, sys\n"
                "ref = sys.argv[sys.argv.index('--flake') + 1]\n"
                f"subprocess.run([{nix!r}, 'eval', '--raw', '--offline', "
                "ref + 'checks.x86_64-linux.fixture.drvPath'], check=True)\n"
            )
            evaluator.chmod(0o755)
            env["PATH"] = f"{binaries}:{env['PATH']}"
            self.assertEqual(command("bash", "scripts/checks/flake.sh"), expected)
            (repo / "artifacts").mkdir()
            artifact = repo / "artifacts/timings.jsonl"
            artifact.write_text("ignored timing report\n")
            command(
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "commit",
                "--allow-empty",
                "-qm",
                "metadata only",
            )
            self.assertEqual(command("bash", "scripts/checks/flake.sh"), expected)
            # A real source change must still invalidate the result.
            (repo / "scripts/checks/flake_jobs.py").write_text(
                "import sys\nprint(sys.stdin.read().strip(), flush=True)\n"
            )
            self.assertNotEqual(command("bash", "scripts/checks/flake.sh"), expected)


if __name__ == "__main__":
    unittest.main()
