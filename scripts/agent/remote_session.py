#!/usr/bin/env python3
"""Opt selected checkouts into persistent interactive agent sessions over SSH."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path
from typing import BinaryIO, NoReturn
from urllib.parse import urlsplit, urlunsplit

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
SSH_PROBE_TIMEOUT = 8
SOURCE_EXCLUDES = {
    ".git",
    ".worktrees",
    "worktrees",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".direnv",
    ".cache",
    ".next",
    ".nuxt",
    "target",
    "dist",
    "build",
    "result",
    ".stfolder",
    ".stversions",
}


def source_file(path: Path) -> bool:
    return not (
        set(path.parts) & SOURCE_EXCLUDES
        or path.name == ".env"
        or (
            path.name.startswith(".env.")
            and path.suffix not in {".example", ".sample", ".template"}
        )
        or path.name in {"credentials.json", "auth.json", "id_rsa", "id_ed25519"}
        or path.suffix in {".pem", ".key"}
    )


def source_files(local: Path, git: bool, include_tracked: bool = False) -> list[Path]:
    if git:
        names = subprocess.check_output(
            [
                "git",
                "-C",
                str(local),
                "ls-files",
                *(["--cached"] if include_tracked else []),
                "--others",
                "--exclude-standard",
                "-z",
            ]
        ).split(b"\0")
        paths = [Path(os.fsdecode(name)) for name in names if name]
    else:
        paths = []
        for directory, dirs, files in os.walk(local):
            dirs[:] = [
                name
                for name in dirs
                if source_file(Path(name)) and not (Path(directory) / name).is_symlink()
            ]
            paths.extend((Path(directory) / name).relative_to(local) for name in files)
    return [
        path
        for path in sorted(set(paths))
        if source_file(path)
        and (local / path).is_file()
        and (local / path).resolve().is_relative_to(local.resolve())
    ]


def create_seed(local: Path, archive: Path) -> None:
    head = subprocess.run(
        ["git", "-C", str(local), "rev-parse", "--verify", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    is_repo = (local / ".git").exists()
    with tempfile.TemporaryDirectory(prefix="homelab-source-") as directory:
        staging = Path(directory)
        manifest = {
            "branch": "main",
            "origin": "",
            "head": head.stdout.strip() if head.returncode == 0 else "",
        }
        if manifest["head"]:
            shallow = Path(
                subprocess.check_output(
                    [
                        "git",
                        "-C",
                        str(local),
                        "rev-parse",
                        "--path-format=absolute",
                        "--git-path",
                        "shallow",
                    ],
                    text=True,
                ).strip()
            )
            if shallow.is_file():
                shutil.copy2(shallow, staging / "shallow")
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(local),
                    "bundle",
                    "create",
                    str(staging / "repo.bundle"),
                    "HEAD",
                ],
                check=True,
            )
            (staging / "changes.patch").write_bytes(
                subprocess.check_output(
                    [
                        "git",
                        "-C",
                        str(local),
                        "diff",
                        "--no-ext-diff",
                        "--no-textconv",
                        "--binary",
                        "HEAD",
                    ]
                )
            )
            branch = subprocess.run(
                ["git", "-C", str(local), "symbolic-ref", "--short", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
            manifest["branch"] = branch.stdout.strip() or "main"
        origin = subprocess.run(
            ["git", "-C", str(local), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False,
        )
        if is_repo and origin.returncode == 0:
            url = origin.stdout.strip()
            parts = urlsplit(url)
            if parts.scheme in {"http", "https"}:
                url = urlunsplit(
                    (parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", "")
                )
            manifest["origin"] = url
        (staging / "manifest.json").write_text(json.dumps(manifest))
        with tarfile.open(archive, "w") as tar:
            for path in staging.iterdir():
                tar.add(path, arcname=path.name)
            for path in source_files(local, is_repo, is_repo and not manifest["head"]):
                tar.add(
                    local / path, arcname=str(Path("files") / path), recursive=False
                )


def receive_seed(remote: Path, stream: BinaryIO) -> None:
    if not remote.is_absolute() or ".." in remote.parts:
        raise ValueError("Invalid remote project directory")
    remote.parent.mkdir(parents=True, exist_ok=True)
    with (remote.parent / ".seed.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if remote.exists():
            if (remote / ".git").is_dir():
                while stream.read(1024 * 1024):
                    pass
                return
            raise ValueError(f"Refusing to overwrite existing directory: {remote}")
        with tempfile.TemporaryDirectory(
            prefix=".seed-", dir=remote.parent
        ) as directory:
            staging = Path(directory)
            incoming = staging / "incoming"
            incoming.mkdir()
            with tarfile.open(fileobj=stream, mode="r|*") as tar:
                tar.extractall(incoming, filter="data")
            manifest = json.loads((incoming / "manifest.json").read_text())
            checkout = staging / "checkout"
            subprocess.run(
                ["git", "init", "-b", manifest["branch"], str(checkout)],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            git = ["git", "-C", str(checkout)]
            if manifest["head"]:
                if (incoming / "shallow").is_file():
                    shutil.copy2(incoming / "shallow", checkout / ".git/shallow")
                subprocess.run(
                    [*git, "fetch", str(incoming / "repo.bundle"), "HEAD"], check=True
                )
                subprocess.run(
                    [*git, "checkout", "-B", manifest["branch"], "FETCH_HEAD"],
                    check=True,
                )
                patch = incoming / "changes.patch"
                if patch.stat().st_size:
                    subprocess.run([*git, "apply", str(patch)], check=True)
            else:
                subprocess.run(
                    [
                        *git,
                        "-c",
                        "user.name=Rupan",
                        "-c",
                        "user.email=rupan@agent-rupan",
                        "commit",
                        "--allow-empty",
                        "-m",
                        "Initialize remote project workspace",
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                )
            if (incoming / "files").exists():
                shutil.copytree(
                    incoming / "files", checkout, dirs_exist_ok=True, symlinks=True
                )
            if manifest["origin"]:
                subprocess.run(
                    [*git, "remote", "add", "origin", manifest["origin"]], check=True
                )
            with (checkout / ".git/info/exclude").open("a") as excludes:
                excludes.write("\n.worktrees/\n.claude/worktrees/\n")
            (checkout / ".git/homelab-source.json").write_text(json.dumps(manifest))
            checkout.rename(remote)


def prepare_project(local: Path, host: str, remote: Path) -> None:
    def command(*args: str) -> list[str]:
        return [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "--",
            host,
            'exec "$HOME/.local/bin/homelab-agent" ' + shlex.join(args),
        ]

    ready = subprocess.run(
        command("--ready", str(remote)), stdin=subprocess.DEVNULL, check=False
    )
    if ready.returncode == 0:
        return
    if ready.returncode != 3:
        raise ValueError(
            f"Cannot initialize remote project {remote} (SSH exit {ready.returncode})"
        )
    print(
        f"Creating initial server copy of {local}, including uncommitted source. Later edits are not synchronized.",
        file=sys.stderr,
        flush=True,
    )
    with tempfile.TemporaryDirectory(prefix="homelab-project-") as directory:
        archive = Path(directory) / "seed.tar"
        create_seed(local, archive)
        with archive.open("rb") as stream:
            subprocess.run(command("--seed", str(remote)), stdin=stream, check=True)


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
    cwd = cwd.resolve()
    local = Path(result.stdout.strip()).resolve() if result.returncode == 0 else None
    settings = json.loads(config.read_text())
    for entry in settings.get("repos", []):
        if local is None or Path(entry["local"]).resolve() != local:
            continue
        host, remote = entry["host"], Path(entry["remote"])
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", host):
            raise ValueError("Invalid SSH host alias in remote agent configuration")
        if not remote.is_absolute() or ".." in remote.parts or "\0" in str(remote):
            raise ValueError("Remote checkout must be an absolute normalized path")
        return host, remote, cwd.relative_to(local)
    roots = sorted(
        settings.get("roots", []),
        key=lambda entry: len(Path(entry["local"]).parts),
        reverse=True,
    )
    for entry in roots:
        root = Path(entry["local"]).resolve()
        if cwd == root or not cwd.is_relative_to(root):
            continue
        project = (
            local
            if local is not None and local.is_relative_to(root)
            else root / cwd.relative_to(root).parts[0]
        )
        host, remote = entry["host"], Path(entry["remote"])
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", host):
            raise ValueError("Invalid SSH host alias in remote agent configuration")
        if not remote.is_absolute() or ".." in remote.parts or "\0" in str(remote):
            raise ValueError("Remote checkout must be an absolute normalized path")
        return (
            host,
            remote / project.relative_to(root) / ".checkout",
            cwd.relative_to(project),
        )
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


def server_available(host: str) -> bool:
    try:
        with subprocess.Popen(
            [
                "ssh",
                "-T",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=5",
                "-o",
                "ConnectionAttempts=1",
                "--",
                host,
                "true",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        ) as process:
            try:
                return process.wait(timeout=SSH_PROBE_TIMEOUT) == 0
            except subprocess.TimeoutExpired:
                # ProxyJump children must not outlive the bounded probe.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                return False
    except OSError:
        return False


def claude_trusted(*directories: Path) -> bool:
    """Whether Claude's trust dialog was accepted; `--worktree` refuses otherwise."""
    config = (
        Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home()))) / ".claude.json"
    )
    try:
        projects = json.loads(config.read_text()).get("projects", {})
    except (OSError, ValueError):
        return False
    return any(
        projects.get(str(directory), {}).get("hasTrustDialogAccepted") is True
        for directory in directories
    )


