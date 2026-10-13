from __future__ import annotations

import re
from pathlib import Path

from scripts.ci.dockerfile_supply import (
    check_copy_sources,
    check_run_lines,
    parse_dockerfile,
)


def validate(root: Path) -> None:
    root = root.resolve()
    dockerfile = root / "docker" / "Dockerfile"
    if not dockerfile.is_file():
        raise ValueError("Dotnet service requires docker/Dockerfile")
    text = dockerfile.read_text(encoding="utf-8")
    sdk_bases = tuple(
        re.findall(
            r"^\s*FROM\s+(registry\.rupan\.dev/apps/dotnet-sdk-6@sha256:[0-9a-f]{64})(?:\s+AS\s+[A-Za-z0-9_.-]+)?\s*$",
            text,
            re.MULTILINE | re.IGNORECASE,
        )
    )
    supply = parse_dockerfile(dockerfile, additional_bases=sdk_bases)
    if not supply.bases:
        raise ValueError("Dockerfile declares no base image")
    check_copy_sources(supply)
    check_run_lines(text)
    if not (root / "src").is_dir():
        raise ValueError("Dotnet service requires src/")
    if not (root / "frontend").is_dir():
        raise ValueError("Dotnet service requires frontend/")


if __name__ == "__main__":
    validate(Path.cwd())
    print("Dotnet service source and local-supply contract validated")
