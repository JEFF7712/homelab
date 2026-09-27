"""Upper side beams and lid support, pending physical hardware acceptance."""

from __future__ import annotations

from typing import cast

from build123d import Align, Box, Cylinder, Location, Part, Plane

from . import params


def side_beam(right: bool = False) -> Part:
    top_z = params.ADDED_HEIGHT_MM
    plate = Box(
        params.TOP_SIDE_PLATE_THICK_MM,
        params.BODY_DEPTH_MM,
        params.TOP_SIDE_BEAM_HEIGHT_MM,
        align=(Align.MIN, Align.MIN, Align.MIN),
    ).moved(
        Location(
            (-params.TOP_SIDE_PLATE_THICK_MM, 0, top_z - params.TOP_SIDE_BEAM_HEIGHT_MM)
        )
    )
    ledge = Box(
        params.COLUMN_INWARD_MM + params.JOINT_OVERLAP_MM,
        params.BODY_DEPTH_MM - 2 * params.COLUMN_DEPTH_MM,
        params.PATH_A_TOP_FRAME_MM,
        align=(Align.MIN, Align.MIN, Align.MIN),
    ).moved(
        Location(
            (
                -params.JOINT_OVERLAP_MM,
                params.COLUMN_DEPTH_MM,
                top_z - params.PATH_A_TOP_FRAME_MM,
            )
        )
    )
    beam = plate + ledge
    for y in params.TOP_SIDE_FASTENER_Y_MM:
        for z in params.TOP_SIDE_FASTENER_Z_MM:
            hole = Cylinder(
                params.M4_FRAME_CLEARANCE_DIA_MM / 2,
                params.TOP_SIDE_PLATE_THICK_MM + 2,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
            ).moved(Location((-params.TOP_SIDE_PLATE_THICK_MM - 1, y, z), (0, 90, 0)))
            beam -= hole
    for y in (params.HANDLE_HOLE_Y_FRONT_MM, params.HANDLE_HOLE_Y_REAR_MM):
        bore = Cylinder(
            params.M4_HEAT_SET_PILOT_DIA_MM / 2,
            params.LID_INSERT_BORE_DEPTH_MM + 1,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        ).moved(
            Location(
                (
                    params.HANDLE_HOLE_X_LEFT_MM,
                    y,
                    top_z - params.LID_INSERT_BORE_DEPTH_MM,
                )
            )
        )
        beam -= bore
    if right:
        beam = beam.mirror(Plane.YZ).moved(Location((params.BODY_WIDTH_MM, 0, 0)))
    return cast(Part, beam)


def lid_reference() -> Part:
    lid = Box(
        params.BODY_WIDTH_MM,
        params.BODY_DEPTH_MM,
        params.TOP_PLATE_THICK_MM,
        align=(Align.MIN, Align.MIN, Align.MIN),
    ).moved(Location((0, 0, params.ADDED_HEIGHT_MM)))
    for x in (params.HANDLE_HOLE_X_LEFT_MM, params.HANDLE_HOLE_X_RIGHT_MM):
        for y in (params.HANDLE_HOLE_Y_FRONT_MM, params.HANDLE_HOLE_Y_REAR_MM):
            lid -= Cylinder(
                params.M4_FRAME_CLEARANCE_DIA_MM / 2,
                params.TOP_PLATE_THICK_MM + 2,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
            ).moved(Location((x, y, params.ADDED_HEIGHT_MM - 1)))
    return cast(Part, lid)


def lid_fit_coupon() -> Part:
    clip = Box(45, 35, 50, align=(Align.MIN, Align.MIN, Align.MIN)).moved(
        Location((-10, 17.5, params.ADDED_HEIGHT_MM - 45))
    )
    return cast(Part, side_beam() & clip)
