from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


def refs(repository: Path, remote: str | None = None) -> dict[str, str]:
    command = ["git", "-C", str(repository)]
    command += (
        ["ls-remote", remote, "refs/heads/*", "refs/tags/*"]
        if remote
        else [
            "for-each-ref",
            "--format=%(objectname) %(refname)",
            "refs/heads",
            "refs/tags",
        ]
    )
    output = subprocess.check_output(command, text=True)
    return {
        name: sha
        for sha, name in (line.split() for line in output.splitlines())
        if not name.endswith("^{}")
    }


def mirror(repository: Path, remote: str) -> None:
    source = refs(repository)
    if not source:
        raise ValueError("Refusing to replicate an empty repository")
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "push",
            "--atomic",
            remote,
            "refs/heads/*:refs/heads/*",
            "refs/tags/*:refs/tags/*",
        ],
        check=True,
    )
    destination = refs(repository, remote)
    if any(destination.get(name) != sha for name, sha in source.items()):
        raise ValueError("Recovery mirror ref verification failed")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="dr-mirror-") as temporary:
        helper = Path(temporary) / "credential"
        helper.write_text(
            '#!/usr/bin/env bash\nif [[ $1 == get ]]; then\n  printf "%s\\n" username=oauth2 "password=$GITLAB_MIRROR_TOKEN"\nfi\n'
        )
        helper.chmod(0o700)
        os.environ.update(
            GIT_TERMINAL_PROMPT="0",
            GIT_CONFIG_COUNT="2",
            GIT_CONFIG_KEY_0="credential.helper",
            GIT_CONFIG_VALUE_0="",
            GIT_CONFIG_KEY_1="credential.https://gitlab.com.helper",
            GIT_CONFIG_VALUE_1=str(helper),
        )
        mirror(
            Path("/tank/forgejo/repositories/jeff7712/homelab.git"),
            "https://gitlab.com/JEFF7712/homelab.git",
        )


if __name__ == "__main__":
    main()
