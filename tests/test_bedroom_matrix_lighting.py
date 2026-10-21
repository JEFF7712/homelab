from __future__ import annotations

import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
HA_ROOT = REPO_ROOT / "home-assistant"
MATRIX_POWER = "input_boolean.bedroom_matrix_power"


def load(relative: str) -> dict[str, Any]:
    path = HA_ROOT / relative
    if not path.is_file():
        raise AssertionError(f"missing Home Assistant source file: {relative}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AssertionError(f"{relative} must parse to a mapping")
    return data


def walk(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item)


def action_targets(document: dict[str, Any], action: str) -> set[str]:
    targets: set[str] = set()
    for step in walk(document):
        if step.get("action") != action:
            continue
        target = step.get("target")
        if not isinstance(target, dict):
            continue
        entities = target.get("entity_id")
        if isinstance(entities, list):
            targets.update(str(entity) for entity in entities)
        elif isinstance(entities, str):
            targets.add(entities)
    return targets


class TestBedroomMatrixLighting(unittest.TestCase):
    def test_bedroom_lights_off_powers_matrix_down(self) -> None:
        script = load("scripts/bedroom_lights_off.yaml")
        self.assertIn(
            MATRIX_POWER,
            action_targets(script, "input_boolean.turn_off"),
        )

    def test_bedroom_lights_on_powers_matrix_up(self) -> None:
        script = load("scripts/bedroom_lights_on.yaml")
        self.assertIn(
            MATRIX_POWER,
            action_targets(script, "input_boolean.turn_on"),
        )

    def test_matrix_power_is_not_snapshot_by_the_bedroom_scripts(self) -> None:
        """The matrix follows the room, it is not restored from a snapshot."""
        off_script = load("scripts/bedroom_lights_off.yaml")
        snapshots = [
            step for step in walk(off_script) if step.get("action") == "scene.create"
        ]
        self.assertTrue(snapshots)
        for step in snapshots:
            entities = step.get("data", {}).get("snapshot_entities", [])
            self.assertNotIn(MATRIX_POWER, entities)

    def test_away_automation_snapshots_and_enforces_the_matrix(self) -> None:
        automation = load("automations/bedroom_away_lights.yaml")
        snapshots = [
            step for step in walk(automation) if step.get("action") == "scene.create"
        ]
        self.assertTrue(snapshots)
        for step in snapshots:
            entities = step.get("data", {}).get("snapshot_entities", [])
            self.assertIn(MATRIX_POWER, entities)

        enforced_entities: set[str] = set()
        for trigger in automation.get("triggers", []):
            if trigger.get("id") != "enforced":
                continue
            enforced_entities.update(trigger.get("entity_id", []))
        self.assertIn(MATRIX_POWER, enforced_entities)

    def test_wake_up_fade_powers_matrix_up(self) -> None:
        automation = load("automations/bedroom_lights_morning_wake_up.yaml")
        self.assertIn(
            MATRIX_POWER,
            action_targets(automation, "input_boolean.turn_on"),
        )

    def test_lighting_director_drives_the_bedroom_through_the_scripts(self) -> None:
        """The director must reuse the scripts so the matrix keeps following."""
        director = load("automations/lighting_director.yaml")
        called = {
            step.get("action")
            for step in walk(director)
            if str(step.get("action", "")).startswith("script.")
        }
        self.assertIn("script.bedroom_lights_on", called)
        self.assertIn("script.bedroom_lights_off", called)

    def test_shared_space_scripts_leave_the_matrix_alone(self) -> None:
        for relative in (
            "scripts/shared_lights_off.yaml",
            "scripts/shared_lights_on.yaml",
        ):
            with self.subTest(relative=relative):
                raw = (HA_ROOT / relative).read_text(encoding="utf-8")
                self.assertNotIn(MATRIX_POWER, raw)


if __name__ == "__main__":
    unittest.main()
