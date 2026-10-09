from __future__ import annotations

import re
from pathlib import Path


def validate(root: Path) -> None:
    root = root.resolve()
    if not (root / "Makefile").is_file():
        raise ValueError("Firmware requires Makefile")
    if not (root / "flake.nix").is_file():
        raise ValueError("Firmware requires flake.nix")
    ino = (
        list((root / "bus_display").glob("*.ino"))
        if (root / "bus_display").is_dir()
        else []
    )
    if not ino:
        raise ValueError("Firmware requires bus_display/*.ino")
    modules = root / ".gitmodules"
    if not modules.is_file():
        raise ValueError("Firmware vendor checkouts require .gitmodules")
    content = modules.read_text(encoding="utf-8")
    if "waveshare-repo" not in content:
        raise ValueError(".gitmodules must map the waveshare-repo checkout")
    for path in sorted(root.rglob("*")):
        if path.suffix in {".pyc", ".pyo"} or path.name == "__pycache__":
            raise ValueError(f"Compiled artifacts must not be committed: {path}")
        if path.suffix in {".o", ".a", ".elf"} and "build" not in path.parts:
            raise ValueError(f"Build outputs must not be committed: {path}")
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    if re.search(r"https?://", makefile):
        raise ValueError("Makefile must not fetch remote URLs")


if __name__ == "__main__":
    validate(Path.cwd())
    print("Firmware source and vendor contract validated")
