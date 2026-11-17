import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from scripts.ci import uwagent_grades

ROOT = Path(__file__).resolve().parents[1]


def grades(*window: str) -> str:
    return json.dumps({"window": list(window), "courses": {}})


class FakeCheckout:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls: list[tuple[str, ...]] = []
        (path / "data").mkdir(parents=True)
        (path / uwagent_grades.GRADES).write_text(grades("FA23", "SP26"))
        self.original = (path / uwagent_grades.GRADES).read_text()

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        changed = (self.path / uwagent_grades.GRADES).read_text() != self.original
        code = 1 if args[0] == "diff" and changed else 0
        return subprocess.CompletedProcess(args, code, "", "")


class FakeNode:
    def __init__(self, checkout: FakeCheckout, published: str | None) -> None:
        self.checkout = checkout
        self.published = published
        self.calls: list[tuple[tuple[str, ...], dict[str, str] | None]] = []

    def npm_run(
        self, cwd: Path, *args: str, extra: dict[str, str] | None = None
    ) -> None:
        self.calls.append((args, extra))
        if "fetch-madgrades" in args and self.published:
            (cwd / uwagent_grades.GRADES).write_text(grades("SP24", self.published))


class RefreshTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.checkout = FakeCheckout(Path(self.directory.name) / "uwagent")
        self.forgejo = MagicMock()
        self.forgejo.branch.return_value = None
        self.forgejo.call.return_value = {"html_url": "https://git/pr/5"}

    def tearDown(self) -> None:
        self.directory.cleanup()

    def refresh(self, published: str | None) -> tuple[str, FakeNode]:
        node = FakeNode(self.checkout, published)
        result = uwagent_grades.refresh(self.checkout, node, self.forgejo, "secret")
        return result, node

    def scripts(self, node: FakeNode) -> list[tuple[str, ...]]:
        return [args for args, _ in node.calls]

    def test_no_new_term_stops_after_the_poll(self) -> None:
        result, node = self.refresh(None)
        self.assertEqual(result, "grades.json unchanged at SP26")
        self.assertEqual(
            self.scripts(node),
            [
                ("ci", "--no-audit", "--no-fund"),
                ("run", "fetch-madgrades", "--", "--if-new"),
            ],
        )
        self.assertEqual(node.calls[1][1], {"MADGRADES_TOKEN": "secret"})
        self.forgejo.call.assert_not_called()
        self.assertNotIn(("push",), [call[:1] for call in self.checkout.calls])

    def test_a_new_term_is_built_tested_and_proposed(self) -> None:
        result, node = self.refresh("FA26")
        self.assertEqual(result, "opened https://git/pr/5")
        self.assertEqual(
            self.scripts(node)[2:],
            [
                ("run", "build"),
                ("exec", "--", "vitest", "run", *uwagent_grades.TESTS),
            ],
        )
        # Only the fetch gets the Madgrades token.
        self.assertEqual([extra for _, extra in node.calls].count(None), 3)
        self.forgejo.branch.assert_called_once_with("chore/grades-FA26")
        self.assertIn(
            ("push", "origin", "HEAD:refs/heads/chore/grades-FA26"),
            self.checkout.calls,
        )
        method, path, body = self.forgejo.call.call_args.args
        self.assertEqual((method, path), ("POST", "/pulls"))
        self.assertEqual(
            (body["base"], body["head"], body["title"]),
            (
                "main",
                "chore/grades-FA26",
                "chore(data): refresh Madgrades grades through FA26",
            ),
        )
        self.assertIn("through SP26", body["body"])

    def test_an_existing_branch_is_not_proposed_again(self) -> None:
        self.forgejo.branch.return_value = "c" * 40
        result, node = self.refresh("FA26")
        self.assertEqual(
            result, "chore/grades-FA26 already exists; not proposing it again"
        )
        self.assertNotIn(("run", "build"), self.scripts(node))
        self.forgejo.call.assert_not_called()


class MainTest(unittest.TestCase):
    environment = {"FORGEJO_PUBLISH_TOKEN": "publish", "MADGRADES_TOKEN": "secret"}

    def test_an_open_refresh_pr_skips_the_poll(self) -> None:
        clone = MagicMock()
        forgejo = MagicMock()
        forgejo.call.return_value = [
            {"head": {"ref": "feat/other"}, "html_url": "a"},
            {"head": {"ref": "chore/grades-FA26"}, "html_url": "b"},
        ]
        with (
            patch.dict(os.environ, self.environment),
            patch.object(uwagent_grades, "Forgejo", return_value=forgejo) as client,
        ):
            self.assertEqual(uwagent_grades.main(ROOT, clone=clone), 0)
        client.assert_called_once_with("publish", "JEFF7712/uwagent")
        clone.assert_not_called()


class NodeTest(unittest.TestCase):
    def test_npm_scripts_never_see_ci_credentials(self) -> None:
        node = object.__new__(uwagent_grades.Node)
        node.bin = Path("/toolchain/node/bin")
        node.npm = Path("/toolchain/node/lib/node_modules/npm/bin/npm-cli.js")
        node.cache = Path("/work/npm-cache")
        node.home = Path("/work/home")
        captured: dict[str, Any] = {}

        def run(command: list[str], **kwargs: Any) -> None:
            captured.update(kwargs, command=command)

        secrets = {
            "FORGEJO_PUBLISH_TOKEN": "publish",
            "FORGEJO_PACKAGE_TOKEN": "package",
            "MADGRADES_TOKEN": "secret",
            "TF_HTTP_PASSWORD": "state",
        }
        with (
            patch.dict(os.environ, secrets),
            patch.object(uwagent_grades.subprocess, "run", side_effect=run),
        ):
            node.npm_run(Path("/src"), "ci", extra={"MADGRADES_TOKEN": "secret"})
        env = captured["env"]
        self.assertEqual(
            sorted(env),
            [
                "HOME",
                "MADGRADES_TOKEN",
                "NPM_CONFIG_CACHE",
                "NPM_CONFIG_OFFLINE",
                "NPM_CONFIG_UPDATE_NOTIFIER",
                "PATH",
            ],
        )
        self.assertTrue(env["PATH"].startswith("/toolchain/node/bin:"))
        self.assertEqual(captured["command"][0], "/toolchain/node/bin/node")


class CatalogTest(unittest.TestCase):
    def test_the_release_npm_cache_pin_is_found(self) -> None:
        catalog = json.loads((ROOT / "config/ci/applications.json").read_text())
        item = uwagent_grades.supply(catalog, uwagent_grades.CACHE)
        self.assertRegex(item["sha256"], r"^sha256:[0-9a-f]{64}$")

    def test_the_operation_mutates_with_only_its_credentials(self) -> None:
        catalog = json.loads((ROOT / "config/ci/operations.json").read_text())
        operation = catalog["uwagent-grades-refresh"]
        self.assertEqual(operation["commands"], ["python -m scripts.ci.uwagent_grades"])
        self.assertTrue(operation["mutation"])
        self.assertFalse(operation["automatic"])
        secrets = {
            name: value["from_secret"]
            for name, value in operation["environment"].items()
            if isinstance(value, dict)
        }
        self.assertEqual(secrets["FORGEJO_PUBLISH_TOKEN"], "forgejo_publish_token")
        # Supply downloads use the read-only token, never supply_write_token.
        self.assertEqual(secrets["FORGEJO_PACKAGE_TOKEN"], "supply_read_password")
        self.assertEqual(secrets["MADGRADES_TOKEN"], "madgrades_token")


if __name__ == "__main__":
    unittest.main()
