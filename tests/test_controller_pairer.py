import asyncio
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

PAIRER_PATH = (
    Path(__file__).resolve().parent.parent
    / "flake"
    / "modules"
    / "controller-pairer.py"
)
spec = importlib.util.spec_from_file_location("controller_pairer", PAIRER_PATH)
assert spec and spec.loader
controller_pairer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller_pairer)


class TestControllerPairer(unittest.TestCase):
    def test_is_controller_candidate(self) -> None:
        # Match by icon
        self.assertTrue(
            controller_pairer.is_controller_candidate("Device", "", "input-gaming")
        )

        # Match by keyword
        self.assertTrue(
            controller_pairer.is_controller_candidate("Lic3 Pro Controller", "", "")
        )
        self.assertTrue(
            controller_pairer.is_controller_candidate(
                "Xbox Wireless Controller", "", ""
            )
        )
        self.assertTrue(
            controller_pairer.is_controller_candidate(
                "DualSense Wireless Controller", "", ""
            )
        )

        # Match by dev_class (Major 5: Peripheral, Minor 1: Joystick or 2: Gamepad)
        self.assertTrue(
            controller_pairer.is_controller_candidate("Generic", "0x00000508", "")
        )
        self.assertTrue(
            controller_pairer.is_controller_candidate("Generic", "0x00002508", "")
        )

        # Non-controllers
        self.assertFalse(
            controller_pairer.is_controller_candidate("LAURENSYOGA", "0x00000104", "")
        )
        self.assertFalse(
            controller_pairer.is_controller_candidate(
                "DESKTOP-PC", "0x00000104", "computer"
            )
        )
        self.assertFalse(
            controller_pairer.is_controller_candidate(
                "LG S77S", "0x00240428", "audio-card"
            )
        )

    def test_format_connected_status(self) -> None:
        self.assertEqual(controller_pairer.format_connected_status([]), "Ready")
        self.assertEqual(
            controller_pairer.format_connected_status(
                [("AA:BB:CC:DD:EE:FF", "Lic3 Pro Controller")]
            ),
            "Connected: Lic3 Pro Controller",
        )
        self.assertEqual(
            controller_pairer.format_connected_status(
                [
                    ("AA:BB:CC:DD:EE:01", "Lic Pro Controller"),
                    ("AA:BB:CC:DD:EE:02", "Lic3 Pro Controller"),
                ]
            ),
            "Connected (2): Lic Pro Controller, Lic3 Pro Controller",
        )

    def test_stdbuf_included_when_available(self) -> None:
        pairer = controller_pairer.ControllerPairer(
            mqtt_host="",
            mqtt_port=0,
            mqtt_user=None,
            mqtt_pass=None,
            scan_timeout=10,
        )

        fake_proc = AsyncMock()
        fake_proc.stdout.readline = AsyncMock(side_effect=[b"", b""])
        fake_proc.terminate = unittest.mock.MagicMock()
        fake_proc.wait = AsyncMock()

        async def run_test() -> None:
            with (
                patch("shutil.which", return_value="/bin/stdbuf"),
                patch(
                    "asyncio.create_subprocess_exec", return_value=fake_proc
                ) as mock_exec,
                patch.object(
                    controller_pairer, "get_connected_controllers", return_value=[]
                ),
            ):
                success, msg = await pairer.run_pairing_session()
                self.assertFalse(success)
                self.assertEqual(msg, "Pairing timed out")
                mock_exec.assert_called_once()
                args = mock_exec.call_args[0]
                self.assertEqual(args[:3], ("stdbuf", "-oL", "-eL"))
                self.assertEqual(args[3:6], ("bluetoothctl", "--timeout", "10"))

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
