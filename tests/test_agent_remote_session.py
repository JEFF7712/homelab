from __future__ import annotations

import json
import os
import shlex
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.agent.remote_session import (
    main,
    mapping_for,
    native_command,
    server_available,
    ssh_command,
)


class RemoteSessionTests(unittest.TestCase):
    def test_unavailable_server_launches_local_client_with_original_arguments(
        self,
    ) -> None:
        for client in ("codex", "claude"):
            with self.subTest(client=client):
                args = ["--model", "model name", "inspect this repo"]
                with (
                    patch(
                        "scripts.agent.remote_session.native_command",
                        return_value=False,
                    ),
                    patch(
                        "scripts.agent.remote_session.mapping_for",
                        return_value=("agent-rupan", Path("/server/repo"), Path(".")),
                    ),
                    patch(
                        "scripts.agent.remote_session.server_available",
                        return_value=False,
                    ),
                    patch("os.execvp", side_effect=SystemExit) as execute,
                    patch("sys.stderr") as stderr,
                ):
                    cwd = Path.cwd()
                    with self.assertRaises(SystemExit):
                        main([client, *args])
                    execute.assert_called_once_with(client, [client, *args])
                    self.assertEqual(Path.cwd(), cwd)
                    message = "".join(
                        call.args[0] for call in stderr.write.call_args_list
                    )
                    self.assertIn(f"running {client} locally in {cwd}", message)
                    self.assertIn("not synchronized", message)

    def test_reachable_server_uses_ssh_without_fallback_if_launch_fails(self) -> None:
        with (
            patch("scripts.agent.remote_session.native_command", return_value=False),
            patch(
                "scripts.agent.remote_session.mapping_for",
                return_value=("agent-rupan", Path("/server/repo"), Path(".")),
            ),
            patch("scripts.agent.remote_session.server_available", return_value=True),
            patch("os.execvp", side_effect=OSError("SSH launch failed")) as execute,
            patch.dict("os.environ"),
            patch("sys.stderr"),
        ):
            with self.assertRaisesRegex(OSError, "SSH launch failed"):
                main(["codex", "--yolo"])
            self.assertEqual(execute.call_count, 1)
            self.assertEqual(execute.call_args.args[0], "ssh")

    def test_ssh_probe_success_failure_and_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ssh = Path(directory) / "ssh"
            for body, expected in (
                ("exit 0", True),
                ("exit 255", False),
                ("sleep 30", False),
            ):
                with self.subTest(body=body):
                    ssh.write_text(f"#!/bin/sh\n{body}\n")
                    ssh.chmod(0o700)
                    with (
                        patch.dict(
                            "os.environ",
                            {"PATH": directory + os.pathsep + os.environ["PATH"]},
                        ),
                        patch("scripts.agent.remote_session.SSH_PROBE_TIMEOUT", 0.2),
                    ):
                        started = time.monotonic()
                        self.assertEqual(server_available("test-server"), expected)
                        self.assertLess(time.monotonic() - started, 3)

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
