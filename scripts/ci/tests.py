import argparse
import base64
import io
import multiprocessing
import os
import re
import sys
import time
import traceback
import unittest
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed
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


def write_report(cases: Iterable[ET.Element], output: Path) -> None:
    cases = list(cases)
    suite = ET.Element(
        "testsuite",
        name="repository",
        tests=str(len(cases)),
        failures=str(sum(case.find("failure") is not None for case in cases)),
        errors=str(sum(case.find("error") is not None for case in cases)),
        skipped=str(sum(case.find("skipped") is not None for case in cases)),
        time=str(sum(float(case.get("time", "0")) for case in cases)),
    )
    suite.extend(cases)
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


# Module suites the forked workers run, by index. Workers inherit them from
# the parent, so each test module is imported once.
_PARTS: list[unittest.TestCase | unittest.TestSuite] = []


def _run_part(index: int) -> tuple[int, str, str, list[bytes], int, bool]:
    stream = io.StringIO()
    result = ReportResult(unittest.runner._WritelnDecorator(stream), True, 2)
    result.startTestRun()
    try:
        _PARTS[index](result)
    finally:
        result.stopTestRun()
    log = stream.getvalue()
    # Tracebacks go to their own buffer so they print together at the end,
    # where a truncated build log tail still shows them.
    errors = io.StringIO()
    result.stream = unittest.runner._WritelnDecorator(errors)
    result.printErrors()
    cases = [ET.tostring(case) for case in result.cases]
    passed = result.wasSuccessful()
    return index, log, errors.getvalue(), cases, result.testsRun, passed


def run_parallel(suite: unittest.TestSuite, jobs: int) -> tuple[list[ET.Element], bool]:
    """Run each top-level (per-module) suite in a pool of forked workers.

    Modules are the unit of distribution, so module and class fixtures run
    as they do serially. Each module's log prints when it finishes; the
    report keeps discovery order.
    """
    _PARTS[:] = [part for part in suite if part.countTestCases()]
    started = time.perf_counter()
    reported: dict[int, list[bytes]] = {}
    errors: dict[int, str] = {}
    ran = 0
    success = True
    # Fork so workers reuse the parent's imported modules; flush first so
    # buffered parent output is not repeated by every worker.
    sys.stdout.flush()
    sys.stderr.flush()
    context = multiprocessing.get_context("fork")
    with ProcessPoolExecutor(max_workers=jobs, mp_context=context) as pool:
        futures = [pool.submit(_run_part, index) for index in range(len(_PARTS))]
        for future in as_completed(futures):
            index, log, failed, cases, count, passed = future.result()
            sys.stderr.write(log)
            sys.stderr.flush()
            reported[index] = cases
            errors[index] = failed
            ran += count
            success = success and passed
    elapsed = time.perf_counter() - started
    sys.stderr.write(
        "".join(errors[index] for index in sorted(errors))
        + f"{'-' * 70}\nRan {ran} tests in {elapsed:.3f}s with {jobs} workers\n\n"
        f"{'OK' if success else 'FAILED'}\n"
    )
    cases = [
        ET.fromstring(case) for index in sorted(reported) for case in reported[index]
    ]
    return cases, success


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", default="tests")
    parser.add_argument("--pattern", default="test*.py")
    parser.add_argument("--log-report", action="store_true")
    parser.add_argument("--exclude-module")
    parser.add_argument(
        "--jobs", type=int, default=1, help="worker processes; 0 uses every CPU"
    )
    args = parser.parse_args(argv)
    suite = unittest.defaultTestLoader.discover(args.start, pattern=args.pattern)
    if args.exclude_module:
        suite = exclude_module(suite, args.exclude_module)
    jobs = args.jobs if args.jobs > 0 else len(os.sched_getaffinity(0))
    if jobs > 1:
        cases, success = run_parallel(suite, jobs)
    else:
        result = unittest.TextTestRunner(verbosity=2, resultclass=ReportResult).run(
            suite
        )
        assert isinstance(result, ReportResult)
        cases, success = result.cases, result.wasSuccessful()
    write_report(cases, args.output)
    if args.log_report and not success:
        print(REPORT_BEGIN)
        print(base64.encodebytes(args.output.read_bytes()).decode().rstrip())
        print(REPORT_END, flush=True)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
