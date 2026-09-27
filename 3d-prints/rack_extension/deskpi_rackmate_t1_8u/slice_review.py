"""Slice prototype STLs locally; never contacts or starts a printer."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def flatten(name: str, presets: dict[str, dict]) -> dict:
    source = presets[name]
    parent = source.get("inherits")
    result = flatten(parent, presets) if parent else {}
    result.update(source)
    result.pop("inherits", None)
    return result


def profiles(out: Path, root: Path) -> tuple[Path, Path, Path]:
    presets = {}
    for path in root.rglob("*.json"):
        data = json.loads(path.read_text())
        if "name" in data:
            presets[data["name"]] = data
    machine = flatten("Bambu Lab A1 0.4 nozzle", presets)
    process = flatten("0.20mm Standard @BBL A1", presets)
    filament = flatten("Generic PETG @BBL A1", presets)
    process.update(
        {
            "layer_height": "0.2",
            "initial_layer_print_height": "0.2",
            "wall_loops": "6",
            "top_shell_layers": "6",
            "bottom_shell_layers": "6",
            "sparse_infill_density": "25%",
            "sparse_infill_pattern": "gyroid",
            "outer_wall_speed": "45",
            "inner_wall_speed": "80",
            "sparse_infill_speed": "80",
            "top_surface_speed": "40",
            "bridge_speed": "20",
            "initial_layer_speed": "25",
            "default_acceleration": "2000",
            "brim_type": "outer_only",
            "brim_width": "3",
            "brim_object_gap": "0.15",
            "enable_support": "0",
            "support_type": "normal(auto)",
            "support_style": "snug",
            "support_on_build_plate_only": "1",
            "support_top_z_distance": "0.2",
            "curr_bed_type": "Textured PEI Plate",
        }
    )
    filament.update(
        {"nozzle_temperature": ["250"], "nozzle_temperature_initial_layer": ["250"]}
    )
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, data in [
        ("machine", machine),
        ("process", process),
        ("filament", filament),
    ]:
        path = out / f"{name}.json"
        path.write_text(json.dumps(data, indent=2) + "\n")
        paths.append(path)
    return paths[0], paths[1], paths[2]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parts", type=Path, default=Path("rev4"))
    parser.add_argument(
        "--presets", type=Path, default=Path.home() / ".config/OrcaSlicer/system/BBL"
    )
    parser.add_argument("names", nargs="+")
    args = parser.parse_args()
    parts = args.parts.resolve()
    output = parts / "slicing"
    machine, process_path, filament = profiles(output / "profiles", args.presets)
    for name in args.names:
        destination = output / name
        destination.mkdir(parents=True, exist_ok=True)
        settings = json.loads(process_path.read_text())
        if name.startswith(("mount_", "lower_", "upper_")):
            settings["enable_support"] = "1"
        if name.startswith("rear_"):
            settings["brim_width"] = "0"
        process = destination / "process.json"
        process.write_text(json.dumps(settings, indent=2) + "\n")
        command = [
            "orca-slicer",
            "--load-settings",
            f"{machine};{process}",
            "--load-filaments",
            str(filament),
            "--orient",
            "0",
            "--arrange",
            "1",
            "--slice",
            "0",
            "--export-3mf",
            f"{name}.3mf",
            "--outputdir",
            str(destination),
            str(parts / f"{name}.stl"),
        ]
        if name == "m4_pilot_coupon":
            command.append(str(parts / "m4_test_spacer.stl"))
        with (destination / "slice.log").open("w") as log:
            result = subprocess.run(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=destination,
                timeout=120,
                check=False,
            )
        print(f"{name}: exit {result.returncode}", flush=True)
        if result.returncode:
            raise SystemExit(result.returncode)
        if not (destination / "plate_1.gcode").is_file():
            raise RuntimeError(f"Slicer produced no toolpaths for {name}")
        (destination / "inputs.json").write_text(
            json.dumps(
                {
                    "stl_sha256": hashlib.sha256(
                        (parts / f"{name}.stl").read_bytes()
                    ).hexdigest(),
                    "process_sha256": hashlib.sha256(process.read_bytes()).hexdigest(),
                    "machine_sha256": hashlib.sha256(machine.read_bytes()).hexdigest(),
                    "filament_sha256": hashlib.sha256(
                        filament.read_bytes()
                    ).hexdigest(),
                },
                indent=2,
            )
            + "\n"
        )


if __name__ == "__main__":
    main()
