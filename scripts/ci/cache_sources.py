from __future__ import annotations

import argparse
import json
from pathlib import Path


def source_paths(archive: object) -> list[str]:
    paths: set[str] = set()

    def collect(entry: object, name: str) -> None:
        if not isinstance(entry, dict):
            raise ValueError(f"Flake archive entry {name} must be an object")
        path = entry.get("path")
        if (
            not isinstance(path, str)
            or Path(path).parent != Path("/nix/store")
            or any(character.isspace() for character in path)
        ):
            raise ValueError(f"Flake archive entry {name} has no valid store path")
        paths.add(path)
        inputs = entry.get("inputs", {})
        if not isinstance(inputs, dict):
            raise ValueError(f"Flake archive entry {name} has invalid inputs")
        for key, child in inputs.items():
            collect(child, f"{name}/{key}")

    collect(archive, "root")
    return sorted(paths)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List recursively archived flake sources"
    )
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    for path in source_paths(json.loads(args.archive.read_text())):
        print(path)


if __name__ == "__main__":
    main()
