import argparse
import base64
import re
import time
import traceback
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.ci.contract import REPORT_BEGIN, REPORT_END


class ReportResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.cases: list[ET.Element] = []
        self.current: ET.Element | None = None
        self.started = 0.0

    def startTest(self, test) -> None:
        super().startTest(test)
        identity = test.id()
        classname, _, name = identity.rpartition(".")
        self.current = ET.Element("testcase", classname=classname, name=name)
        self.cases.append(self.current)
        self.started = time.monotonic()

    def stopTest(self, test) -> None:
        if self.current is not None:
            self.current.set("time", str(time.monotonic() - self.started))
        self.current = None
        super().stopTest(test)

    def record(self, kind: str, text: str, test) -> None:
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "?", text)
        case = self.current
        if case is None:
            classname, _, name = test.id().rpartition(".")
            case = ET.Element("testcase", classname=classname, name=name)
            self.cases.append(case)
        ET.SubElement(
            case, kind, message=text.splitlines()[-1] if text else kind
        ).text = text

    def addFailure(self, test, err) -> None:
        super().addFailure(test, err)
        self.record("failure", "".join(traceback.format_exception(*err)), test)

    def addError(self, test, err) -> None:
        super().addError(test, err)
        self.record("error", "".join(traceback.format_exception(*err)), test)

    def addSkip(self, test, reason) -> None:
        super().addSkip(test, reason)
        self.record("skipped", reason, test)

    def addExpectedFailure(self, test, err) -> None:
        super().addExpectedFailure(test, err)
        self.record("skipped", "expected failure", test)

    def addUnexpectedSuccess(self, test) -> None:
        super().addUnexpectedSuccess(test)
        self.record("failure", "unexpected success", test)

    def addSubTest(self, test, subtest, err) -> None:
        super().addSubTest(test, subtest, err)
        if err is not None:
            kind = "failure" if isinstance(err[1], test.failureException) else "error"
            self.record(kind, "".join(traceback.format_exception(*err)), test)


def write_report(result: ReportResult, output: Path) -> None:
    suite = ET.Element(
        "testsuite",
        name="repository",
        tests=str(len(result.cases)),
        failures=str(sum(case.find("failure") is not None for case in result.cases)),
        errors=str(sum(case.find("error") is not None for case in result.cases)),
        skipped=str(sum(case.find("skipped") is not None for case in result.cases)),
        time=str(sum(float(case.get("time", "0")) for case in result.cases)),
    )
    suite.extend(result.cases)
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suite).write(output, encoding="utf-8", xml_declaration=True)


def exclude_module(suite: unittest.TestSuite, module: str) -> unittest.TestSuite:
    selected = unittest.TestSuite()
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            selected.addTest(exclude_module(test, module))
        elif type(test).__module__ != module:
            selected.addTest(test)
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", default="tests")
    parser.add_argument("--pattern", default="test*.py")
    parser.add_argument("--log-report", action="store_true")
    parser.add_argument("--exclude-module")
    args = parser.parse_args(argv)
    suite = unittest.defaultTestLoader.discover(args.start, pattern=args.pattern)
    if args.exclude_module:
        suite = exclude_module(suite, args.exclude_module)
    result = unittest.TextTestRunner(verbosity=2, resultclass=ReportResult).run(suite)
    assert isinstance(result, ReportResult)
    write_report(result, args.output)
    if args.log_report and not result.wasSuccessful():
        print(REPORT_BEGIN)
        print(base64.encodebytes(args.output.read_bytes()).decode().rstrip())
        print(REPORT_END, flush=True)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
