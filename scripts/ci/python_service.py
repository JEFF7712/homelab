from __future__ import annotations

import re
from pathlib import Path


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
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.upper().startswith("FROM"):
            if "@sha256:" not in stripped:
                raise ValueError(f"Base image must be digest-pinned: {stripped}")
            if "registry.rupan.dev/upstream/" not in stripped:
                raise ValueError(
                    f"Base image must use local upstream mirror: {stripped}"
                )
    has_requirements = (root / "requirements.txt").is_file()
    has_pyproject = (root / "pyproject.toml").is_file()
    has_uvlock = (root / "uv.lock").is_file()
    if not (has_requirements or has_pyproject):
        raise ValueError("Python service requires requirements.txt or pyproject.toml")
    if has_pyproject and not has_uvlock:
        raise ValueError("pyproject.toml services require committed uv.lock")
    for name in ("docker-compose.yaml", "docker-compose.yml"):
        if (root / name).is_file():
            compose = (root / name).read_text(encoding="utf-8")
            if "ghcr.io/jeff7712" in compose.lower() or "type=gha" in compose:
                raise ValueError(
                    f"{name} must not reference external producer cache or GHCR"
                )
    github = root / ".github" / "workflows"
    if github.is_dir():
        for workflow in github.glob("*.yml"):
            content = workflow.read_text(encoding="utf-8")
            if "actions/checkout" in content and "forgejo" not in content.lower():
                pass


if __name__ == "__main__":
    validate(Path.cwd())
    print("Python service source and local-base contract validated")
