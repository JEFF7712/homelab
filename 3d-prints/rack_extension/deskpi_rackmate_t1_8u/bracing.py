"""Parametric frame bracing and crossmembers for DeskPi RackMate T1 8U extension.

Provides anti-racking structural rigidity (Option 1):
1. Lower and upper rear horizontal crossbars maintaining column spacing.
2. Two-piece interlocking rear diagonal brace providing triangular shear stiffness.
3. Mid-height side restraint bars linking front and rear posts against cantilever sway.

All parts are segmented or sized to print flat on the 256 mm A1 build plate.
"""

from __future__ import annotations

import math
from typing import cast

from build123d import (
    Align,
    Box,
    Compound,
    Cylinder,
    Location,
    Part,
    Plane,
)

from . import params


def rear_crossbar(z_pos: float, is_upper: bool = False) -> Part:
    """Rear horizontal crossbar spanning between the rear uprights.

    Length is 251 mm (from X=15 to X=266 mm), fitting directly within the
    256 mm bed axis without diagonal placement. Mounts against the rear face
    at Y=200..210 mm. Fastens to columns at X=23 and X=258 with M4 screws.
    """
    x_min = 15.0
    x_max = params.BODY_WIDTH_MM - 15.0  # 266.0 mm
    length = x_max - x_min  # 251.0 mm
    h = params.REAR_CROSSBAR_HEIGHT_MM
    t = params.REAR_CROSSBAR_THICK_MM

    # Mounts against the column rear face (Y from BODY_DEPTH_MM to BODY_DEPTH_MM + t)
    y_min = params.BODY_DEPTH_MM

    bar = Box(length, t, h, align=(Align.MIN, Align.MIN, Align.MIN)).locate(
        Location((x_min, y_min, z_pos))
    )

    head_dia = params.BRACE_FASTENER_HEAD_DIA_MM
    clearance_radius = params.M4_FRAME_CLEARANCE_DIA_MM / 2
    insert_radius = params.M4_HEAT_SET_PILOT_DIA_MM / 2
    for x in (
        params.REAR_CROSSBAR_FASTENER_X_LEFT_MM,
        params.REAR_CROSSBAR_FASTENER_X_RIGHT_MM,
    ):
        clearance = Cylinder(
            radius=clearance_radius,
            height=t + 2.0,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        ).locate(Location((x, y_min - 1.0, z_pos + h / 2), (-90.0, 0, 0)))
        cb = Cylinder(
            radius=head_dia / 2,
            height=params.M4_BRACE_SCREW_HEAD_HEIGHT_MM + 1.0,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        ).locate(
            Location(
                (x, y_min + t - params.M4_BRACE_SCREW_HEAD_HEIGHT_MM, z_pos + h / 2),
                (-90.0, 0, 0),
            )
        )
        bar = cast(Part, bar - clearance - cb)

    diag_x = (
        params.DIAGONAL_ANCHOR_UPPER_X_MM
        if is_upper
        else params.DIAGONAL_ANCHOR_LOWER_X_MM
    )
    diag_insert = Cylinder(
        radius=insert_radius,
        height=params.M4_HEAT_SET_BORE_DEPTH_MM,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(Location((diag_x, y_min + t, z_pos + h / 2), (90.0, 0, 0)))
    bar = cast(Part, bar - diag_insert)

    return bar


def rear_lower_crossbar() -> Part:
    """Lower rear crossbar with its attachment center at z=55.0 mm."""
    return rear_crossbar(params.REAR_LOWER_CROSSBAR_Z_MM - 5.0, is_upper=False)


def rear_upper_crossbar() -> Part:
    """Upper rear crossbar at z=325.0 mm (anchor center at z=335.0 mm)."""
    return rear_crossbar(params.REAR_UPPER_CROSSBAR_Z_MM, is_upper=True)


def side_restraint_bar(right: bool = False) -> Part:
    """Mid-height side restraint bar connecting front and rear columns.

    Spans Y=15 to Y=185 mm (170 mm total length), fitting flat on the bed.
    Mounts against the column outer face (X=-6..0 on left, X=281..287 on right).
    """
    length = params.BODY_DEPTH_MM - 30.0  # 170.0 mm
    y_min = 15.0
    h = params.SIDE_RESTRAINT_HEIGHT_MM
    t = params.SIDE_RESTRAINT_THICK_MM

    x_pos = params.BODY_WIDTH_MM if right else -t

    bar = Box(t, length, h, align=(Align.MIN, Align.MIN, Align.MIN)).locate(
        Location((x_pos, y_min, params.SIDE_RESTRAINT_Z_MM - h / 2))
    )

    # Fastener clearance holes into front column (Y=15) and rear column (Y=185)
    for y in (y_min + 7.5, y_min + length - 7.5):
        hole = Cylinder(
            radius=params.REAR_BRACE_FASTENER_DIA_MM / 2,
            height=t + 2.0,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        ).locate(Location((x_pos - 1.0, y, params.SIDE_RESTRAINT_Z_MM), (0, 90.0, 0)))
        bar = cast(Part, bar - hole)

    return bar


def rear_diagonal_brace_half(upper: bool = False) -> Part:
    """One half of the two-piece bolted rear diagonal brace.

    Derived parametrically from anchor coordinates (35, 25) -> (246, 335).
    Span is 375.0 mm, half length is 212.5 mm, fitting flat on the 256 mm bed.
    """
    w = params.REAR_BRACE_WIDTH_MM
    t = params.REAR_BRACE_THICK_MM
    half_len = params.DIAGONAL_HALF_LEN_MM
    lap_len = params.DIAGONAL_LAP_LEN_MM

    # Base strut
    strut = Box(half_len, w, t, align=(Align.MIN, Align.CENTER, Align.MIN))

    # Lap joint cut on the joining end
    lap_cut = Box(
        lap_len + 1.0,
        w + 2.0,
        t / 2 if upper else t / 2 + 0.5,
        align=(Align.MIN, Align.CENTER, Align.MIN),
    )
    if upper:
        lap_cut = lap_cut.locate(Location((0.0, 0.0, 0.0)))
    else:
        lap_cut = lap_cut.locate(Location((half_len - lap_len, 0.0, t / 2)))
    strut = cast(Part, strut - lap_cut)

    # Lap joint fastener holes (two M4 holes spaced 15 mm)
    lap_ref = 0.0 if upper else half_len - lap_len
    for offset in (7.5, 22.5):
        hole = Cylinder(
            radius=params.REAR_BRACE_FASTENER_DIA_MM / 2,
            height=t + 2.0,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        ).locate(Location((lap_ref + offset, 0.0, -1.0)))
        strut = cast(Part, strut - hole)

    # End attachment hole (10 mm from end)
    end_ref = half_len - 10.0 if upper else 10.0
    end_hole = Cylinder(
        radius=params.REAR_BRACE_FASTENER_DIA_MM / 2,
        height=t + 2.0,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(Location((end_ref, 0.0, -1.0)))
    strut = cast(Part, strut - end_hole)

    return strut


def rear_diagonal_assembly() -> Compound:
    """Assembled 3D representation of the bolted rear diagonal brace."""
    dx = params.DIAGONAL_ANCHOR_UPPER_X_MM - params.DIAGONAL_ANCHOR_LOWER_X_MM
    dz = params.DIAGONAL_ANCHOR_UPPER_Z_MM - params.DIAGONAL_ANCHOR_LOWER_Z_MM
    dist = math.hypot(dx, dz)
    y_pos = params.BODY_DEPTH_MM + params.REAR_CROSSBAR_THICK_MM

    lower = rear_diagonal_brace_half(upper=False).moved(Location((-10.0, 0, 0)))
    upper = rear_diagonal_brace_half(upper=True).moved(
        Location(
            (
                params.DIAGONAL_SPAN_MM - (params.DIAGONAL_HALF_LEN_MM - 10.0),
                0,
                0,
            )
        )
    )

    diag_local = Compound(children=[lower, upper])
    plane = Plane(
        origin=(
            params.DIAGONAL_ANCHOR_LOWER_X_MM,
            y_pos,
            params.DIAGONAL_ANCHOR_LOWER_Z_MM,
        ),
        x_dir=(dx / dist, 0, dz / dist),
        z_dir=(0, 1, 0),
    )
    return diag_local.moved(plane.location)