def serve(remote: Path, relative: Path, client: str, args: list[str]) -> NoReturn:
    remote = remote.resolve(strict=True)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Invalid checkout-relative directory")
    directory = (remote / relative).resolve()
    if not directory.is_relative_to(remote):
        raise ValueError("Working directory escapes the project checkout")
    # Every launch is an independent process in its own tmux session; the
    # client creates and owns the session's Git worktree.
    if not any(
        arg == "--worktree"
        or arg.startswith("--worktree=")
        or (client == "claude" and arg == "-w")
        for arg in args
    ):
        if client == "claude" and not claude_trusted(remote, directory):
            print(
                "Claude has not been trusted in this project yet, so this session "
                "runs in the project checkout. Accept the trust dialog; later "
                "launches use a fresh worktree.",
                flush=True,
            )
        else:
            args = ["--worktree", *args]
    session = (
        "agent-"
        + hashlib.sha256(str(remote).encode()).hexdigest()[:12]
        + f"-{client}-{uuid.uuid4().hex}"
    )
    directory.mkdir(parents=True, exist_ok=True)
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
    print(f"Server: {os.uname().nodename}:{directory} (tmux {session})", flush=True)
    os.execvp("tmux", ["tmux", "attach-session", "-t", session])


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 2 and argv[0] in {"--ready", "--seed"}:
        remote = Path(argv[1])
        if argv[0] == "--ready":
            if not remote.exists():
                sys.exit(3)
            if not (remote / ".git").is_dir():
                raise ValueError(
                    f"Existing directory is not a project checkout: {remote}"
                )
            sys.exit(0)
        receive_seed(remote, sys.stdin.buffer)
        return
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
    if not server_available(host):
        print(
            f"Server {host} unavailable; running {client} locally in {Path.cwd()}. "
            "Server changes and conversations are not synchronized.",
            file=sys.stderr,
            flush=True,
        )
        os.execvp(client, [client, *args])
    settings = json.loads(config.read_text()) if config.exists() else {}
    if any(
        Path.cwd().resolve().is_relative_to(Path(entry["local"]).resolve())
        for entry in settings.get("roots", [])
    ):
        local = Path.cwd().resolve()
        for _ in relative.parts:
            local = local.parent
        prepare_project(local, host, remote)
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
    except (
        ValueError,
        KeyError,
        OSError,
        subprocess.SubprocessError,
        tarfile.TarError,
    ) as error:
        print(f"homelab-agent: {error}", file=sys.stderr)
        sys.exit(1)
