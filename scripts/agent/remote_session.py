#!/usr/bin/env python3
"""Opt selected checkouts into persistent interactive agent sessions over SSH."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

CLIENTS = {"codex", "claude"}
LOCAL_COMMANDS = {
    "codex": {
        "exec",
        "review",
        "login",
        "logout",
        "mcp",
        "app",
        "app-server",
        "completion",
        "debug",
        "help",
    },
    "claude": {"auth", "mcp", "doctor", "update", "install", "setup-token", "help"},
}
LOCAL_FLAGS = {"-h", "--help", "-V", "--version"}


def native_command(client: str, args: list[str]) -> bool:
    return (
        os.environ.get("HOMELAB_AGENT_LOCAL") == "1"
        or not sys.stdin.isatty()
        or bool(
            set(args)
            & (LOCAL_FLAGS | ({"-p", "--print"} if client == "claude" else set()))
        )
        or bool(args and args[0] in LOCAL_COMMANDS[client])
    )


def mapping_for(cwd: Path, config: Path) -> tuple[str, Path, Path] | None:
    if not config.exists():
        return None
    result = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return None
    local = Path(result.stdout.strip()).resolve()
    for entry in json.loads(config.read_text())["repos"]:
        if Path(entry["local"]).resolve() != local:
            continue
        host, remote = entry["host"], Path(entry["remote"])
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", host):
            raise ValueError("Invalid SSH host alias in remote agent configuration")
        if not remote.is_absolute() or ".." in remote.parts or "\0" in str(remote):
            raise ValueError("Remote checkout must be an absolute normalized path")
        return host, remote, cwd.resolve().relative_to(local)
    return None


def ssh_command(
    host: str, remote: Path, relative: Path, client: str, args: list[str]
) -> list[str]:
    command = ["--serve", str(remote), str(relative), client, *args]
    return [
        "ssh",
        "-t",
        "--",
        host,
        'exec "$HOME/.local/bin/homelab-agent" ' + shlex.join(command),
    ]


def serve(remote: Path, relative: Path, client: str, args: list[str]) -> NoReturn:
    remote = remote.resolve(strict=True)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Invalid checkout-relative directory")
    worktree = remote / ".worktrees" / f"interactive-{client}"
    if not worktree.exists():
        subprocess.run(
            [
                "git",
                "-C",
                str(remote),
                "worktree",
                "add",
                "-b",
                f"interactive/{client}",
                str(worktree),
                "HEAD",
            ],
            check=True,
        )
    common = subprocess.check_output(
        [
            "git",
            "-C",
            str(worktree),
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ],
        text=True,
    ).strip()
    if Path(common).resolve() != (remote / ".git").resolve():
        raise ValueError("Existing agent worktree belongs to a different repository")
    session = (
        "agent-" + hashlib.sha256(str(remote).encode()).hexdigest()[:12] + "-" + client
    )
    exists = subprocess.run(
        ["tmux", "has-session", "-t", session], capture_output=True, check=False
    )
    if exists.returncode:
        directory = (worktree / relative).resolve(strict=True)
        if not directory.is_relative_to(worktree.resolve()):
            raise ValueError("Working directory escapes the agent worktree")
        binary = shutil.which(client)
        if not binary:
            raise ValueError(f"{client} is not installed on the server")
        subprocess.run(
            [
                "tmux",
                "new-session",
                "-d",
                "-s",
                session,
                "-c",
                str(directory),
                "--",
                binary,
                *args,
            ],
            check=True,
        )
    else:
        directory = Path(
            subprocess.check_output(
                [
                    "tmux",
                    "display-message",
                    "-p",
                    "-t",
                    session,
                    "#{pane_current_path}",
                ],
                text=True,
            ).strip()
        )
        print(
            "Reconnecting to the existing session; startup arguments apply only to new sessions.",
            flush=True,
        )
    print(f"Server: {os.uname().nodename}:{directory} (tmux {session})", flush=True)
    os.execvp("tmux", ["tmux", "attach-session", "-t", session])


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--serve":
        if len(argv) < 4 or argv[3] not in CLIENTS:
            raise ValueError(
                "Usage: homelab-agent --serve CHECKOUT RELATIVE_DIR CLIENT [ARGS...]"
            )
        serve(Path(argv[1]), Path(argv[2]), argv[3], argv[4:])
    if not argv or argv[0] not in CLIENTS:
        raise ValueError("Usage: homelab-agent codex|claude [ARGS...]")
    client, args = argv[0], argv[1:]
    config = (
        Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        / "homelab-agent/repos.json"
    )
    mapping = None if native_command(client, args) else mapping_for(Path.cwd(), config)
    if mapping is None:
        os.execvp(client, [client, *args])
    host, remote, relative = mapping
    print(
        f"Remote {client}: {host}:{remote}. Laptop files are not synchronized.",
        file=sys.stderr,
        flush=True,
    )
    os.environ["TERM"] = "xterm-256color"
    os.execvp("ssh", ssh_command(host, remote, relative, client, args))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        print(f"homelab-agent: {error}", file=sys.stderr)
        sys.exit(1)
