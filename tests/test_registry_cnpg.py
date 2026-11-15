from __future__ import annotations

import pathlib
import tempfile
import unittest

from scripts.registry.core import discover_inventory


class DatabaseImageDiscovery(unittest.TestCase):
    def test_cnpg_image_name_is_an_image_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "gitops").mkdir()
            (root / "gitops/database.yaml").write_text(
                "apiVersion: postgresql.cnpg.io/v1\nkind: Cluster\n"
                "metadata:\n  name: database\nspec:\n"
                "  imageName: ghcr.io/cloudnative-pg/postgresql:17\n"
            )
            inventory = discover_inventory(root)
        self.assertEqual(len(inventory["images"]), 1)
        image = inventory["images"][0]
        self.assertEqual(
            image["source"]["reference"], "ghcr.io/cloudnative-pg/postgresql:17"
        )
        self.assertEqual(image["consumers"], ["gitops/database.yaml:6"])


if __name__ == "__main__":
    unittest.main()
