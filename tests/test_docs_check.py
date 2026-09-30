from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.checks.docs import (
    command_contract_errors,
    iter_markdown_files,
    local_link_errors,
    trailing_whitespace_errors,
)


class DocsCheckTest(unittest.TestCase):
    def test_local_links_anchors_references_and_code_examples(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs/runbooks").mkdir(parents=True)
            (root / "docs/runbooks/target file.md").write_text(
                "# Repeated\n# Repeated\n"
            )
            (
                root / "README.md"
            ).write_text("""[ok](docs/runbooks/target%20file.md#repeated-1)
[missing](missing.md)
[bad anchor](docs/runbooks/target%20file.md#unknown)
[reference]: docs/runbooks/missing.md
[external](https://example.invalid/missing)
`[example](ignored.md)`
```md
[example](ignored.md)
```
""")
            errors = local_link_errors(root)
        self.assertEqual(len(errors), 3)
        self.assertIn("README.md:2: missing local link target", errors[0])
        self.assertIn("missing Markdown anchor", errors[1])

    def test_recipe_and_cli_contract_drift_is_reported_without_execution(self) -> None:
        root_source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "justfile").write_text(
                (root_source / "justfile")
                .read_text()
                .replace("scripts.agent context", "scripts.agent does-not-exist")
            )
            (root / "README.md").write_text("Use `just absent-recipe`.\n")
            errors = command_contract_errors(root)
        self.assertTrue(
            any("unknown just recipe absent-recipe" in error for error in errors)
        )
        self.assertTrue(any("agent-context must invoke" in error for error in errors))

    def test_vendored_trees_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "docs" / "owned.md").write_text("clean\n", encoding="utf-8")
            vendored = root / ".opencode" / "node_modules" / "dep"
            vendored.mkdir(parents=True)
            (vendored / "README.md").write_text("dirty   \n", encoding="utf-8")
            prints = root / "3d-prints" / "rack_extension"
            prints.mkdir(parents=True)
            (prints / "part.md").write_text("break  \n", encoding="utf-8")

            errors = trailing_whitespace_errors(root)
            scanned = list(iter_markdown_files(root))

        self.assertEqual(errors, [])
        self.assertEqual(scanned, [root / "docs" / "owned.md"])

    def test_owned_trailing_whitespace_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notes.md").write_text("dirty   \n", encoding="utf-8")

            errors = trailing_whitespace_errors(root)

        self.assertEqual(len(errors), 1)
        self.assertIn("notes.md:1: trailing whitespace", errors[0])


if __name__ == "__main__":
    unittest.main()
