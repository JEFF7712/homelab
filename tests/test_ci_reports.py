import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from scripts.ci import run, timings_report
from scripts.ci.contract import recover_report
from scripts.ci.tests import ReportResult, write_report

ROOT = Path(__file__).resolve().parents[1]


class ReportsTest(unittest.TestCase):
    def test_cgroup_counters_are_optional_and_ignore_malformed_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.assertEqual(run.cgroup_resources(directory), {})
            (directory / "memory.events").write_text("oom 2\noom_kill 1\nbad nope\n")
            (directory / "cpu.stat").write_text("throttled_usec 2300000\n")
            (directory / "memory.peak").write_text("8388608\n")
            self.assertEqual(
                run.cgroup_resources(directory),
                {
                    "memory_oom": 2,
                    "memory_oom_kill": 1,
                    "cpu_throttled_usec": 2300000,
                    "memory_peak_bytes": 8388608,
                },
            )

    def test_timing_records_counter_deltas_and_distinguishes_pipeline_age(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.dict(
                    os.environ, CI_TIMING_DIR=temporary, CI_PIPELINE_CREATED="100"
                ),
                patch("scripts.ci.run.time.time", return_value=125),
                patch("scripts.ci.run.subprocess.call", return_value=7),
                patch(
                    "scripts.ci.run.cgroup_resources",
                    side_effect=[
                        {"memory_oom_kill": 2, "cpu_throttled_usec": 1000000},
                        {
                            "memory_oom_kill": 3,
                            "cpu_throttled_usec": 4000000,
                            "memory_peak_bytes": 8388608,
                        },
                    ],
                ),
            ):
                self.assertEqual(run.main(["gate", "--", "test-command"]), 7)
            report = json.loads((Path(temporary) / "timings.jsonl").read_text())
            self.assertEqual(report["pipeline_age_at_command_start_seconds"], 25)
            self.assertNotIn("queue_seconds", report)
            self.assertEqual(report["cgroup"]["memory_oom_kill"], 1)
            self.assertEqual(report["cgroup"]["cpu_throttled_usec"], 3000000)
            self.assertEqual(report["cgroup"]["memory_peak_bytes"], 8388608)

    def test_summary_reports_earliest_command_age_and_container_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            records = [
                {
                    "label": "gate",
                    "elapsed_seconds": 8,
                    "exit_code": 0,
                    "pipeline_age_at_command_start_seconds": 20,
                    "cgroup": {
                        "memory_oom_kill": 0,
                        "memory_peak_bytes": 8388608,
                        "cpu_throttled_usec": 3000000,
                    },
                },
                {
                    "label": "check-flake",
                    "elapsed_seconds": 7,
                    "exit_code": 0,
                    "pipeline_age_at_command_start_seconds": 23,
                },
            ]
            (Path(temporary) / "timings.jsonl").write_text(
                "\n".join(json.dumps(record) for record in records)
            )
            output = io.StringIO()
            with (
                patch.dict(os.environ, CI_TIMING_DIR=temporary),
                patch("sys.stdout", output),
            ):
                self.assertEqual(timings_report.main(), 0)
            self.assertIn("20.0s after creation", output.getvalue())
            self.assertIn("cgroup_peak=8MiB", output.getvalue())
            self.assertIn("cgroup_oom_kills=0", output.getvalue())
            self.assertIn("cgroup_cpu_throttled=3.0s", output.getvalue())
            self.assertNotIn("[WAIT / QUEUE]", output.getvalue())

    def test_killed_child_reports_signal_and_normalized_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.ci.run",
                    "killed",
                    "--",
                    sys.executable,
                    "-c",
                    "import os, signal; os.kill(os.getpid(), signal.SIGKILL)",
                ],
                cwd=ROOT,
                env=dict(os.environ, CI_TIMING_DIR=temporary),
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 137)
            report = json.loads((Path(temporary) / "timings.jsonl").read_text())
            self.assertEqual(report["exit_code"], 137)
            self.assertEqual(report["signal"], 9)

    def test_partition_excludes_integration_module_and_preserves_module_fixtures(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "test_unit.py").write_text(
                "import unittest\n"
                "def setUpModule():\n    raise ValueError('unit fixture')\n"
                "class Example(unittest.TestCase):\n    def test_unused(self):\n        pass\n"
            )
            (directory / "test_integration.py").write_text(
                "import unittest\n"
                "class Integration(unittest.TestCase):\n"
                "    def test_integration(self):\n        self.fail('integration failure')\n"
            )
            for flags, expected_error, expected_failure in (
                (["--exclude-module", "test_integration"], "1", "0"),
                (["--pattern", "test_integration.py"], "0", "1"),
            ):
                output = directory / "report.xml"
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "scripts.ci.tests",
                        "--start",
                        temporary,
                        "--output",
                        str(output),
                        *flags,
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 1)
                report = ET.parse(output).getroot()
                self.assertEqual(report.get("tests"), "1")
                self.assertEqual(report.get("errors"), expected_error)
                self.assertEqual(report.get("failures"), expected_failure)

    def test_outcomes_and_fixture_errors_have_distinct_cases(self) -> None:
        class Outcomes(unittest.TestCase):
            def test_pass(self):
                pass

            def test_failure(self):
                self.fail("failure detail" + chr(0))

            def test_error(self):
                raise ValueError("error detail")

            @unittest.skip("skip detail")
            def test_skip(self):
                pass

            @unittest.expectedFailure
            def test_expected(self):
                self.fail()

            @unittest.expectedFailure
            def test_unexpected(self):
                pass

            def test_subtest(self):
                with self.subTest(value=1):
                    self.fail("subtest detail")

        class FixtureOne(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise ValueError("fixture one")

            def test_unused(self):
                pass

        class FixtureTwo(FixtureOne):
            @classmethod
            def setUpClass(cls):
                raise ValueError("fixture two")

        suite = unittest.TestSuite(
            unittest.defaultTestLoader.loadTestsFromTestCase(cls)
            for cls in (Outcomes, FixtureOne, FixtureTwo)
        )
        result = unittest.TextTestRunner(
            stream=io.StringIO(), resultclass=ReportResult
        ).run(suite)
        self.assertIsInstance(result, ReportResult)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "nested/tests.xml"
            write_report(result, output)
            report = ET.parse(output).getroot()
        self.assertEqual(report.attrib["tests"], "9")
        self.assertEqual(report.attrib["failures"], "3")
        self.assertEqual(report.attrib["errors"], "3")
        self.assertEqual(report.attrib["skipped"], "2")
        names = [case.attrib["name"] for case in report]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("subtest detail", ET.tostring(report).decode())

    def test_runner_returns_failure_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "test_failure.py").write_text(
                "import unittest\nclass Example(unittest.TestCase):\n"
                "    def test_fail(self):\n        self.fail('detail')\n"
            )
            output = directory / "report.xml"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.ci.tests",
                    "--start",
                    temporary,
                    "--output",
                    str(output),
                    "--log-report",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(ET.parse(output).getroot().attrib["failures"], "1")
            self.assertEqual(recover_report(result.stdout), output.read_bytes())

    def test_timing_preserves_exit_code_without_logging_command_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = dict(os.environ, CI_TIMING_DIR=temporary)
            for code in (0, 7):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "scripts.ci.run",
                        "sample",
                        "--",
                        sys.executable,
                        "-c",
                        f"raise SystemExit({code})",
                    ],
                    cwd=ROOT,
                    env=env,
                    check=False,
                )
                self.assertEqual(result.returncode, code)
            reports = [
                json.loads(line)
                for line in (Path(temporary) / "timings.jsonl").read_text().splitlines()
            ]
            self.assertEqual([item["exit_code"] for item in reports], [0, 7])
            for item in reports:
                self.assertGreater(item["elapsed_seconds"], 0)
                self.assertNotIn("command", item)

    def test_missing_command_reports_its_actual_exit_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = dict(os.environ, CI_TIMING_DIR=temporary)
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.ci.run",
                    "missing",
                    "--",
                    str(Path(temporary) / "missing-command"),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 127)
            report = json.loads((Path(temporary) / "timings.jsonl").read_text())
            self.assertEqual(report["exit_code"], result.returncode)
