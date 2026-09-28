import base64
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPORT_BEGIN = "CI_JUNIT_BEGIN"
REPORT_END = "CI_JUNIT_END"


def recover_report(log: str) -> bytes:
    _, begin, remainder = log.partition(REPORT_BEGIN + "\n")
    payload, end, _ = remainder.partition("\n" + REPORT_END)
    if not begin or not end:
        raise ValueError("sandbox did not publish a test report")
    data = base64.b64decode("".join(payload.splitlines()), validate=True)
    validate_report(data)
    return data


def validate_report(data: bytes) -> None:
    suite = ET.fromstring(data)
    if suite.tag != "testsuite" or int(suite.get("tests", "0")) <= 0:
        raise ValueError("sandbox report contains no test cases")


def infrastructure_report(output: Path, message: str) -> None:
    suite = ET.Element("testsuite", name="repository", tests="1", errors="1")
    case = ET.SubElement(suite, "testcase", classname="ci.sandbox", name="build")
    ET.SubElement(case, "error", message=message).text = message
    ET.ElementTree(suite).write(output, encoding="utf-8", xml_declaration=True)


def build_report(flake: str, output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    target = f"{flake}#checks.x86_64-linux.repository-contract"
    try:
        result = subprocess.run(
            ["nix", "build", "--no-link", "--json", "--no-write-lock-file", target],
            stdout=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            built = json.loads(result.stdout)
            report = Path(built[0]["outputs"]["out"]) / "tests.xml"
            validate_report(report.read_bytes())
            shutil.copyfile(report, output)
            return 0
        log = subprocess.run(
            ["nix", "log", "--no-write-lock-file", target],
            capture_output=True,
            text=True,
            check=False,
        )
        output.write_bytes(recover_report(log.stdout))
        return result.returncode
    except (OSError, ValueError, KeyError, IndexError, ET.ParseError) as error:
        message = f"cannot collect sandbox test results: {error}"
        print(message, file=sys.stderr)
        infrastructure_report(output, message)
        return 1
