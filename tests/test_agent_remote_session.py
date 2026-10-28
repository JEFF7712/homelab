from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.agent.remote_session import (
    claude_trusted,
    create_seed,
    main,
    mapping_for,
    native_command,
    receive_seed,
    serve,
    server_available,
    ssh_command,
)


class RemoteSessionTests(unittest.TestCase):
    def git(self, root: Path, *args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(root), *args], text=True
        ).strip()

    def test_each_launch_is_a_fresh_session_with_a_client_managed_worktree(
        self,
    ) -> None:
        cases = {
            "codex": (["--model", "m"], ["--worktree", "--model", "m"]),
            "claude": (["--model", "m"], ["--worktree", "--model", "m"]),
        }
        explicit = {
            "codex": ["--worktree=named"],
            "claude": ["-w", "named"],
        }
        with tempfile.TemporaryDirectory() as directory:
            remote = Path(directory) / "repo"
            self.git(Path(directory), "init", "-q", str(remote))
            self.git(
                remote,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.test",
                "commit",
                "--allow-empty",
                "-qm",
                "Initial",
            )
            run = subprocess.run
            for client, (given, expected) in cases.items():
                sessions: list[str] = []
                commands: list[list[str]] = []

                def intercept(
                    command: list[str], sessions=sessions, commands=commands, **kwargs
                ):
                    if command[0] != "tmux":
                        return run(command, **kwargs)
                    self.assertEqual(command[1], "new-session")
                    sessions.append(command[command.index("-s") + 1])
                    commands.append(command)
                    return subprocess.CompletedProcess(command, 0)

                with (
                    patch(
                        "scripts.agent.remote_session.subprocess.run",
                        side_effect=intercept,
                    ),
                    patch(
                        "scripts.agent.remote_session.shutil.which",
                        return_value=f"/bin/{client}",
                    ),
                    patch(
                        "scripts.agent.remote_session.claude_trusted",
                        return_value=True,
                    ),
                    patch("os.execvp", side_effect=SystemExit) as execute,
                ):
                    for arguments in (given, given, explicit[client]):
                        with self.assertRaises(SystemExit):
                            serve(remote, Path("."), client, arguments)
                        self.assertEqual(execute.call_args.args[1][-1], sessions[-1])

                self.assertEqual(len(set(sessions)), 3)
                self.assertTrue(all(f"-{client}-" in name for name in sessions))
                for command in commands:
                    self.assertEqual(Path(command[command.index("-c") + 1]), remote)
                    self.assertEqual(command[command.index("--") + 1], f"/bin/{client}")
                self.assertEqual(commands[0][-len(expected) :], expected)
                self.assertEqual(
                    commands[2][commands[2].index("--") + 2 :], explicit[client]
                )
                self.assertEqual(
                    len(self.git(remote, "worktree", "list").splitlines()), 1
                )

    def test_untrusted_claude_project_starts_once_without_a_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            remote = Path(directory)
            commands: list[list[str]] = []
            with (
                patch(
                    "scripts.agent.remote_session.subprocess.run",
                    side_effect=lambda command, **kwargs: commands.append(command),
                ),
                patch(
                    "scripts.agent.remote_session.shutil.which",
                    return_value="/bin/claude",
                ),
                patch(
                    "scripts.agent.remote_session.claude_trusted", return_value=False
                ),
                patch("os.execvp", side_effect=SystemExit),
                self.assertRaises(SystemExit),
            ):
                serve(remote, Path("."), "claude", ["--model", "m"])
            self.assertEqual(commands[0][-3:], ["/bin/claude", "--model", "m"])

    def test_claude_trust_is_read_per_directory_from_its_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            (config / ".claude.json").write_text(
                json.dumps(
                    {
                        "projects": {
                            "/trusted": {"hasTrustDialogAccepted": True},
                            "/declined": {"hasTrustDialogAccepted": False},
                        }
                    }
                )
            )
            with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": directory}):
                self.assertTrue(claude_trusted(Path("/other"), Path("/trusted")))
                self.assertFalse(claude_trusted(Path("/declined")))
                self.assertFalse(claude_trusted(Path("/trusted/nested")))
            with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": f"{directory}/none"}):
                self.assertFalse(claude_trusted(Path("/trusted")))

    def test_project_root_routes_new_nested_and_non_git_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projects = root / "projects"
            plain = projects / "plain" / "src"
            nested = projects / "sites" / "web" / "src"
            plain.mkdir(parents=True)
            nested.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(nested.parent)], check=True)
            config = root / "repos.json"
            config.write_text(
                json.dumps(
                    {
                        "roots": [
                            {
                                "local": str(projects),
                                "remote": "/server/projects",
                                "host": "agent-rupan",
                            }
                        ]
                    }
                )
            )
            self.assertEqual(
                mapping_for(plain, config),
                ("agent-rupan", Path("/server/projects/plain/.checkout"), Path("src")),
            )
            self.assertEqual(
                mapping_for(nested, config),
                (
                    "agent-rupan",
                    Path("/server/projects/sites/web/.checkout"),
                    Path("src"),
                ),
            )
            self.assertIsNone(mapping_for(projects, config))
            outside = root / "outside"
            outside.mkdir()
            (projects / "link").symlink_to(outside, target_is_directory=True)
            self.assertIsNone(mapping_for(projects / "link", config))
            new = projects / "created-later"
            new.mkdir()
            self.assertEqual(
                mapping_for(new, config),
                (
                    "agent-rupan",
                    Path("/server/projects/created-later/.checkout"),
                    Path("."),
                ),
            )

    def test_seed_preserves_git_history_and_dirty_source_in_both_clients(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "local"
            local.mkdir()
            subprocess.run(
                ["git", "init", "-q", "-b", "feature", str(local)], check=True
            )
            (local / "code.py").write_text("original\n")
            (local / "deleted.py").write_text("delete me\n")
            (local / "binary").write_bytes(b"old\0binary")
            self.git(local, "add", "code.py", "deleted.py", "binary")
            self.git(
                local,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.test",
                "commit",
                "-qm",
                "Initial",
            )
            head = self.git(local, "rev-parse", "HEAD")
            self.git(
                local,
                "remote",
                "add",
                "origin",
                "https://user:password@example.test/repo.git?token=secret",
            )
            (local / "code.py").write_text("working changes\n")
            self.git(local, "add", "code.py")
            (local / "deleted.py").unlink()
            (local / "binary").write_bytes(b"new\0binary")
            (local / "untracked.py").write_text("new source\n")
            (local / ".env").write_text("secret")
            (local / "node_modules").mkdir()
            (local / "node_modules/dependency.js").write_text("generated")
            (local / "internal-link").symlink_to("untracked.py")
            (local / "outside-link").symlink_to(root / "outside")
            before = self.git(local, "status", "--porcelain")
            archive = root / "seed.tar"
            create_seed(local, archive)
            remote = root / "server/project/.checkout"
            with archive.open("rb") as stream:
                receive_seed(remote, stream)
            self.assertEqual(self.git(remote, "rev-parse", "HEAD"), head)
            self.assertEqual(
                self.git(remote, "remote", "get-url", "origin"),
                "https://example.test/repo.git",
            )
            self.assertEqual((remote / "code.py").read_text(), "working changes\n")
            self.assertEqual((remote / "binary").read_bytes(), b"new\0binary")
            self.assertFalse((remote / "deleted.py").exists())
            self.assertEqual((remote / "untracked.py").read_text(), "new source\n")
            self.assertTrue((remote / "internal-link").is_symlink())
            self.assertFalse((remote / ".env").exists())
            self.assertFalse((remote / "node_modules").exists())
            self.assertEqual(
                self.git(remote, "check-ignore", ".claude/worktrees/session"),
                ".claude/worktrees/session",
            )
            self.assertEqual(self.git(local, "rev-parse", "HEAD"), head)
            self.assertEqual(self.git(local, "status", "--porcelain"), before)
            (remote / "code.py").write_text("server edits in base\n")
            with archive.open("rb") as stream:
                receive_seed(remote, stream)
            self.assertEqual((remote / "code.py").read_text(), "server edits in base\n")
            occupied = root / "existing-directory"
            occupied.mkdir()
            with archive.open("rb") as stream:
                with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
                    receive_seed(occupied, stream)

    def test_non_git_seed_is_independent_and_excludes_local_environments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "plain"
            local.mkdir()
            (local / "main.py").write_text("source\n")
            (local / ".env.example").write_text("safe template")
            (local / ".env.local").write_text("secret")
            (local / ".venv").mkdir()
            (local / ".venv/python").write_text("local environment")
            archive = root / "seed.tar"
            create_seed(local, archive)
            remote = root / "remote/.checkout"
            with archive.open("rb") as stream:
                receive_seed(remote, stream)
            self.assertEqual((remote / "main.py").read_text(), "source\n")
            self.assertTrue((remote / ".env.example").exists())
            self.assertFalse((remote / ".env.local").exists())
            self.assertFalse((remote / ".venv").exists())
            self.assertFalse((local / ".git").exists())

    def test_unborn_git_seed_keeps_staged_source_without_committing_locally(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "new"
            subprocess.run(["git", "init", "-q", str(local)], check=True)
            (local / "source.py").write_text("new project\n")
            self.git(local, "add", "source.py")
            archive = root / "seed.tar"
            create_seed(local, archive)
            remote = root / "server/.checkout"
            with archive.open("rb") as stream:
                receive_seed(remote, stream)
            self.assertEqual(
                (remote / "source.py").read_text(),
                "new project\n",
            )
            self.assertEqual(self.git(local, "status", "--porcelain"), "A  source.py")

    def test_shallow_history_can_be_imported_without_fetching_upstream(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            origin = root / "origin"
            subprocess.run(["git", "init", "-q", str(origin)], check=True)
            for message in ("First", "Second"):
                self.git(
                    origin,
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.test",
                    "commit",
                    "--allow-empty",
                    "-qm",
                    message,
                )
            local = root / "shallow"
            subprocess.run(
                ["git", "clone", "-q", "--depth", "1", origin.as_uri(), str(local)],
                check=True,
            )
            archive = root / "seed.tar"
            create_seed(local, archive)
            remote = root / "server/.checkout"
            with archive.open("rb") as stream:
                receive_seed(remote, stream)
            self.assertEqual(
                self.git(remote, "rev-parse", "HEAD"),
                self.git(local, "rev-parse", "HEAD"),
            )
            self.assertEqual(
                self.git(remote, "rev-parse", "--is-shallow-repository"), "true"
            )

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
