import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "gitops/automation/cluster-sentinel/cluster-sentinel.sh"
)


def check_change_function() -> str:
    match = re.search(
        r"^check_change\(\) \{\n.*?^\}\n", SCRIPT.read_text(), re.M | re.S
    )
    assert match, "cluster-sentinel has no check_change function"
    return match.group(0)


class CheckChangeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "state"
        self.state.mkdir()
        self.notifications = Path(self.temp.name) / "notifications"
        self.notifications.touch()

    def run_check(self, status: str, message: str) -> list[str]:
        before = self.notifications.read_text()
        harness = f"""
set -eu
state_dir={self.state}
state_get() {{ cat "$state_dir/$1" 2>/dev/null || true; }}
state_set() {{ printf '%s' "$2" > "$state_dir/$1"; }}
notify() {{ printf '%s|%s\\n' "$1" "$2" >> {self.notifications}; }}
{check_change_function()}
check_change cronjobs "$1" "CronJob failure" "CronJobs recovered" "$2"
"""
        subprocess.run(["sh", "-c", harness, "sh", status, message], check=True)
        return self.notifications.read_text()[len(before) :].splitlines()

    def test_new_failure_alerts_while_check_is_already_bad(self) -> None:
        first = "immich/backup (backup-1): BackoffLimitExceeded"
        self.assertEqual(self.run_check("bad", first), ["CronJob failure|high"])
        self.assertEqual(
            self.run_check("bad", "immich/backup (backup-2): BackoffLimitExceeded"),
            [],
        )
        both = first + "\nmedia/sync (sync-9): DeadlineExceeded"
        self.assertEqual(self.run_check("bad", both), ["CronJob failure|high"])
        self.assertEqual(self.run_check("bad", both), [])

    def test_recovery_alerts_once_and_clears_items(self) -> None:
        self.run_check("bad", "a/b: Failed")
        self.assertEqual(self.run_check("ok", "fine"), ["CronJobs recovered|default"])
        self.assertEqual(self.run_check("ok", "fine"), [])
        self.assertEqual(self.run_check("bad", "a/b: Failed"), ["CronJob failure|high"])

    def test_previously_latched_state_without_items_alerts_once(self) -> None:
        (self.state / "cronjobs").write_text("bad")
        self.assertEqual(self.run_check("bad", "a/b: Failed"), ["CronJob failure|high"])
        self.assertEqual(self.run_check("bad", "a/b: Failed"), [])


class StateConfigMapTests(unittest.TestCase):
    def test_flux_only_creates_runtime_state(self) -> None:
        # Regression (2026-11-19): a Flux re-apply dropped every key the
        # sentinel had patched into cluster-sentinel-state.
        state = SCRIPT.parents[1] / "state.yaml"
        maps = [doc for doc in yaml.safe_load_all(state.read_text()) if doc]
        self.assertTrue(maps)
        for document in maps:
            with self.subTest(document["metadata"]["name"]):
                self.assertEqual(
                    document["metadata"]["annotations"][
                        "kustomize.toolkit.fluxcd.io/ssa"
                    ],
                    "IfNotPresent",
                )


if __name__ == "__main__":
    unittest.main()
