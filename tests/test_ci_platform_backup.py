import subprocess
import unittest
from unittest.mock import patch

from scripts.ci.platform_backup import stop_active_services


class PlatformBackupServiceTests(unittest.TestCase):
    @patch("scripts.ci.platform_backup.subprocess.run")
    def test_outage_service_is_not_scheduled_for_restart(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 3, "inactive\n"),
            subprocess.CompletedProcess([], 0, "active\n"),
            subprocess.CompletedProcess([], 0),
        ]
        stopped = []
        stop_active_services(["forgejo", "garage"], stopped)
        self.assertEqual(stopped, ["garage"])
        self.assertEqual(
            run.call_args_list[-1].args[0], ["systemctl", "stop", "garage"]
        )

    @patch("scripts.ci.platform_backup.subprocess.run")
    def test_transition_fails_before_backup_or_restart(self, run):
        run.return_value = subprocess.CompletedProcess([], 3, "deactivating\n")
        stopped = []
        with self.assertRaisesRegex(RuntimeError, "deactivating"):
            stop_active_services(["forgejo"], stopped)
        self.assertEqual(stopped, [])
        self.assertEqual(run.call_count, 1)

    @patch("scripts.ci.platform_backup.subprocess.run")
    def test_stop_failure_retains_cleanup_obligation(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, "active\n"),
            subprocess.CalledProcessError(1, ["systemctl", "stop", "forgejo"]),
        ]
        stopped = []
        with self.assertRaises(subprocess.CalledProcessError):
            stop_active_services(["forgejo"], stopped)
        self.assertEqual(stopped, ["forgejo"])
