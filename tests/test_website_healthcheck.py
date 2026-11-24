import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "gitops/automation/website-monitor/healthcheck.sh"
)

# Fake kubectl: keeps the state ConfigMap's data in $STATE as JSON.
KUBECTL = """#!/bin/sh
case "$*" in
  *"get configmap"*"-o json"*) printf '{"data": %s}' "$(cat "$STATE")" ;;
  *"get configmap"*) exit 0 ;;
  *"patch configmap"*)
    for patch; do :; done
    jq -s '.[0] * .[1].data' "$STATE" - <<EOF > "$STATE.new"
$patch
EOF
    mv "$STATE.new" "$STATE" ;;
esac
"""

# Fake curl: the probe answers with $CODE; publishes are recorded.
CURL = """#!/bin/sh
for arg; do
  case "$arg" in
    http://ntfy*) printf '%s\\n' "$title" >> "$NOTIFIED"; exit 0 ;;
    X-Title:*) title="${arg#X-Title: }" ;;
  esac
done
[ "$CODE" = 000 ] && { printf 000; exit 28; }
printf '%s' "$CODE"
"""


class HealthcheckTests(unittest.TestCase):
    """LAN-gateway DNS made in-cluster probes time out every other run
    (2026-11-23), sending ~290 urgent down/recovered pushes in 12 hours."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        for name, body in (("kubectl", KUBECTL), ("curl", CURL)):
            path = bin_dir / name
            path.write_text(body)
            path.chmod(0o755)
        config = root / "config"
        config.mkdir()
        (config / "ntfy-topic").write_text("alerts")
        (config / "sites.txt").write_text("site|https://site.example\n")
        self.state = root / "state.json"
        self.state.write_text("{}")
        self.notified = root / "notified"
        self.notified.touch()
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CONFIG_DIR": str(config),
            "STATE": str(self.state),
            "NOTIFIED": str(self.notified),
            "NTFY_TOKEN": "test",
        }

    def probe(self, code: str) -> list[str]:
        before = self.notified.read_text()
        subprocess.run(
            ["sh", str(SCRIPT)],
            env={**self.env, "CODE": code},
            check=True,
            capture_output=True,
        )
        return self.notified.read_text()[len(before) :].splitlines()

    def status(self) -> str:
        return json.loads(self.state.read_text())["site"]

    def test_single_failure_is_silent(self) -> None:
        self.assertEqual(self.probe("200"), [])
        self.assertEqual(self.probe("000"), [])
        self.assertEqual(self.status(), "failing:000")
        self.assertEqual(self.probe("200"), [])
        self.assertEqual(self.status(), "up:200")

    def test_second_consecutive_failure_pages_once_and_recovers(self) -> None:
        self.probe("200")
        self.probe("503")
        self.assertEqual(self.probe("503"), ["Website down: site"])
        self.assertEqual(self.probe("503"), [])
        self.assertEqual(self.status(), "down:503")
        self.assertEqual(self.probe("200"), ["Website recovered: site"])
        self.assertEqual(self.probe("200"), [])

    def test_latched_down_state_recovers(self) -> None:
        self.state.write_text('{"site": "down:000"}')
        self.assertEqual(self.probe("200"), ["Website recovered: site"])


if __name__ == "__main__":
    unittest.main()
