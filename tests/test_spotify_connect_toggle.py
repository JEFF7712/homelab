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

    def test_bedroom_helper_definition(self) -> None:
        helper_file = (
            REPO_ROOT
            / "home-assistant"
            / "helpers"
            / "input_boolean_spotify_connect_bedroom.yaml"
        )
        self.assertTrue(helper_file.is_file(), "Bedroom helper file must exist")
        data = yaml.safe_load(helper_file.read_text(encoding="utf-8"))
        self.assertEqual(data.get("entity_id"), "input_boolean.spotify_connect_bedroom")
        self.assertEqual(data.get("name"), "Bedroom Spotify Connect")

    def test_entity_registry_registration(self) -> None:
        entities_file = REPO_ROOT / "home-assistant" / "registries" / "entities.yaml"
        data = yaml.safe_load(entities_file.read_text(encoding="utf-8"))
        registered = [
            entry.get("entity_id") for entry in data if isinstance(entry, dict)
        ]
        self.assertIn("input_boolean.spotify_connect", registered)
        self.assertIn("input_boolean.spotify_connect_bedroom", registered)

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
        triggers = data.get("triggers", [])
        state_triggers = [t for t in triggers if t.get("trigger") == "state"]
        trigger_entities = []
        for st in state_triggers:
            ent = st.get("entity_id")
            if isinstance(ent, list):
                trigger_entities.extend(ent)
            elif isinstance(ent, str):
                trigger_entities.append(ent)
        self.assertIn("input_boolean.spotify_connect", trigger_entities)
        self.assertIn("input_boolean.spotify_connect_bedroom", trigger_entities)

    def test_core_configuration_shell_command(self) -> None:
        core_file = REPO_ROOT / "home-assistant" / "core" / "configuration.yaml"
        content = core_file.read_text(encoding="utf-8")
        self.assertIn("shell_command:", content)
        self.assertIn("set_music_assistant_spotify_connect:", content)
        self.assertIn("media_player.homelab_05_satellite_media_player", content)
        self.assertIn("up4f07df12", content)

    def test_dashboard_cards(self) -> None:
        shared_file = REPO_ROOT / "home-assistant" / "dashboards" / "shared-spaces.yaml"
        shared_content = shared_file.read_text(encoding="utf-8")
        self.assertIn("input_boolean.spotify_connect", shared_content)

        bedroom_file = (
            REPO_ROOT / "home-assistant" / "dashboards" / "bedroom-lights.yaml"
        )
        bedroom_content = bedroom_file.read_text(encoding="utf-8")
        self.assertIn("input_boolean.spotify_connect_bedroom", bedroom_content)
        self.assertIn("script.connect_bedroom_speaker", bedroom_content)


if __name__ == "__main__":
    unittest.main()
