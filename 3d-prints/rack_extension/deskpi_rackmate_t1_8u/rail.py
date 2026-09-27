"""Parametric separate equipment rail for DeskPi RackMate T1 8U extension.

Decoupled equipment rails (Option 1) print flat on the build plate without
supports, aligning layer circles with the insert axes and allowing independent
iteration of rack hole pitch, insert pilot sizing, or fastener clearance without
discarding or reprinting the structural posts.
"""

from __future__ import annotations

from typing import cast

from build123d import (
    Align,
    Box,
    Cylinder,
    Location,
    Part,
)

from . import params, seam


def equipment_rail_segment(
    z_start: float,
    z_end: float,
    fastener_zs: tuple[float, ...],
    right: bool = False,
) -> Part:
    """Generate a printable separate equipment rail strip.

    When printed flat (back face on the build plate), all hole axes are vertical,
    requiring zero supports and ensuring circular insert pilots.
    """
    length = z_end - z_start
    w = params.RAIL_WIDTH_MM
    t = params.RAIL_THICKNESS_MM

    # Base rail strip solid
    if right:
        x_min = params.BODY_WIDTH_MM - params.RAIL_X_END_MM
        cx_rack = params.BODY_WIDTH_MM - params.RACK_HOLE_CENTER_X_MM
        cx_fastener = params.BODY_WIDTH_MM - params.RAIL_FASTENER_X_MM
    else:
        x_min = params.RAIL_X_START_MM
        cx_rack = params.RACK_HOLE_CENTER_X_MM
        cx_fastener = params.RAIL_FASTENER_X_MM

    rail = Box(w, t, length, align=(Align.MIN, Align.MIN, Align.MIN)).locate(
        Location((x_min, 0.0, z_start))
    )

    # Cut rack-hole insert pilots
    for z in seam.extension_hole_centers():
        if z_start <= z <= z_end:
            pilot = Cylinder(
                radius=params.INSERT_PILOT_DIA_MM / 2,
                height=params.M5_INSERT_COUPON_DEPTH_MM + 1.0,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
            ).locate(Location((cx_rack, -1.0, z), (-90.0, 0, 0)))
            rail = cast(Part, rail - pilot)

    # Cut rail-to-column attachment holes (M3 clearance + counterbore)
    for z in fastener_zs:
        if z_start <= z <= z_end:
            through_hole = Cylinder(
                radius=params.RAIL_FASTENER_DIA_MM / 2,
                height=t + 2.0,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
            ).locate(Location((cx_fastener, -1.0, z), (-90.0, 0, 0)))
            couterbore = Cylinder(
                radius=params.RAIL_FASTENER_HEAD_DIA_MM / 2,
                height=params.RAIL_FASTENER_HEAD_DEPTH_MM + 1.0,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
            ).locate(Location((cx_fastener, -1.0, z), (-90.0, 0, 0)))
            rail = cast(Part, rail - through_hole - couterbore)

    return rail


def lower_equipment_rail(right: bool = False) -> Part:
    """Lower equipment rail from lower frame (z=12.0) to splice (z=169.85)."""
    return equipment_rail_segment(
        z_start=params.RAIL_LOWER_Z_START_MM,
        z_end=params.RAIL_LOWER_Z_END_MM,
        fastener_zs=params.RAIL_FASTENER_Z_LOWER_MM,
        right=right,
    )


def lower_equipment_rail_left() -> Part:
    return lower_equipment_rail(right=False)


def lower_equipment_rail_right() -> Part:
    return lower_equipment_rail(right=True)


def upper_equipment_rail(right: bool = False) -> Part:
    """Upper equipment rail from splice (z=169.85) to top frame (z=343.6)."""
    return equipment_rail_segment(
        z_start=params.RAIL_UPPER_Z_START_MM,
        z_end=params.RAIL_UPPER_Z_END_MM,
        fastener_zs=params.RAIL_FASTENER_Z_UPPER_MM,
        right=right,
    )


def upper_equipment_rail_left() -> Part:
    return upper_equipment_rail(right=False)


def upper_equipment_rail_right() -> Part:
    return upper_equipment_rail(right=True)


def rail_coupon(right: bool = False) -> Part:
    """1U test coupon with 3 rack insert pilots and 1 rail fastener hole."""
    z_start = 70.0
    z_end = z_start + params.U_PITCH_MM
    fastener_zs = (params.RAIL_FASTENER_Z_LOWER_MM[1],)
    return equipment_rail_segment(
        z_start=z_start,
        z_end=z_end,
        fastener_zs=fastener_zs,
        right=right,
    )
