"""Offline schema validation for the Jev decision benchmark corpus.

Checks every entry in tests/jev_decision_corpus.yaml against the live
router allowlists (derived from router.build_request, so schema drift fails
loudly). No network, no models, no fixtures required.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import types
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "home-assistant" / "custom_components" / "jarvis_jev"
CORPUS_PATH = ROOT / "tests" / "jev_decision_corpus.yaml"

SERVICE_RE = re.compile(r"^[a-z_]+\.[a-z_]+$")
VALUE_ACTIONS = {"set_brightness", "set_temperature"}


def load_router():
    package = types.ModuleType("jarvis_jev")
    package.__path__ = [str(PACKAGE_DIR)]
    sys.modules["jarvis_jev"] = package
    for name in ("const", "router"):
        spec = importlib.util.spec_from_file_location(
            f"jarvis_jev.{name}", PACKAGE_DIR / f"{name}.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"jarvis_jev.{name}"] = module
        spec.loader.exec_module(module)
    return sys.modules["jarvis_jev.router"]


router = load_router()
PROBE = router.build_request("probe")
KINDS = set(PROBE["questions"]["request_kind"]["criteria"])
TARGETS = set(PROBE["questions"]["target"]["criteria"])
ACTIONS = set(PROBE["questions"]["action"]["criteria"])
COLORS = set(PROBE["questions"]["color"]["criteria"])
REFERENCES = set(PROBE["questions"]["reference"]["criteria"])
ROUTES = {"execute", "clarify", "fallback"}


def load_corpus() -> list:
    with open(CORPUS_PATH, encoding="utf-8") as f:
        entries = yaml.safe_load(f)
    assert isinstance(entries, list) and entries
    return entries


class JevDecisionCorpusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.entries = load_corpus()

    def test_ids_unique_and_say_present(self) -> None:
        ids = [e.get("id") for e in self.entries]
        self.assertEqual(len(ids), len(set(ids)), "duplicate corpus ids")
        for entry in self.entries:
            self.assertTrue(entry.get("id"), "entry without id")
            self.assertTrue(
                isinstance(entry.get("say"), str) and entry["say"].strip(),
                f"{entry.get('id')}: empty say",
            )

    def test_routes_and_kinds_valid(self) -> None:
        for entry in self.entries:
            case = entry.get("id")
            self.assertIn(entry.get("expected_route"), ROUTES, case)
            expected = entry.get("expected")
            self.assertIsInstance(expected, dict, case)
            self.assertIn(expected.get("request_kind"), KINDS, case)

    def test_optional_labels_within_allowlists(self) -> None:
        for entry in self.entries:
            case = entry.get("id")
            expected = entry["expected"]
            if "target" in expected:
                self.assertIn(expected["target"], TARGETS, case)
            if "action" in expected:
                self.assertIn(expected["action"], ACTIONS, case)
            if "color" in expected:
                self.assertIn(expected["color"], COLORS, case)
            if "reference" in expected:
                self.assertIn(expected["reference"], REFERENCES, case)

    def test_execute_cases_fully_bound(self) -> None:
        for entry in self.entries:
            if entry.get("expected_route") != "execute":
                continue
            case = entry.get("id")
            expected = entry["expected"]
            for field in ("target", "action", "color", "reference"):
                self.assertIn(field, expected, f"{case}: execute without {field}")
            self.assertNotEqual(expected["target"], "none_or_unknown", case)
            self.assertNotEqual(expected["action"], "none_or_unsupported", case)
            if expected["action"] == "play_music":
                # Music plays on the default speaker without naming a device.
                self.assertEqual(expected["reference"], "missing_or_ambiguous", case)
            else:
                self.assertEqual(expected["reference"], "explicit_target", case)
            if expected["action"] in VALUE_ACTIONS:
                self.assertIn("value", entry, f"{case}: value action without value")
                self.assertIsInstance(entry["value"], (int, float), case)
            else:
                self.assertNotIn("value", entry, f"{case}: stray value")
            targets = entry.get("targets")
            self.assertTrue(targets, f"{case}: execute without targets")
            entity = router.TARGETS[expected["target"]].entity_id
            for target in targets:
                self.assertEqual(target, entity, f"{case}: target/entity mismatch")
            service = entry.get("service", "")
            self.assertTrue(
                isinstance(service, str) and SERVICE_RE.match(service),
                f"{case}: bad service {service!r}",
            )

    def test_non_execute_cases_carry_no_action(self) -> None:
        for entry in self.entries:
            if entry.get("expected_route") == "execute":
                continue
            case = entry.get("id")
            for field in ("targets", "service", "value"):
                self.assertNotIn(field, entry, f"{case}: non-execute binds {field}")

    def test_probe_allowlists_cover_router_tables(self) -> None:
        self.assertEqual(TARGETS - {"none_or_unknown"}, set(router.TARGETS))
        self.assertEqual(COLORS - {"none_or_unknown"}, set(router.COLORS))


if __name__ == "__main__":
    unittest.main()
