import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.ci import fleet_lock_update

OLD = "a" * 40
NEW = "b" * 40


def lock(nixpkgs: str, agents: str = "c" * 40) -> dict:
    return {
        "root": "root",
        "version": 7,
        "nodes": {
            "root": {
                "inputs": {
                    "nixpkgs": "nixpkgs_2",
                    "nixpkgs-agents": "nixpkgs_3",
                    "sops-nix": "sops-nix",
                }
            },
            # Another input's own nixpkgs, named plainly "nixpkgs".
            "nixpkgs": {"locked": {"rev": "d" * 40, "lastModified": 1}},
            "nixpkgs_2": {"locked": {"rev": nixpkgs, "lastModified": 1794268800}},
            "nixpkgs_3": {"locked": {"rev": agents, "lastModified": 1794268800}},
            "sops-nix": {
                "inputs": {"nixpkgs": ["nixpkgs"]},
                "locked": {"rev": "e" * 40, "lastModified": 1},
            },
        },
    }


class FleetLockUpdateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cwd = os.getcwd()
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / "flake").mkdir()
        self.write(lock(OLD))

    def tearDown(self) -> None:
        os.chdir(self.cwd)
        self.directory.cleanup()

    def write(self, content: dict) -> None:
        (self.root / "flake/flake.lock").write_text(json.dumps(content))

    def run_update(self, updated: dict) -> MagicMock:
        def nix(argv, **kwargs):
            self.assertEqual(
                argv, ["nix", "flake", "update", "nixpkgs", "--flake", "./flake"]
            )
            self.write(updated)

        propose = MagicMock()
        with patch.dict(os.environ, {"FORGEJO_PUBLISH_TOKEN": "token"}):
            fleet_lock_update.main(self.root, run=nix, propose=propose)
        return propose

    def test_the_fleet_input_is_found_through_the_root(self) -> None:
        self.assertEqual(fleet_lock_update.locked(lock(OLD))["rev"], OLD)
        self.assertEqual(
            fleet_lock_update.locked(lock(OLD), "sops-nix")["rev"], "e" * 40
        )

    def test_a_moved_nixpkgs_is_proposed_on_one_branch(self) -> None:
        propose = self.run_update(lock(NEW))
        propose.assert_called_once()
        token, branch, title, body, paths = propose.call_args.args
        self.assertEqual(
            (token, branch, paths),
            ("token", "flake-lock/nixpkgs", ["flake/flake.lock"]),
        )
        self.assertEqual(title, "flake: move nixpkgs to nixos-unstable 2026-11-10")
        self.assertIn(f"compare/{OLD}...{NEW}", body)

    def test_a_current_nixpkgs_proposes_nothing(self) -> None:
        self.run_update(lock(OLD)).assert_not_called()

    def test_moving_any_other_input_aborts(self) -> None:
        # Regression (#782): a whole-lock update moved every input and
        # upgraded the fleet unannounced.
        with self.assertRaisesRegex(SystemExit, "nixpkgs-agents"):
            self.run_update(lock(NEW, agents="f" * 40))


if __name__ == "__main__":
    unittest.main()
