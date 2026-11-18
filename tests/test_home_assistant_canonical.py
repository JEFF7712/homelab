from __future__ import annotations

import json
import unittest

from scripts.home_assistant.canonical import (
    IncludeDirTag,
    IncludeTag,
    SecretTag,
    canonical_hash,
    canonical_json,
    detect_secrets,
    dump_yaml,
    parse_yaml,
    strip_volatile,
)


class TestHomeAssistantCanonical(unittest.TestCase):
    def test_parse_and_dump_secret_and_include_tags(self) -> None:
        raw_yaml = (
            "default_config:\n"
            "recorder:\n"
            "  db_url: !secret recorder_db_url\n"
            "automation: !include automations.yaml\n"
        )
        parsed = parse_yaml(raw_yaml)
        self.assertIsInstance(parsed["recorder"]["db_url"], SecretTag)
        self.assertEqual(parsed["recorder"]["db_url"].value, "recorder_db_url")
        self.assertIsInstance(parsed["automation"], IncludeTag)
        self.assertEqual(parsed["automation"].value, "automations.yaml")

        dumped = dump_yaml(parsed)
        self.assertIn("!secret recorder_db_url", dumped)
        self.assertIn("!include automations.yaml", dumped)

    def test_parse_and_dump_include_dir_merge_named_tag(self) -> None:
        raw_yaml = "frontend:\n  themes: !include_dir_merge_named themes\n"
        parsed = parse_yaml(raw_yaml)
        self.assertIsInstance(parsed["frontend"]["themes"], IncludeDirTag)
        self.assertEqual(parsed["frontend"]["themes"].value, "themes")

        dumped = dump_yaml(parsed)
        self.assertIn("!include_dir_merge_named themes", dumped)

    def test_rejects_duplicate_yaml_keys(self) -> None:
        raw_yaml = "alias: Test\nalias: Duplicate\n"
        with self.assertRaises(ValueError) as ctx:
            parse_yaml(raw_yaml)
        self.assertIn("Duplicate YAML key", str(ctx.exception))

    def test_preserves_list_and_action_order(self) -> None:
        data = {
            "id": "test_auto",
            "actions": [
                {"action": "light.turn_on", "target": {"entity_id": "light.a"}},
                {"action": "delay", "seconds": 5},
                {"action": "light.turn_off", "target": {"entity_id": "light.b"}},
            ],
        }
        dumped = dump_yaml(data)
        parsed = parse_yaml(dumped)
        self.assertEqual(parsed["actions"][0]["action"], "light.turn_on")
        self.assertEqual(parsed["actions"][1]["action"], "delay")
        self.assertEqual(parsed["actions"][2]["action"], "light.turn_off")

    def test_canonical_json_and_hash_deterministic(self) -> None:
        data1 = {"b": 2, "a": 1, "nested": {"y": 2, "x": 1}}
        data2 = {"a": 1, "b": 2, "nested": {"x": 1, "y": 2}}
        self.assertEqual(canonical_json(data1), canonical_json(data2))
        self.assertEqual(canonical_hash(data1), canonical_hash(data2))

    def test_variables_keep_source_order_through_plan_and_dump(self) -> None:
        # HA renders a variables step in order; sorting broke live automations.
        from scripts.home_assistant.models import ActionType, PlanAction

        variables = {"owners": {"b": 1, "a": 2}, "candidates": "{{ owners }}"}
        config = {"alias": "x", "actions": [{"variables": variables}]}
        action = PlanAction(
            kind="automation",
            key="x",
            action=ActionType.UPDATE,
            before=None,
            after=config,
        )
        restored = PlanAction.from_dict(json.loads(json.dumps(action.to_dict())))
        payload = restored.after["actions"][0]["variables"]
        self.assertEqual(list(payload), ["owners", "candidates"])
        self.assertEqual(list(payload["owners"]), ["b", "a"])
        dumped = parse_yaml(dump_yaml(config))["actions"][0]["variables"]
        self.assertEqual(list(dumped), ["owners", "candidates"])
        reordered = {"actions": [{"variables": dict(reversed(variables.items()))}]}
        self.assertEqual(
            canonical_hash({"actions": config["actions"]}), canonical_hash(reordered)
        )

    def test_strips_volatile_timestamps(self) -> None:
        data = {
            "id": "my_auto",
            "alias": "Hello",
            "last_triggered": "2026-09-09T10:00:00Z",
            "actions": [{"action": "test"}],
        }
        clean = strip_volatile(data)
        self.assertNotIn("last_triggered", clean)
        self.assertIn("alias", clean)

    def test_detect_secrets_identifies_credentials(self) -> None:
        clean_doc = {"id": "1", "api_key": SecretTag("my_secret")}
        self.assertEqual(detect_secrets(clean_doc), [])

        leaked_jwt = {
            "id": "1",
            "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig",
        }
        findings = detect_secrets(leaked_jwt)
        self.assertTrue(len(findings) > 0)

        leaked_url = {"db": "postgres://user:supersecretpass@db.local/ha"}
        findings_url = detect_secrets(leaked_url)
        self.assertTrue(len(findings_url) > 0)


if __name__ == "__main__":
    unittest.main()
