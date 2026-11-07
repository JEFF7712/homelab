from __future__ import annotations

import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestSpotifyConnectToggle(unittest.TestCase):
    def test_helper_definition(self) -> None:
        helper_file = (
            REPO_ROOT
            / "home-assistant"
            / "helpers"
            / "input_boolean_spotify_connect.yaml"
        )
        self.assertTrue(helper_file.is_file(), "Helper file must exist")
        data = yaml.safe_load(helper_file.read_text(encoding="utf-8"))
        self.assertEqual(data.get("entity_id"), "input_boolean.spotify_connect")
        self.assertEqual(data.get("name"), "Spotify Connect")

    def test_sync_automation(self) -> None:
        auto_file = (
            REPO_ROOT / "home-assistant" / "automations" / "spotify_connect_sync.yaml"
        )
        self.assertTrue(auto_file.is_file(), "Automation file must exist")
        data = yaml.safe_load(auto_file.read_text(encoding="utf-8"))
        self.assertEqual(data.get("id"), "spotify_connect_sync")
        self.assertEqual(data.get("alias"), "Music Assistant: Spotify Connect Sync")
        actions = data.get("actions", [])
        self.assertTrue(
            any(
                a.get("action") == "shell_command.set_music_assistant_spotify_connect"
                for a in actions
            )
        )

    def test_core_configuration_shell_command(self) -> None:
        core_file = REPO_ROOT / "home-assistant" / "core" / "configuration.yaml"
        content = core_file.read_text(encoding="utf-8")
        self.assertIn("shell_command:", content)
        self.assertIn("set_music_assistant_spotify_connect:", content)

    def test_dashboard_card(self) -> None:
        dashboard_file = (
            REPO_ROOT / "home-assistant" / "dashboards" / "shared-spaces.yaml"
        )
        content = dashboard_file.read_text(encoding="utf-8")
        self.assertIn("input_boolean.spotify_connect", content)


if __name__ == "__main__":
    unittest.main()
