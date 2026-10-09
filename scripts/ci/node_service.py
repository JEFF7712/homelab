from __future__ import annotations

from pathlib import Path


def validate(root: Path) -> None:
    root = root.resolve()
    dockerfile = None
    for candidate in [
        root / "Dockerfile",
        root / "docker" / "Dockerfile",
        root / "site" / "Dockerfile",
        root / "quartz" / "Dockerfile",
        root / "config" / "Dockerfile",
    ]:
        if candidate.is_file():
            dockerfile = candidate
            break
    if dockerfile is None:
        raise ValueError("Node service requires Dockerfile")
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
    has_package = any(
        (root / p).is_file()
        for p in ["package.json", "site/package.json", "quartz/package.json"]
    )
    if not has_package:
        raise ValueError("Node service requires package.json")
    has_lock = any(
        (root / p).is_file()
        for p in [
            "package-lock.json",
            "bun.lock",
            "pnpm-lock.yaml",
            "yarn.lock",
            "site/package-lock.json",
            "site/pnpm-lock.yaml",
            "quartz/package-lock.json",
        ]
    )
    if not has_lock:
        raise ValueError("Node service requires committed package lockfile")
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
            if "ghcr.io/jeff7712" in content.lower():
                raise ValueError(
                    f"{workflow.name} must not reference external producer cache or GHCR"
                )


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    validate(target)
    print("Node service source and local-base contract validated")
