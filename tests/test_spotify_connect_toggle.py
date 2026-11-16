from __future__ import annotations

import contextlib
import io
import itertools
import json
import shlex
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import yaml
from jinja2 import Environment, StrictUndefined

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestSpotifyConnectToggle(unittest.TestCase):
    def run_sync(
        self,
        living_room: bool | None,
        bedroom: bool | None,
        current: list[str],
        *,
        enabled: bool = True,
        fail: bool = False,
        ignore_save: bool = False,
    ) -> list[dict]:
        """Execute the actual rendered shell-command Python, replacing only I/O."""
        config = yaml.load(
            (REPO_ROOT / "home-assistant/core/configuration.yaml").read_text(),
            Loader=yaml.BaseLoader,
        )
        command = config["shell_command"]["set_music_assistant_spotify_connect"]
        variables = {}
        if living_room is not None:
            variables["living_room"] = living_room
        if bedroom is not None:
            variables["bedroom"] = bedroom
        rendered = (
            Environment(undefined=StrictUndefined)
            .from_string(command)
            .render(**variables)
        )
        argv = shlex.split(rendered)
        self.assertEqual(argv[:2], ["python3", "-c"])
        code = compile(argv[2], "spotify-connect-shell-command", "exec")
        state = {
            "enabled": enabled,
            "values": {"connected_players": {"value": list(current)}},
        }
        requests = []

        def urlopen(request, *, timeout):
            self.assertEqual(timeout, 15)
            self.assertEqual(request.full_url, "http://configured-ma:8095/api")
            self.assertEqual(
                request.get_header("Authorization"), "Bearer test-only-token"
            )
            if fail:
                raise urllib.error.HTTPError(
                    request.full_url, 503, "unavailable", {}, None
                )
            data = json.loads(request.data)
            requests.append(data)
            if data["command"] == "config/providers/save" and not ignore_save:
                values = data["args"]["values"]
                state["enabled"] = values["enabled"]
                state["values"]["connected_players"]["value"] = values[
                    "connected_players"
                ]
            return io.BytesIO(json.dumps(state).encode())

        entries = {
            "data": {
                "entries": [
                    {
                        "domain": "music_assistant",
                        "data": {
                            "url": "http://configured-ma:8095/",
                            "token": "test-only-token",
                        },
                    }
                ]
            }
        }
        with (
            mock.patch("builtins.open", mock.mock_open(read_data=json.dumps(entries))),
            mock.patch("urllib.request.urlopen", side_effect=urlopen),
            mock.patch("sys.argv", ["-c", *argv[3:]]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            exec(code, {"__name__": "__main__"})  # noqa: S102 - execute the repository-owned command under mocked I/O
        return requests

    def test_command_applies_and_reads_back_all_speaker_combinations(self) -> None:
        for living_room, bedroom in itertools.product((False, True), repeat=2):
            with self.subTest(living_room=living_room, bedroom=bedroom):
                requests = self.run_sync(living_room, bedroom, ["old-player"])
                self.assertEqual(
                    [r["command"] for r in requests],
                    [
                        "config/providers/get",
                        "config/providers/save",
                        "config/providers/get",
                    ],
                )
                expected = []
                if living_room:
                    expected.append("media_player.homelab_05_satellite_media_player")
                if bedroom:
                    expected.append("up4f07df12")
                self.assertEqual(
                    requests[1]["args"]["values"],
                    {
                        "enabled": True,
                        "connected_players": expected,
                    },
                )

    def test_unchanged_settings_do_not_reload_provider(self) -> None:
        requests = self.run_sync(
            True,
            True,
            [
                "up4f07df12",
                "media_player.homelab_05_satellite_media_player",
            ],
        )
        self.assertEqual([r["command"] for r in requests], ["config/providers/get"])

    def test_disabled_provider_can_be_reenabled(self) -> None:
        requests = self.run_sync(False, True, ["up4f07df12"], enabled=False)
        self.assertTrue(requests[1]["args"]["values"]["enabled"])

    def test_missing_service_arguments_default_to_off(self) -> None:
        requests = self.run_sync(None, None, ["up4f07df12"])
        self.assertEqual(requests[1]["args"]["values"]["connected_players"], [])

    def test_unavailable_api_fails_instead_of_reporting_success(self) -> None:
        with self.assertRaisesRegex(SystemExit, "HTTP 503"):
            self.run_sync(True, False, [], fail=True)

    def test_failed_readback_fails_instead_of_reporting_success(self) -> None:
        with self.assertRaisesRegex(SystemExit, "RuntimeError"):
            self.run_sync(True, False, [], ignore_save=True)

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
        self.assertEqual(data["mode"], "queued")
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
        self.assertTrue(any(t.get("trigger") == "time_pattern" for t in triggers))
        self.assertEqual(actions[0]["response_variable"], "sync_result")
        self.assertIn("persistent_notification.create", json.dumps(actions))

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
