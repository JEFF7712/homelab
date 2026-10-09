from __future__ import annotations

import re
from pathlib import Path

from scripts.ci.dockerfile_supply import (
    check_copy_sources,
    check_run_lines,
    parse_dockerfile,
)

PINNED = re.compile(r"^[A-Za-z0-9_.-]+==[^;\s]+(?:;.*)?$")
OPTION = re.compile(r"^\s*(-f|--find-links|--index-url|--extra-index-url|--|-[a-zA-Z])")


def _check_requirements(path: Path) -> None:
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("--hash"):
            continue
        if OPTION.match(line):
            if re.search(r"https?://", line):
                raise ValueError(
                    f"{path.name}:{number} must not reference remote indexes"
                )
            if re.match(r"^\s*(-f|--find-links)\s", line):
                target = line.split(None, 1)[1].strip().strip("\"'")
                if "://" in target or target.startswith("/"):
                    raise ValueError(
                        f"{path.name}:{number} find-links must be a repo-local path"
                    )
                continue
            raise ValueError(
                f"{path.name}:{number} options are not allowed except local --find-links"
            )
        if "://" in line:
            raise ValueError(f"{path.name}:{number} must not reference remote URLs")
        if not PINNED.match(line):
            raise ValueError(
                f"{path.name}:{number} dependencies must be pinned with ==: {line}"
            )


def validate(root: Path) -> None:
    root = root.resolve()
    dockerfile = root / "Dockerfile"
    if not dockerfile.is_file():
        alt = root / "config" / "Dockerfile"
        if alt.is_file():
            dockerfile = alt
        else:
            raise ValueError("Python service requires Dockerfile or config/Dockerfile")
    text = dockerfile.read_text(encoding="utf-8")
    supply = parse_dockerfile(dockerfile)
    if not supply.bases:
        raise ValueError("Dockerfile declares no base image")
    check_copy_sources(supply)
    check_run_lines(text)
    requirements = root / "requirements.txt"
    if requirements.is_file():
        _check_requirements(requirements)
    else:
        if not (root / "pyproject.toml").is_file():
            raise ValueError(
                "Python service requires requirements.txt or pyproject.toml"
            )
        if not (root / "uv.lock").is_file():
            raise ValueError("pyproject.toml services require committed uv.lock")
    for name in ("docker-compose.yaml", "docker-compose.yml"):
        candidate = root / name
        if candidate.is_file():
            compose = candidate.read_text(encoding="utf-8")
            if "type=gha" in compose:
                raise ValueError(f"{name} must not use external GitHub cache")
            if re.search(r"ghcr\.io/jeff7712", compose, re.IGNORECASE):
                raise ValueError(f"{name} must not reference external GHCR images")


if __name__ == "__main__":
    validate(Path.cwd())
    print("Python service source and local-supply contract validated")
