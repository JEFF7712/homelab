from __future__ import annotations

import re
from pathlib import Path

from scripts.ci.dockerfile_supply import (
    check_copy_sources,
    parse_dockerfile,
)

PUBLIC_NPM_REGISTRIES = (
    "https://registry.npmjs.org",
    "http://registry.npmjs.org",
    "https://registry.yarnpkg.com",
    "http://registry.yarnpkg.com",
)


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
    supply = parse_dockerfile(dockerfile)
    if not supply.bases:
        raise ValueError("Dockerfile declares no base image")
    check_copy_sources(supply)
    if "type=gha" in text:
        raise ValueError(
            f"{dockerfile.name} must not use external GitHub cache storage"
        )
    for registry in PUBLIC_NPM_REGISTRIES:
        if registry in text:
            raise ValueError(
                f"{dockerfile.name} must not reference public npm registry {registry}"
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
            if "type=gha" in compose:
                raise ValueError(f"{name} must not use external GitHub cache")
            if re.search(r"ghcr\.io/jeff7712", compose, re.IGNORECASE):
                raise ValueError(f"{name} must not reference external GHCR images")
    github = root / ".github" / "workflows"
    if github.is_dir():
        for workflow in sorted(github.glob("*.yml")) + sorted(github.glob("*.yaml")):
            content = workflow.read_text(encoding="utf-8")
            if "registry.rupan.dev" in content:
                raise ValueError(
                    f"{workflow.name} must not publish to the local registry; "
                    "delivery belongs to Woodpecker"
                )
            if "type=gha" in content:
                raise ValueError(f"{workflow.name} must not use external GitHub cache")


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    validate(target)
    print("Node service source and local-base contract validated")
