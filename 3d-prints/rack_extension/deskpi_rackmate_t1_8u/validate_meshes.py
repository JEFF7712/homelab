"""Check exported print meshes with trimesh and a graph backend installed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import trimesh


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    report = {}
    for path in sorted(args.directory.glob("*.stl")):
        mesh = trimesh.load_mesh(path)
        report[path.stem] = {
            "watertight": bool(mesh.is_watertight),
            "winding_consistent": bool(mesh.is_winding_consistent),
            "components": len(mesh.split(only_watertight=False)),
            "volume_mm3": float(mesh.volume),
        }
    (args.directory / "mesh_checks.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    failed = [
        name
        for name, data in report.items()
        if not data["watertight"]
        or not data["winding_consistent"]
        or data["components"] != 1
        or data["volume_mm3"] <= 0
    ]
    print(f"Checked {len(report)} STLs; failures: {failed}")
    if failed or not report:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
