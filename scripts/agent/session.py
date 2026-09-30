from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .git_state import collect_git_state
from .tasks import TaskError, resume_task

CLIENTS = {
    "codex": "codex",
    "claude": "claude",
    "cursor": "agent",
    "opencode": "opencode",
    "antigravity": "agy",
    "muse": "muse",
}


@contextmanager
def client_environment(
    root: Path, task_id: str, client: str, *, trace: bool = False
) -> Iterator[dict[str, str]]:
    environment = {**os.environ, "AGENT_TASK_ID": task_id, "AGENT_HOOK_CLIENT": client}
    if trace:
        environment["AGENT_HOOK_TRACE"] = "1"
    if client not in {"antigravity", "muse"}:
        yield environment
        return
    directory = root / ".agent-state/client-sessions"
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{client}-", dir=directory) as temporary:
        bridge = Path(temporary) / "homelab-agent-session"
        metadata = json.dumps(
            {
                "schema_version": 1,
                "root": str(root.resolve()),
                "task": task_id,
                "client": client,
                "trace": environment.get("AGENT_HOOK_TRACE", "0"),
            }
        )
        # The hooks exec this bridge rather than sourcing it, so the shebang has
        # to name an interpreter the kernel can actually open. A
        # `#!/usr/bin/env bash` line depends on /usr/bin/env, which NixOS
        # provides for compatibility but a build sandbox or a slim container
        # does not, and the failure is silent: hook_restore_session gives up and
        # every hook falls open as if no task were selected.
        interpreter = shutil.which("bash") or "/bin/bash"
        bridge.write_text(
            f"#!{interpreter}\nprintf '%s\\n' " + shlex.quote(metadata) + "\n"
        )
        bridge.chmod(0o700)
        environment["PATH"] = f"{temporary}:{environment.get('PATH', os.defpath)}"
        yield environment


def run_client(root: Path, task_id: str, client: str, arguments: list[str]) -> int:
    inspection = resume_task(root, task_id)
    if inspection["task"]["status"] not in {"active", "blocked"}:
        raise TaskError("client sessions require an active or blocked task")
    state = collect_git_state(root)
    command = [CLIENTS[client], *arguments]
    with client_environment(state.root, task_id, client) as environment:
        result = subprocess.run(command, cwd=state.root, env=environment, check=False)
    return result.returncode
