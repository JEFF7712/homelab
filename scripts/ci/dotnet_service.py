from __future__ import annotations

from pathlib import Path


def validate(root: Path) -> None:
    root = root.resolve()
    dockerfile = root / "docker" / "Dockerfile"
    if not dockerfile.is_file():
        raise ValueError("Dotnet service requires docker/Dockerfile")
    text = dockerfile.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.upper().startswith("FROM"):
            if "@sha256:" not in stripped:
                raise ValueError(f"Base image must be digest-pinned: {stripped}")
    if not (root / "src").is_dir():
        raise ValueError("Dotnet service requires src/")
    if not (root / "frontend").is_dir():
        raise ValueError("Dotnet service requires frontend/")


if __name__ == "__main__":
    validate(Path.cwd())
    print("Dotnet service source contract validated")
