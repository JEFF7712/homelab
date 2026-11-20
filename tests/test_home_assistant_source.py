from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.home_assistant.adapters.core import (
    iter_custom_components,
    iter_custom_sentences,
    iter_www,
    render_config_configmap,
)
from scripts.home_assistant.models import ResourceDocument
from scripts.home_assistant.source import (
    adopt_resources,
    load_source_tree,
    remove_resource,
    validate_key,
    write_resource_atomic,
)


class TestHomeAssistantSource(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "home-assistant" / "core").mkdir(parents=True)
        (self.root / "home-assistant" / "automations").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_key_validation(self) -> None:
        validate_key("valid_key-123")
        with self.assertRaises(ValueError):
            validate_key("../traversal")
        with self.assertRaises(ValueError):
            validate_key("key with spaces")
        with self.assertRaises(ValueError):
            validate_key("key/slash")

    def test_atomic_write_and_load(self) -> None:
        doc = ResourceDocument(
            kind="automation",
            key="morning_lights",
            desired={"alias": "Morning Lights", "trigger": [], "action": []},
        )
        written_path = write_resource_atomic(self.root, doc)
        self.assertTrue(written_path.is_file())

        tree = load_source_tree(self.root)
        self.assertIn("automation/morning_lights", tree)
        loaded = tree["automation/morning_lights"]
        self.assertEqual(loaded.desired["alias"], "Morning Lights")

    def test_selected_adopt_preserves_unselected(self) -> None:
        doc1 = ResourceDocument(
            kind="automation", key="auto_one", desired={"alias": "One"}
        )
        doc2 = ResourceDocument(
            kind="automation", key="auto_two", desired={"alias": "Two"}
        )

        # Initial write of auto_one
        write_resource_atomic(self.root, doc1)

        # Adopt only auto_two
        written = adopt_resources(
            self.root, [doc1, doc2], selected_keys={"automation/auto_two"}
        )
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0].stem, "auto_two")

        tree = load_source_tree(self.root)
        self.assertIn("automation/auto_one", tree)
        self.assertIn("automation/auto_two", tree)
        self.assertEqual(tree["automation/auto_one"].desired["alias"], "One")
        self.assertEqual(tree["automation/auto_two"].desired["alias"], "Two")

    def test_adoption_rollback_on_write_error(self) -> None:
        doc_good = ResourceDocument(
            kind="automation", key="good", desired={"alias": "Good"}
        )
        # Write good doc first
        orig_file = write_resource_atomic(self.root, doc_good)
        orig_content = orig_file.read_text(encoding="utf-8")

        # Create a document whose target cannot be written (simulate error)
        # by trying to adopt with an unhashable/invalid desired object during dump
        class BadObject:
            pass

        bad_doc = ResourceDocument(
            kind="automation", key="bad", desired={"bad": BadObject()}
        )
        modified_good_doc = ResourceDocument(
            kind="automation", key="good", desired={"alias": "Modified"}
        )

        with self.assertRaises(RuntimeError):
            adopt_resources(self.root, [modified_good_doc, bad_doc])

        # Verify orig_file was rolled back to orig_content!
        self.assertEqual(orig_file.read_text(encoding="utf-8"), orig_content)

    def test_remove_resource(self) -> None:
        doc = ResourceDocument(kind="automation", key="temp", desired={"alias": "Temp"})
        path = write_resource_atomic(self.root, doc)
        self.assertTrue(path.is_file())
        removed = remove_resource(self.root, "automation", "temp")
        self.assertTrue(removed)
        self.assertFalse(path.exists())

    def test_registry_plural_filenames_round_trip(self) -> None:
        from scripts.home_assistant.source import get_source_path

        expected = {
            "area": "areas.yaml",
            "floor": "floors.yaml",
            "label": "labels.yaml",
            "device": "devices.yaml",
            "entity": "entities.yaml",
        }
        for kind, filename in expected.items():
            _ = ResourceDocument(kind=kind, key="collection", desired=[])
            self.assertEqual(
                get_source_path(self.root, kind, "collection").name, filename
            )

        entity_doc = ResourceDocument(
            kind="entity",
            key="collection",
            desired=[{"entity_id": "light.test", "labels": []}],
        )
        written = write_resource_atomic(self.root, entity_doc)
        self.assertEqual(written.name, "entities.yaml")
        tree = load_source_tree(self.root)
        self.assertIn("entity/collection", tree)

    def test_configmap_keys_are_valid(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        cm_text, _ = render_config_configmap(repo_root)
        data = yaml.safe_load(cm_text)["data"]
        pattern = re.compile(r"^[-._a-zA-Z0-9]+$")
        for key in data:
            self.assertRegex(key, pattern, key)
        rendered = {key: data[key] for key in data if key != "configuration.yaml"}
        expected = (
            dict(iter_custom_sentences(repo_root))
            | dict(iter_custom_components(repo_root))
            | dict(iter_www(repo_root))
        )
        self.assertEqual(rendered, expected)

    def test_jarvis_face_bundle_ships_in_configmap(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        cm_text, _ = render_config_configmap(repo_root)
        data = yaml.safe_load(cm_text)["data"]
        face_dir = repo_root / "home-assistant" / "www" / "jarvis"
        for path in face_dir.iterdir():
            self.assertEqual(
                data[f"www.jarvis.{path.name}"], path.read_text(encoding="utf-8")
            )

    def test_legacy_entitys_filename_still_loads(self) -> None:
        reg_dir = self.root / "home-assistant" / "registries"
        reg_dir.mkdir(parents=True, exist_ok=True)
        (reg_dir / "entitys.yaml").write_text(
            "- entity_id: light.legacy\n  labels: []\n", encoding="utf-8"
        )
        tree = load_source_tree(self.root)
        self.assertIn("entity/collection", tree)


if __name__ == "__main__":
    unittest.main()
