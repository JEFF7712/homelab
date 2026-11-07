from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.home_assistant.canonical import DuplicateKeySafeLoader

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PY = REPO_ROOT / "flake/modules/bedroom-bluetooth.py"
MODULE_NIX = REPO_ROOT / "flake/modules/bedroom-bluetooth.nix"
NAS_HOST = REPO_ROOT / "flake/hosts/nas-01/default.nix"
CORE_YAML = REPO_ROOT / "home-assistant/core/configuration.yaml"
SCRIPT_YAML = REPO_ROOT / "home-assistant/scripts/connect_bedroom_speaker.yaml"

spec = importlib.util.spec_from_file_location("bedroom_bluetooth", MODULE_PY)
if spec and spec.loader:
    bb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bb)
else:
    bb = None


SAMPLE_CONNECTED = """
Device E4:58:BC:10:CA:C9 (public)
\tName: Bose Flex 2 SoundLink
\tAlias: Bose Flex 2 SoundLink
\tClass: 0x002c0414 (2884628)
\tIcon: audio-card
\tPaired: yes
\tBonded: yes
\tTrusted: yes
\tBlocked: no
\tConnected: yes
\tBREDR.Connected: yes
"""

SAMPLE_DISCONNECTED = """
Device E4:58:BC:10:CA:C9 (public)
\tName: Bose Flex 2 SoundLink
\tAlias: Bose Flex 2 SoundLink
\tIcon: audio-card
\tPaired: no
\tBonded: no
\tTrusted: yes
\tConnected: no
"""


class TestBedroomBluetoothBridge(unittest.TestCase):
    def test_parse_bluetoothctl_info(self) -> None:
        self.assertIsNotNone(bb, "Module must load")
        assert bb is not None

        info_conn = bb.parse_bluetoothctl_info(SAMPLE_CONNECTED, "E4:58:BC:10:CA:C9")
        self.assertTrue(info_conn["connected"])
        self.assertTrue(info_conn["paired"])
        self.assertTrue(info_conn["trusted"])
        self.assertEqual(info_conn["name"], "Bose Flex 2 SoundLink")

        info_disc = bb.parse_bluetoothctl_info(SAMPLE_DISCONNECTED, "E4:58:BC:10:CA:C9")
        self.assertFalse(info_disc["connected"])
        self.assertFalse(info_disc["paired"])
        self.assertTrue(info_disc["trusted"])

    def test_connect_speaker(self) -> None:
        assert bb is not None
        with patch.object(
            bb,
            "run_bluetoothctl",
            side_effect=[
                (0, "Connection successful\n"),
                (0, SAMPLE_CONNECTED),
            ],
        ):
            res = bb.connect_speaker("E4:58:BC:10:CA:C9")
            self.assertEqual(res["status"], "connected")
            self.assertTrue(res["connected"])

    def test_connect_speaker_failure(self) -> None:
        assert bb is not None
        with patch.object(
            bb,
            "run_bluetoothctl",
            side_effect=[
                (1, "Failed to connect: org.bluez.Error.Failed\n"),
                (0, SAMPLE_DISCONNECTED),
            ],
        ):
            res = bb.connect_speaker("E4:58:BC:10:CA:C9")
            self.assertEqual(res["status"], "failed")
            self.assertFalse(res["connected"])

    def test_disconnect_speaker(self) -> None:
        assert bb is not None
        with patch.object(
            bb,
            "run_bluetoothctl",
            side_effect=[
                (0, "Successful disconnected\n"),
                (0, SAMPLE_DISCONNECTED),
            ],
        ):
            res = bb.disconnect_speaker("E4:58:BC:10:CA:C9")
            self.assertEqual(res["status"], "disconnected")
            self.assertFalse(res["connected"])

    def test_nixos_module_and_host_wiring(self) -> None:
        self.assertTrue(MODULE_NIX.is_file(), "bedroom-bluetooth.nix must exist")
        nix_content = MODULE_NIX.read_text(encoding="utf-8")
        self.assertIn("homelab.bedroomBluetooth", nix_content)
        self.assertIn("systemd.services.bedroom-bluetooth", nix_content)
        self.assertIn("dport", nix_content)

        host_content = NAS_HOST.read_text(encoding="utf-8")
        self.assertIn("../../modules/bedroom-bluetooth.nix", host_content)
        self.assertIn("homelab.bedroomBluetooth.enable = true", host_content)

    def test_home_assistant_configuration(self) -> None:
        core_raw = CORE_YAML.read_text(encoding="utf-8")
        core_data = yaml.load(core_raw, Loader=DuplicateKeySafeLoader)

        rest_cmds = core_data.get("rest_command", {})
        self.assertIn("bose_speaker_connect", rest_cmds)
        self.assertEqual(
            rest_cmds["bose_speaker_connect"]["url"],
            "http://10.0.30.20:8889/connect",
        )
        self.assertEqual(
            rest_cmds["bose_speaker_connect"]["method"],
            "post",
        )

        self.assertIn("bose_speaker_disconnect", rest_cmds)
        self.assertEqual(
            rest_cmds["bose_speaker_disconnect"]["url"],
            "http://10.0.30.20:8889/disconnect",
        )

        self.assertTrue(SCRIPT_YAML.is_file(), "Connect script must exist")
        script_data = yaml.safe_load(SCRIPT_YAML.read_text(encoding="utf-8"))
        self.assertTrue(
            any(
                act.get("action") == "rest_command.bose_speaker_connect"
                for act in script_data.get("sequence", [])
            )
        )


if __name__ == "__main__":
    unittest.main()
