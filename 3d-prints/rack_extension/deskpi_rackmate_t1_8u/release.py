"""One positioned assembly and print-orientation manifest for the next prototype."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast

from build123d import (
    Align,
    Box,
    Compound,
    Cylinder,
    Location,
    Part,
    export_step,
    export_stl,
)

from . import bracing, column, model, params, rail, top


def printed_assembly() -> dict[str, Part]:
    parts: dict[str, Part] = {}
    for right in (False, True):
        suffix = "right" if right else "left"
        x = params.BODY_WIDTH_MM - params.COLUMN_INWARD_MM if right else 0
        for rear in (False, True):
            name = f"{'rear' if rear else 'front'}_{suffix}"
            y = params.BODY_DEPTH_MM - params.COLUMN_DEPTH_MM if rear else 0
            parts[f"mount_{name}"] = column.rev3_corner_mount(rear, right).moved(
                Location((x, y, 0))
            )
            parts[f"retainer_{name}"] = model.phase2_bottom_nut_retainer().moved(
                Location(
                    (x + params.BORE_CENTER_X_MM, y + params.BORE_CENTER_Y_MM, 0),
                    (0, 0, 180 if right else 0),
                )
            )
            lower = (
                column.rev3_lower_column_right()
                if right
                else column.rev3_lower_column_left()
            )
            upper = (
                column.rev3_upper_column_right()
                if right
                else column.rev3_upper_column_left()
            )
            parts[f"lower_{name}"] = lower.moved(
                Location((x, y, params.BOTTOM_BLOCK_TOP_Z_MM))
            )
            parts[f"upper_{name}"] = upper.moved(Location((x, y, params.SPLICE_Z_MM)))
        parts[f"rail_lower_{suffix}"] = rail.lower_equipment_rail(right)
        parts[f"rail_upper_{suffix}"] = rail.upper_equipment_rail(right)
        parts[f"side_restraint_{suffix}"] = bracing.side_restraint_bar(right)
        parts[f"top_side_beam_{suffix}"] = top.side_beam(right)
    parts["rear_lower_crossbar"] = bracing.rear_lower_crossbar()
    parts["rear_upper_crossbar"] = bracing.rear_upper_crossbar()
    for index, solid in enumerate(bracing.rear_diagonal_assembly().solids()):
        parts[f"diagonal_{index}"] = cast(Part, solid)
    return parts


def hardware() -> list[Part]:
    parts = model.rev3_hardware()[4:]
    for x in (params.BORE_CENTER_X_MM, params.BODY_WIDTH_MM - params.BORE_CENTER_X_MM):
        for y in (
            params.BORE_CENTER_Y_MM,
            params.BODY_DEPTH_MM - params.BORE_CENTER_Y_MM,
        ):
            parts.append(
                Cylinder(
                    2.5,
                    params.top_rod_end_z_mm() - params.bottom_rod_end_z_mm(),
                    align=(Align.CENTER, Align.CENTER, Align.MIN),
                ).moved(Location((x, y, params.bottom_rod_end_z_mm())))
            )
    for right in (False, True):
        x = params.BODY_WIDTH_MM if right else 0
        direction = -1.0 if right else 1.0
        for y in params.TOP_SIDE_FASTENER_Y_MM:
            for z in params.TOP_SIDE_FASTENER_Z_MM:
                parts.append(
                    model._m4_screw_x(
                        x - direction * params.TOP_SIDE_PLATE_THICK_MM, direction, y, z
                    )
                )
                parts.append(model._m4_insert_x(x, direction, y, z))
    return parts


def m4_pilot_coupon() -> Part:
    part = Box(15, 60, 18, align=(Align.MIN, Align.MIN, Align.MIN))
    for index, diameter in enumerate((5.6, 5.8, 6.0)):
        y = 10 + index * 20
        bore = Cylinder(
            diameter / 2, 11, align=(Align.CENTER, Align.CENTER, Align.MIN)
        ).moved(Location((0, y, 9), (0, 90, 0)))
        part -= bore
        for mark in range(index + 1):
            part -= Box(2, 2, 1, align=(Align.MIN, Align.MIN, Align.MIN)).moved(
                Location((6 + mark * 3, y - 1, 17))
            )
    return cast(Part, part)


def top_post_coupon() -> Part:
    post = column.rev3_upper_column_left().moved(Location((0, 0, params.SPLICE_Z_MM)))
    clip = Box(
        29, 30, params.TOP_SIDE_BEAM_HEIGHT_MM, align=(Align.MIN, Align.MIN, Align.MIN)
    ).moved(Location((0, 0, params.ADDED_HEIGHT_MM - params.TOP_SIDE_BEAM_HEIGHT_MM)))
    return cast(Part, post & clip)


def print_part(part: Part, rotation: tuple[float, float, float]) -> Part:
    result = part.moved(Location((0, 0, 0), rotation))
    low = result.bounding_box().min
    return cast(Part, result.moved(Location((-low.X, -low.Y, -low.Z))))


def export(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    assembled = printed_assembly()
    export_step(
        Compound(children=[*assembled.values(), *hardware(), top.lid_reference()]),
        str(out / "prototype_assembly.step"),
    )
    manifest: dict[str, dict] = {}
    selections: dict[str, tuple[Part, tuple[float, float, float], int]] = {}
    for name, part in assembled.items():
        if name.startswith("retainer_") and name != "retainer_front_left":
            continue
        if (
            name.startswith(("lower_rear", "upper_rear"))
            or name == "side_restraint_right"
        ):
            continue
        rotation = (0.0, 0.0, 0.0)
        quantity = 1
        if name.startswith(("lower_", "upper_")):
            quantity = 2
        elif name.startswith("retainer_"):
            quantity = 4
        elif name.startswith("rail_"):
            rotation = (-90, 0, 0)
        elif name.startswith("top_side_beam"):
            rotation = (0, -90 if name.endswith("left") else 90, 0)
        elif name.startswith("rear_"):
            rotation = (90, 0, 0)
        elif name.startswith("side_restraint"):
            rotation = (0, -90, 0)
            quantity = 2
        elif name.startswith("diagonal"):
            continue
        elif name.startswith("mount"):
            rotation = (0, 180, 0)
        selections[name] = (part, rotation, quantity)
    selections["diagonal_lower"] = (
        bracing.rear_diagonal_brace_half(False),
        (0, 0, 0),
        1,
    )
    selections["diagonal_upper"] = (
        bracing.rear_diagonal_brace_half(True),
        (180, 0, 0),
        1,
    )
    selections["m4_pilot_coupon"] = (m4_pilot_coupon(), (0, 0, 0), 1)
    spacer = Box(15, 15, 6, align=(Align.CENTER, Align.CENTER, Align.MIN)) - Cylinder(
        params.M4_FRAME_CLEARANCE_DIA_MM / 2,
        6,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    selections["m4_test_spacer"] = (cast(Part, spacer), (0, 0, 0), 1)
    selections["lid_fit_coupon"] = (top.lid_fit_coupon(), (0, -90, 0), 1)
    selections["top_post_coupon"] = (top_post_coupon(), (0, 0, 0), 1)
    selections["rail_fit_coupon"] = (rail.rail_coupon(), (-90, 0, 0), 1)
    selections["rail_column_coupon"] = (column.rev3_column_joint_coupon(), (0, 0, 0), 1)
    for name, (part, rotation, quantity) in selections.items():
        oriented = print_part(part, rotation)
        if not oriented.is_valid or len(oriented.solids()) != 1:
            raise ValueError(f"Invalid part: {name}")
        size = oriented.bounding_box().size
        if any(v > 256 for v in (size.X, size.Y, size.Z)):
            raise ValueError(f"Part exceeds bed: {name}")
        export_stl(oriented, str(out / f"{name}.stl"))
        manifest[name] = {
            "quantity": quantity,
            "size_mm": [size.X, size.Y, size.Z],
            "cad_volume_mm3": oriented.volume,
            "rotation_degrees": rotation,
            "status": "prototype; physical acceptance pending",
        }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--export-dir", type=Path, required=True)
    args = parser.parse_args()
    export(args.export_dir)


if __name__ == "__main__":
    main()
