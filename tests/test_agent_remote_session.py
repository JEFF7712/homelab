from __future__ import annotations

import json
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.agent.remote_session import mapping_for, native_command, ssh_command


class RemoteSessionTests(unittest.TestCase):
    def test_unmapped_checkout_stays_local(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "repos.json"
            config.write_text(
                json.dumps(
                    {
                        "repos": [
                            {
                                "local": str(root / "other"),
                                "host": "agent-rupan",
                                "remote": "/home/developer/workspace/homelab",
                            }
                        ]
                    }
                )
            )
            with patch("scripts.agent.remote_session.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = str(root)
                self.assertIsNone(mapping_for(root, config))

    def test_subdirectory_is_mapped_without_copying_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "services"
            child.mkdir()
            config = root / "repos.json"
            config.write_text(
                json.dumps(
                    {
                        "repos": [
                            {
                                "local": str(root),
                                "host": "agent-rupan",
                                "remote": "/home/developer/workspace/homelab",
                            }
                        ]
                    }
                )
            )
            with patch("scripts.agent.remote_session.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = str(root)
                self.assertEqual(
                    mapping_for(child, config),
                    (
                        "agent-rupan",
                        Path("/home/developer/workspace/homelab"),
                        Path("services"),
                    ),
                )
            self.assertEqual(list(child.iterdir()), [])

    def test_arguments_survive_remote_shell_quoting(self) -> None:
        args = ["--model", "model name", "literal $(touch /tmp/unwanted); 'quoted'"]
        command = ssh_command(
            "agent-rupan",
            Path("/home/developer/workspace/a b"),
            Path("services/a b"),
            "codex",
            args,
        )
        self.assertEqual(command[:4], ["ssh", "-t", "--", "agent-rupan"])
        self.assertEqual(
            shlex.split(command[4]),
            [
                "exec",
                "$HOME/.local/bin/homelab-agent",
                "--serve",
                "/home/developer/workspace/a b",
                "services/a b",
                "codex",
                *args,
            ],
        )

    def test_metadata_automation_and_override_keep_native_behavior(self) -> None:
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch.dict("os.environ", {"HOMELAB_AGENT_LOCAL": ""}),
        ):
            self.assertFalse(native_command("codex", ["--yolo"]))
            self.assertFalse(native_command("codex", ["-p", "work"]))
            self.assertTrue(native_command("codex", ["--version"]))
            self.assertTrue(native_command("codex", ["exec", "hello"]))
            self.assertTrue(native_command("claude", ["auth", "status"]))
            self.assertTrue(native_command("claude", ["-p", "hello"]))
        with patch("sys.stdin.isatty", return_value=False):
            self.assertTrue(native_command("codex", []))
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch.dict("os.environ", {"HOMELAB_AGENT_LOCAL": "1"}),
        ):
            self.assertTrue(native_command("codex", []))


if __name__ == "__main__":
    unittest.main()
