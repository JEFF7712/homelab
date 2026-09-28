import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.ci.contract import recover_report
from scripts.ci.tests import ReportResult, write_report

ROOT = Path(__file__).resolve().parents[1]


class ReportsTest(unittest.TestCase):
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
