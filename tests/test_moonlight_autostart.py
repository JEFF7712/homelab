import importlib.util
import pathlib
import unittest
from unittest.mock import MagicMock, call, patch

spec = importlib.util.spec_from_file_location(
    "moonlight_autostart",
    pathlib.Path(__file__).resolve().parents[1]
    / "flake/modules/moonlight-autostart.py",
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SupervisorTests(unittest.TestCase):
    @patch.object(module.time, "sleep")
    @patch.object(module.subprocess, "run")
    @patch.object(module.subprocess, "Popen")
    @patch.object(module, "is_sunshine_reachable")
    def test_offline_then_online_starts_stream(self, reachable, popen, run, sleep):
        reachable.side_effect = [False, False, True, True]
        first = MagicMock()
        first.poll.return_value = None
        second = MagicMock()
        second.poll.return_value = 0
        popen.side_effect = [first, second]
        module.supervise("moonlight", "host", "app")
        first.terminate.assert_called_once()
        run.assert_called_once_with(["moonlight", "stream", "host", "app"], check=False)
        self.assertIn(call(3), sleep.call_args_list)

    @patch.object(module.time, "sleep")
    @patch.object(module.subprocess, "run")
    @patch.object(module.subprocess, "Popen")
    @patch.object(module, "is_sunshine_reachable", return_value=True)
    def test_manual_exit_does_not_relaunch_stream(self, reachable, popen, run, sleep):
        gui = popen.return_value
        gui.poll.side_effect = [None, 0]
        module.supervise("moonlight", "host", "app")
        self.assertEqual(run.call_count, 1)
        gui.terminate.assert_not_called()
