"""Preliminary Build123d model: DeskPi RackMate T1 8U upper extension.

Scope: lower interface frame (segmented for the A1 bed), four hollow
ribbed columns split 4U+4U with spigot splice, continuous M5 tie-rod
channels, and M4 insert-boss envelopes on front/rear rack faces.
End blocks, seam drawing geometry, and measured handle datums beyond the
PRD table are intentionally schematic and gated, not production.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast

from build123d import (
    Align,
    Box,
    Compound,
    Cylinder,
    Location,
    Part,
    RegularPolygon,
    extrude,
)

from . import corner, params, seam

Z0_LID_PLANE = 0.0


def lower_frame_segments() -> list[Part]:
    """Two side rails + front/rear crossmembers; split for the 256 mm bed."""
    segs: list[Part] = []
    rail_len = params.BODY_DEPTH_MM
    rail_w = params.TOP_MEMBER_WIDTH_MM
    h = params.FRAME_ZONE_MM
    for x0 in (0.0, params.BODY_WIDTH_MM - rail_w):
        seg = Box(rail_w, rail_len, h, align=(Align.MIN, Align.MIN, Align.MIN))
        segs.append(seg.locate(Location((x0, 0, Z0_LID_PLANE))))
    ov = params.JOINT_OVERLAP_MM
    cross_len = params.BODY_WIDTH_MM - 2 * rail_w + 2 * ov
    for y0 in (0.0, params.BODY_DEPTH_MM - rail_w):
        seg = Box(cross_len, rail_w, h, align=(Align.MIN, Align.MIN, Align.MIN))
        segs.append(seg.locate(Location((rail_w - ov, y0, Z0_LID_PLANE))))
    return segs


def top_frame_segments() -> list[Part]:
    """Mirror of the lower frame at the relocated lid-support plane."""
    top_z = Z0_LID_PLANE + params.ADDED_HEIGHT_MM - params.FRAME_ZONE_MM
    segs: list[Part] = []
    rail_len = params.BODY_DEPTH_MM
    rail_w = params.TOP_MEMBER_WIDTH_MM
    h = params.FRAME_ZONE_MM
    for x0 in (0.0, params.BODY_WIDTH_MM - rail_w):
        seg = Box(rail_w, rail_len, h, align=(Align.MIN, Align.MIN, Align.MIN))
        segs.append(seg.locate(Location((x0, 0, top_z))))
    ov = params.JOINT_OVERLAP_MM
    cross_len = params.BODY_WIDTH_MM - 2 * rail_w + 2 * ov
    for y0 in (0.0, params.BODY_DEPTH_MM - rail_w):
        seg = Box(cross_len, rail_w, h, align=(Align.MIN, Align.MIN, Align.MIN))
        segs.append(seg.locate(Location((rail_w - ov, y0, top_z))))
    return segs


def column_body(z_base: float, length: float, with_bore: bool = True) -> Part:
    """Hollow ribbed box column with optional M5 tie-rod bore."""
    w = params.COLUMN_INWARD_MM
    d = params.COLUMN_DEPTH_MM
    wall = params.COLUMN_WALL_MM
    outer = Box(w, d, length, align=(Align.MIN, Align.MIN, Align.MIN))
    inner = Box(
        w - 2 * wall,
        d - 2 * wall,
        length + 2.0,
        align=(Align.MIN, Align.MIN, Align.MIN),
    ).locate(Location((wall, wall, -1.0)))
    col = outer - inner
    ov = params.JOINT_OVERLAP_MM
    rib = Box(
        wall, d - 2 * wall + 2 * ov, length, align=(Align.MIN, Align.MIN, Align.MIN)
    )
    rib = rib.locate(Location(((w - wall) / 2, wall - ov, 0.0)))
    col = col + rib
    if with_bore:
        bore = Cylinder(
            radius=params.TIE_BORE_DIA_MM / 2,
            height=length + 2.0,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        ).locate(Location((w / 2, d / 2, -1.0)))
        col = col - bore
    return cast(Part, col.locate(Location((0, 0, z_base))))


def lower_column_module(origin_x: float, origin_y: float) -> Part:
    """Lower 4U module with male spigot on top (zero added stack height)."""
    z_base = Z0_LID_PLANE + params.PATH_A_BOTTOM_FRAME_MM
    length = params.PATH_A_CLEAR_BODY_MM
    col = column_body(z_base, length)
    spigot_w = (
        params.COLUMN_INWARD_MM
        - 2 * params.SPLICE_CLEARANCE_PER_SIDE_MM
        - 2 * params.COLUMN_WALL_MM
    )
    spigot_d = (
        params.COLUMN_DEPTH_MM
        - 2 * params.SPLICE_CLEARANCE_PER_SIDE_MM
        - 2 * params.COLUMN_WALL_MM
    )
    spigot = Box(
        spigot_w,
        spigot_d,
        params.SPLICE_ENGAGEMENT_MM + params.JOINT_OVERLAP_MM,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(
        Location(
            (
                origin_x + params.COLUMN_INWARD_MM / 2,
                origin_y + params.COLUMN_DEPTH_MM / 2,
                z_base + length - params.JOINT_OVERLAP_MM,
            )
        )
    )
    return cast(
        Part,
        col.moved(Location((origin_x, origin_y, 0)))
        + spigot
        - tie_bore_at(origin_x, origin_y, z_base + length, params.SPLICE_ENGAGEMENT_MM),
    )


def tie_bore_at(ox: float, oy: float, z: float, length: float) -> Part:
    return Cylinder(
        radius=params.TIE_BORE_DIA_MM / 2,
        height=length + 2.0,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(
        Location(
            (ox + params.COLUMN_INWARD_MM / 2, oy + params.COLUMN_DEPTH_MM / 2, z - 1.0)
        )
    )


def upper_column_module(origin_x: float, origin_y: float) -> Part:
    """Upper 4U module with female socket (spigot overlap adds zero height)."""
    z_base = Z0_LID_PLANE + params.PATH_A_BOTTOM_FRAME_MM + params.PATH_A_CLEAR_BODY_MM
    length = params.PATH_A_CLEAR_BODY_MM
    col = column_body(z_base, length)
    socket_w = (
        params.COLUMN_INWARD_MM
        - 2 * params.COLUMN_WALL_MM
        + 2 * params.SPLICE_CLEARANCE_PER_SIDE_MM
    )
    socket_d = (
        params.COLUMN_DEPTH_MM
        - 2 * params.COLUMN_WALL_MM
        + 2 * params.SPLICE_CLEARANCE_PER_SIDE_MM
    )
    socket = Box(
        socket_w,
        socket_d,
        params.SPLICE_ENGAGEMENT_MM + 1.0,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(
        Location(
            (
                origin_x + params.COLUMN_INWARD_MM / 2,
                origin_y + params.COLUMN_DEPTH_MM / 2,
                z_base - 1.0,
            )
        )
    )
    return cast(
        Part,
        col.moved(Location((origin_x, origin_y, 0)))
        - socket
        - tie_bore_at(origin_x, origin_y, z_base - 1.0, length + 2.0),
    )


def _y_cylinder(
    radius: float, height: float, cx: float, y_ref: float, z: float, outward: bool
) -> Part:
    """Cylinder with axis along Y: +Y from the front face, -Y from the rear."""
    cyl = Cylinder(
        radius=radius, height=height, align=(Align.CENTER, Align.CENTER, Align.MIN)
    )
    angle = -90.0 if outward else 90.0
    return cyl.locate(Location((cx, y_ref, z), (angle, 0, 0)))


def _boss_and_pilot(hole_z: float, face_high: bool) -> tuple[Part, Part]:
    """Merged insert boss (additive) and pilot bore (subtractive) at a hole."""
    cx = params.BORE_CENTER_X_MM
    face = params.COLUMN_DEPTH_MM if face_high else 0.0
    cut_ref = params.COLUMN_DEPTH_MM + 1.0 if face_high else -1.0
    boss = _y_cylinder(
        params.BOSS_OD_NOMINAL_MM / 2,
        params.BOSS_DEPTH_TARGET_MM,
        cx,
        face,
        hole_z,
        outward=not face_high,
    )
    pilot = _y_cylinder(
        params.INSERT_PILOT_DIA_MM / 2,
        params.BOSS_DEPTH_TARGET_MM + 1.0,
        cx,
        cut_ref,
        hole_z,
        outward=not face_high,
    )
    return boss, pilot


def bottom_end_block(origin_x: float, origin_y: float, face_high: bool) -> Part:
    """Merged frame/first-boss/nut-capture block, z=0..25 (Rev-1 bottom)."""
    w = params.COLUMN_INWARD_MM
    d = params.COLUMN_DEPTH_MM
    top = params.BOTTOM_BLOCK_TOP_Z_MM
    block = Box(w, d, top, align=(Align.MIN, Align.MIN, Align.MIN))
    nut_af_r = params.HEX_POCKET_AF_MM / 3**0.5
    hex_solid = extrude(
        RegularPolygon(radius=nut_af_r, side_count=6), amount=params.HEX_POCKET_DEPTH_MM
    ).locate(
        Location(
            (
                params.BORE_CENTER_X_MM,
                params.BORE_CENTER_Y_MM,
                params.HEX_POCKET_FLOOR_Z_MM,
            )
        )
    )
    washer = Cylinder(
        radius=params.WASHER_RECESS_DIA_MM / 2,
        height=params.WASHER_RECESS_DEPTH_MM,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(
        Location(
            (
                params.BORE_CENTER_X_MM,
                params.BORE_CENTER_Y_MM,
                params.HEX_POCKET_FLOOR_Z_MM + params.HEX_POCKET_DEPTH_MM,
            )
        )
    )
    bore = Cylinder(
        radius=params.TIE_BORE_DIA_MM / 2,
        height=top + 2.0,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(Location((params.BORE_CENTER_X_MM, params.BORE_CENTER_Y_MM, -1.0)))
    boss, pilot = _boss_and_pilot(seam_first_hole_z(), face_high)
    part = block + boss - hex_solid - washer - bore - pilot
    return cast(Part, part.moved(Location((origin_x, origin_y, 0))))


def seam_first_hole_z() -> float:
    """Lowest provided extension hole center (partial position)."""
    from . import seam as _seam

    return _seam.first_extension_hole()


def top_end_block(origin_x: float, origin_y: float, face_high: bool) -> Part:
    """Integrated washer/nut/boss/bore block, z=330..355.6 (Rev-1 top)."""
    from . import seam as _seam

    w = params.COLUMN_INWARD_MM
    d = params.COLUMN_DEPTH_MM
    bottom = params.TOP_BLOCK_BOTTOM_Z_MM
    height = params.ADDED_HEIGHT_MM - bottom
    block = Box(w, d, height, align=(Align.MIN, Align.MIN, Align.MIN)).locate(
        Location((0, 0, bottom))
    )
    seat = params.top_bearing_seat_z_mm()
    nut = Cylinder(
        radius=params.TOP_NUT_POCKET_DIA_MM / 2,
        height=params.TOP_NUT_POCKET_DEPTH_MM,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(Location((params.BORE_CENTER_X_MM, params.BORE_CENTER_Y_MM, seat)))
    washer = Cylinder(
        radius=params.WASHER_RECESS_DIA_MM / 2,
        height=params.WASHER_RECESS_DEPTH_MM,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(
        Location(
            (
                params.BORE_CENTER_X_MM,
                params.BORE_CENTER_Y_MM,
                seat + params.TOP_NUT_POCKET_DEPTH_MM,
            )
        )
    )
    bore = Cylinder(
        radius=params.TIE_BORE_DIA_MM / 2,
        height=height + 2.0,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(Location((params.BORE_CENTER_X_MM, params.BORE_CENTER_Y_MM, bottom - 1.0)))
    boss, pilot = _boss_and_pilot(_seam.last_extension_hole(), face_high)
    part = block + boss - nut - washer - bore - pilot
    return cast(Part, part.moved(Location((origin_x, origin_y, 0))))


def splice_rail_strip(origin_x: float, origin_y: float, face_high: bool) -> Part:
    """Continuous reinforced strip with spigot relief (Rev-1 splice)."""
    w = params.COLUMN_INWARD_MM
    d = params.COLUMN_DEPTH_MM
    half = params.RAIL_STRIP_HALF_MM
    strip = Box(w, d, 2 * half, align=(Align.MIN, Align.MIN, Align.MIN)).locate(
        Location((0, 0, corner.SPLICE_Z - half))
    )
    spigot_w = (
        params.COLUMN_INWARD_MM
        - 2 * params.SPLICE_CLEARANCE_PER_SIDE_MM
        - 2 * params.COLUMN_WALL_MM
    )
    spigot_d = (
        params.COLUMN_DEPTH_MM
        - 2 * params.SPLICE_CLEARANCE_PER_SIDE_MM
        - 2 * params.COLUMN_WALL_MM
    )
    relief = Box(
        spigot_w,
        spigot_d,
        2 * half + 2.0,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(Location((w / 2, d / 2, corner.SPLICE_Z - half - 1.0)))
    bore = Cylinder(
        radius=params.TIE_BORE_DIA_MM / 2,
        height=2 * half + 2.0,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(Location((w / 2, d / 2, corner.SPLICE_Z - half - 1.0)))
    boss, pilot = _boss_and_pilot(corner.SPLICE_Z, face_high)
    part = strip + boss - relief - bore - pilot
    return cast(Part, part.moved(Location((origin_x, origin_y, 0))))


def end_blocks_and_strips() -> list[Part]:
    """All four corners: bottom block, top block, splice strip each."""
    parts: list[Part] = []
    for ox, oy in column_origins():
        face_high = oy > 0.0
        parts.append(bottom_end_block(ox, oy, face_high))
        parts.append(top_end_block(ox, oy, face_high))
        parts.append(splice_rail_strip(ox, oy, face_high))
    return parts


def interface_coupon() -> Part:
    """Phase 1 fit coupon: rail section + holes + locating lips (Rev-1).

    The rail overhangs the member on both sides so the side lips have a
    volumetric root: lips beside the member cannot touch a flush-width
    rail. Production registration will use corner pockets instead.
    """
    rail_w = params.TOP_MEMBER_WIDTH_MM
    root = params.LIP_CLEARANCE_MM + params.LIP_THICK_MM
    y0, y1 = params.COUPON_Y_START_MM, params.COUPON_Y_END_MM
    rail = Box(
        rail_w + 2 * root,
        y1 - y0,
        params.FRAME_ZONE_MM,
        align=(Align.MIN, Align.MIN, Align.MIN),
    ).locate(Location((-root, y0, 0)))
    ov = params.JOINT_OVERLAP_MM
    lip_h = params.LIP_DEPTH_MM + ov
    lip_out = Box(
        params.LIP_THICK_MM,
        y1 - y0,
        lip_h,
        align=(Align.MIN, Align.MIN, Align.MIN),
    ).locate(
        Location(
            (-params.LIP_CLEARANCE_MM - params.LIP_THICK_MM, y0, -params.LIP_DEPTH_MM)
        )
    )
    lip_in = Box(
        params.LIP_THICK_MM,
        y1 - y0,
        lip_h,
        align=(Align.MIN, Align.MIN, Align.MIN),
    ).locate(Location((rail_w + params.LIP_CLEARANCE_MM, y0, -params.LIP_DEPTH_MM)))
    part = cast(Part, rail + lip_out + lip_in)
    for y in (25.0, 38.0):
        hole = Cylinder(
            radius=params.COUPON_HOLE_DIA_MM / 2,
            height=params.FRAME_ZONE_MM + 2.0,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        ).locate(Location((params.STRUCTURAL_HOLE_X_LEFT_MM, y, -1.0)))
        part = cast(Part, part - hole)
    return part


def corner_interface() -> Part:
    """Front-corner interface with lips clear of the transverse T1 member."""
    rail_w = params.TOP_MEMBER_WIDTH_MM
    root = params.LIP_CLEARANCE_MM + params.LIP_THICK_MM
    y0, y1 = params.COUPON_Y_START_MM, params.COUPON_Y_END_MM
    lip_y0 = params.CORNER_LIP_START_MM
    rail = Box(
        rail_w + 2 * root,
        y1 - y0,
        params.FRAME_ZONE_MM,
        align=(Align.MIN, Align.MIN, Align.MIN),
    ).locate(Location((-root, y0, 0)))
    ov = params.JOINT_OVERLAP_MM
    lip_h = params.LIP_DEPTH_MM + ov
    lip_out = Box(
        params.LIP_THICK_MM,
        y1 - lip_y0,
        lip_h,
        align=(Align.MIN, Align.MIN, Align.MIN),
    ).locate(
        Location(
            (
                -params.LIP_CLEARANCE_MM - params.LIP_THICK_MM,
                lip_y0,
                -params.LIP_DEPTH_MM,
            )
        )
    )
    lip_in = Box(
        params.LIP_THICK_MM,
        y1 - lip_y0,
        lip_h,
        align=(Align.MIN, Align.MIN, Align.MIN),
    ).locate(Location((rail_w + params.LIP_CLEARANCE_MM, lip_y0, -params.LIP_DEPTH_MM)))
    part = cast(Part, rail + lip_out + lip_in)
    for y in params.ATTACH_Y_MM[:2]:
        hole = Cylinder(
            radius=params.COUPON_HOLE_DIA_MM / 2,
            height=params.FRAME_ZONE_MM + 2.0,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        ).locate(Location((params.STRUCTURAL_HOLE_X_LEFT_MM, y, -1.0)))
        part = cast(Part, part - hole)
    return part


def lateral_fit_coupon() -> Part:
    """Short, hole-free slice of the interface coupon for lateral fit checks."""
    root = params.LIP_CLEARANCE_MM + params.LIP_THICK_MM
    clip = Box(
        params.TOP_MEMBER_WIDTH_MM + 2 * root,
        params.LATERAL_COUPON_LENGTH_MM,
        params.FRAME_ZONE_MM + params.LIP_DEPTH_MM,
        align=(Align.MIN, Align.MIN, Align.MIN),
    ).locate(
        Location(
            (
                -root,
                params.COUPON_Y_START_MM,
                -params.LIP_DEPTH_MM,
            )
        )
    )
    return cast(Part, interface_coupon() & clip)


def phase2_corner_fit_coupon() -> Part:
    """Short corner interface proving both M4 holes and the transverse-bar relief."""
    root = params.LIP_CLEARANCE_MM + params.LIP_THICK_MM
    clip = Box(
        params.TOP_MEMBER_WIDTH_MM + 2 * root,
        30.0,
        params.FRAME_ZONE_MM + params.LIP_DEPTH_MM,
        align=(Align.MIN, Align.MIN, Align.MIN),
    ).locate(Location((-root, 20.0, -params.LIP_DEPTH_MM)))
    return cast(Part, corner_interface() & clip)


def m5_insert_pilot_coupon() -> Part:
    """Blind-hole PETG coupon for selecting the measured M5 insert pilot."""
    length = 58.0
    width = 20.0
    height = 10.5
    coupon = Box(length, width, height, align=(Align.MIN, Align.MIN, Align.MIN))
    for x, diameter in zip(
        (10.0, 29.0, 48.0), params.M5_INSERT_PILOT_CANDIDATES_MM, strict=True
    ):
        hole = Cylinder(
            radius=diameter / 2,
            height=params.M5_INSERT_COUPON_DEPTH_MM + 1.0,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        ).locate(Location((x, width / 2, height - params.M5_INSERT_COUPON_DEPTH_MM)))
        coupon = coupon - hole
    marker = Cylinder(
        radius=1.5,
        height=height + 2.0,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(Location((3.0, 3.0, -1.0)))
    return cast(Part, coupon - marker)


def _splice_strip_half(lower: bool) -> Part:
    half = params.RAIL_STRIP_HALF_MM
    z0 = corner.SPLICE_Z - half if lower else corner.SPLICE_Z
    clip = Box(
        100.0,
        100.0,
        half,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(Location((params.COLUMN_INWARD_MM / 2, params.COLUMN_DEPTH_MM / 2, z0)))
    return cast(Part, splice_rail_strip(0, 0, False) & clip)


def phase2_corner_mount() -> Part:
    """T1 interface and end block with top-accessible M4 attachment screws."""
    spigot_w = params.COLUMN_INWARD_MM - 2 * params.COLUMN_WALL_MM - 0.5
    spigot_d = params.COLUMN_DEPTH_MM - 2 * params.COLUMN_WALL_MM - 0.5
    spigot = Box(
        spigot_w,
        spigot_d,
        params.LOWER_MOUNT_SPIGOT_HEIGHT_MM + params.JOINT_OVERLAP_MM,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(
        Location(
            (
                params.COLUMN_INWARD_MM / 2,
                params.COLUMN_DEPTH_MM / 2,
                params.BOTTOM_BLOCK_TOP_Z_MM - params.JOINT_OVERLAP_MM,
            )
        )
    )
    prototype = cast(Part, corner_interface() + bottom_end_block(0, 0, False) + spigot)
    mount_top = params.BOTTOM_BLOCK_TOP_Z_MM + params.LOWER_MOUNT_SPIGOT_HEIGHT_MM
    for y in params.ATTACH_Y_MM[:2]:
        hole = Cylinder(
            radius=params.COUPON_HOLE_DIA_MM / 2,
            height=params.FRAME_ZONE_MM + 2.0,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        ).locate(Location((params.STRUCTURAL_HOLE_X_LEFT_MM, y, -1.0)))
        prototype = cast(Part, prototype - hole)
        access = Cylinder(
            radius=params.ATTACH_ACCESS_DIA_MM / 2,
            height=mount_top - params.ATTACH_SEAT_Z_MM + 1.0,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        ).locate(
            Location(
                (
                    params.STRUCTURAL_HOLE_X_LEFT_MM,
                    y,
                    params.ATTACH_SEAT_Z_MM,
                )
            )
        )
        prototype = cast(Part, prototype - access)
    _, pilot = _boss_and_pilot(seam_first_hole_z(), False)
    tie_bore = tie_bore_at(0, 0, -1.0, mount_top + 2.0)
    hardware_opening = Cylinder(
        radius=params.WASHER_RECESS_DIA_MM / 2,
        height=(
            params.HEX_POCKET_FLOOR_Z_MM
            + params.HEX_POCKET_DEPTH_MM
            + params.WASHER_RECESS_DEPTH_MM
            + 1.0
        ),
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(Location((params.BORE_CENTER_X_MM, params.BORE_CENTER_Y_MM, -1.0)))
    prototype = cast(Part, prototype - pilot - tie_bore - hardware_opening)
    return prototype


def phase2_bottom_nut_retainer() -> Part:
    """Flush insert that keys the bottom M5 nyloc after underside loading."""
    height = params.HEX_POCKET_FLOOR_Z_MM + params.HEX_POCKET_DEPTH_MM
    outer = Cylinder(
        radius=(params.WASHER_RECESS_DIA_MM / 2 - params.BOTTOM_RETAINER_CLEARANCE_MM),
        height=height,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    nut_af_r = params.HEX_POCKET_AF_MM / 3**0.5
    nut = extrude(
        RegularPolygon(radius=nut_af_r, side_count=6), amount=height + 2.0
    ).locate(Location((0, 0, -1.0)))
    bore = Cylinder(
        radius=params.TIE_BORE_DIA_MM / 2,
        height=height + 2.0,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(Location((0, 0, -1.0)))
    return cast(Part, outer - nut - bore)


def phase2_lower_upright() -> Part:
    """Lower upright with a mount socket, top spigot, and lower seam half."""
    bottom = params.BOTTOM_BLOCK_TOP_Z_MM
    top = corner.SPLICE_Z
    upright = column_body(bottom, top - bottom)
    socket_w = (
        params.COLUMN_INWARD_MM
        - 2 * params.COLUMN_WALL_MM
        - 0.5
        + 2 * params.LOWER_MOUNT_SOCKET_CLEARANCE_MM
    )
    socket_d = (
        params.COLUMN_DEPTH_MM
        - 2 * params.COLUMN_WALL_MM
        - 0.5
        + 2 * params.LOWER_MOUNT_SOCKET_CLEARANCE_MM
    )
    socket = Box(
        socket_w,
        socket_d,
        params.LOWER_MOUNT_SPIGOT_HEIGHT_MM + 1.0,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(
        Location(
            (params.COLUMN_INWARD_MM / 2, params.COLUMN_DEPTH_MM / 2, bottom - 1.0)
        )
    )
    spigot_w = (
        params.COLUMN_INWARD_MM
        - 2 * params.SPLICE_CLEARANCE_PER_SIDE_MM
        - 2 * params.COLUMN_WALL_MM
    )
    spigot_d = (
        params.COLUMN_DEPTH_MM
        - 2 * params.SPLICE_CLEARANCE_PER_SIDE_MM
        - 2 * params.COLUMN_WALL_MM
    )
    top_spigot = Box(
        spigot_w,
        spigot_d,
        params.SPLICE_ENGAGEMENT_MM + params.JOINT_OVERLAP_MM,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(
        Location(
            (
                params.COLUMN_INWARD_MM / 2,
                params.COLUMN_DEPTH_MM / 2,
                top - params.JOINT_OVERLAP_MM,
            )
        )
    )
    prototype = cast(
        Part,
        upright
        - socket
        + top_spigot
        + _splice_strip_half(lower=True)
        - tie_bore_at(
            0, 0, bottom - 1.0, top - bottom + params.SPLICE_ENGAGEMENT_MM + 2.0
        ),
    )
    _, pilot = _boss_and_pilot(corner.SPLICE_Z, False)
    prototype = cast(Part, prototype - pilot)
    return cast(Part, prototype.moved(Location((0, 0, -bottom))))


def phase2_upper_upright() -> Part:
    """Upper half-splice, upper 4U module, and reinforced top corner."""
    prototype = (
        _splice_strip_half(lower=False)
        + upper_column_module(0, 0)
        + top_end_block(0, 0, False)
    )
    for z in (corner.SPLICE_Z, seam.last_extension_hole()):
        _, pilot = _boss_and_pilot(z, False)
        prototype = cast(Part, prototype - pilot)
    return cast(Part, prototype.moved(Location((0, 0, -corner.SPLICE_Z))))


def phase2_mount_spigot_fit_coupon() -> Part:
    """Exact mount spigot on a 2 mm section of its supporting shoulder."""
    bottom = params.BOTTOM_BLOCK_TOP_Z_MM - params.JOINT_OVERLAP_MM
    clip = Box(
        params.COLUMN_INWARD_MM,
        params.COLUMN_DEPTH_MM,
        params.LOWER_MOUNT_SPIGOT_HEIGHT_MM + params.JOINT_OVERLAP_MM,
        align=(Align.MIN, Align.MIN, Align.MIN),
    ).locate(Location((0, 0, bottom)))
    coupon = phase2_corner_mount() & clip
    return cast(Part, coupon.moved(Location((0, 0, -bottom))))


def phase2_mount_socket_fit_coupon() -> Part:
    """Exact lower-upright socket in a short printable column section."""
    height = params.LOWER_MOUNT_SPIGOT_HEIGHT_MM + 3.0
    clip = Box(
        params.COLUMN_INWARD_MM,
        params.COLUMN_DEPTH_MM,
        height,
        align=(Align.MIN, Align.MIN, Align.MIN),
    )
    return cast(Part, phase2_lower_upright() & clip)


def column_origins() -> list[tuple[float, float]]:
    w = params.COLUMN_INWARD_MM
    d = params.COLUMN_DEPTH_MM
    return [
        (0.0, 0.0),
        (params.BODY_WIDTH_MM - w, 0.0),
        (0.0, params.BODY_DEPTH_MM - d),
        (params.BODY_WIDTH_MM - w, params.BODY_DEPTH_MM - d),
    ]


def build_columns() -> list[Part]:
    parts: list[Part] = []
    for ox, oy in column_origins():
        parts.append(lower_column_module(ox, oy))
        parts.append(upper_column_module(ox, oy))
    return parts


def audit_gates() -> list[str]:
    """Unresolved pre-production gates; non-empty means NOT production-ready."""
    return [
        (
            "G1 seam drawing: theoretical -28.8(stock)/-16.1(absent)/ "
            "-0.2(omitted)/+15.7 provided; 7 full U + partials, see seam.py"
        ),
        (
            "G2 corner section: supported end-block load path for the 7.8 mm "
            "nyloc stack inside the 12 mm frame zone (1.2 mm residual web "
            "is not structural)"
        ),
        "G3 insert/splice/end-pocket interference sections at three stations",
        "G4 PETG creep / retained-clamp lateral-load validation with limits",
        (
            "G5 final rod cut length from measured seat spacing, not the "
            f"{params.rod_length_illustrative_mm():.1f} mm illustration"
        ),
        (
            "G6 anti-tip: free-standing, no wall attachment; stability via "
            "footprint, heavy-low placement, and staged ballast test"
        ),
        (
            "G7 measurements closed: M-01/M-02/M-03/M-04(5.8mm through, "
            "consistent with 5.9mm member)/M-05..M-13 confirmed, M-06=5.9mm "
            "flat bar, M-11 flat surrounds"
        ),
    ]


def validate() -> list[str]:
    errors = params.validate()
    errors.extend(f"rev1 proof: {v}" for v in corner.rev1_proof())
    segs = (
        lower_frame_segments()
        + top_frame_segments()
        + build_columns()
        + end_blocks_and_strips()
    )
    for part in segs:
        valid = part.is_valid if isinstance(part.is_valid, bool) else part.is_valid()
        if not valid:
            errors.append("invalid solid in preliminary assembly")
            break
    return errors


def export_assembly(out_dir: Path) -> dict[str, Path]:
    from build123d import export_step, export_stl

    out_dir.mkdir(parents=True, exist_ok=True)
    assembly = Compound(
        children=lower_frame_segments()
        + top_frame_segments()
        + build_columns()
        + end_blocks_and_strips()
    )
    step_path = out_dir / "rack_extension_assembly.step"
    stl_path = out_dir / "rack_extension_assembly.stl"
    export_step(assembly, str(step_path))
    export_stl(assembly, str(stl_path))
    columns = out_dir / "column_module_lower.stl"
    export_stl(lower_column_module(0, 0), str(columns))
    bottom = out_dir / "end_block_bottom.stl"
    export_stl(bottom_end_block(0, 0, False), str(bottom))
    top = out_dir / "end_block_top.stl"
    export_stl(top_end_block(0, 0, False), str(top))
    strip = out_dir / "splice_rail_strip.stl"
    export_stl(splice_rail_strip(0, 0, False), str(strip))
    coupon = out_dir / "phase1_interface_coupon.stl"
    export_stl(interface_coupon(), str(coupon))
    lateral_coupon = out_dir / "phase1_lateral_fit_coupon.stl"
    export_stl(lateral_fit_coupon(), str(lateral_coupon))
    corner_coupon = out_dir / "phase2_corner_fit_coupon.stl"
    export_stl(phase2_corner_fit_coupon(), str(corner_coupon))
    insert_coupon = out_dir / "m5_insert_pilot_coupon.stl"
    export_stl(m5_insert_pilot_coupon(), str(insert_coupon))
    phase2_mount = out_dir / "phase2_corner_mount.stl"
    export_stl(phase2_corner_mount(), str(phase2_mount))
    phase2_retainer = out_dir / "phase2_bottom_nut_retainer.stl"
    export_stl(phase2_bottom_nut_retainer(), str(phase2_retainer))
    phase2_lower = out_dir / "phase2_lower_upright.stl"
    export_stl(phase2_lower_upright(), str(phase2_lower))
    phase2_upper = out_dir / "phase2_upper_upright.stl"
    export_stl(phase2_upper_upright(), str(phase2_upper))
    phase2_spigot_coupon = out_dir / "phase2_mount_spigot_fit_coupon.stl"
    export_stl(phase2_mount_spigot_fit_coupon(), str(phase2_spigot_coupon))
    phase2_socket_coupon = out_dir / "phase2_mount_socket_fit_coupon.stl"
    export_stl(phase2_mount_socket_fit_coupon(), str(phase2_socket_coupon))
    return {
        "step": step_path,
        "stl": stl_path,
        "column": columns,
        "bottom": bottom,
        "top": top,
        "strip": strip,
        "coupon": coupon,
        "lateral_coupon": lateral_coupon,
        "phase2_corner_coupon": corner_coupon,
        "m5_insert_pilot_coupon": insert_coupon,
        "phase2_mount": phase2_mount,
        "phase2_bottom_nut_retainer": phase2_retainer,
        "phase2_lower_upright": phase2_lower,
        "phase2_upper_upright": phase2_upper,
        "phase2_mount_spigot_coupon": phase2_spigot_coupon,
        "phase2_mount_socket_coupon": phase2_socket_coupon,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export preliminary rack CAD")
    ap.add_argument("--export-dir", default="/tmp/opencode/rack_extension")
    ap.add_argument("--audit", action="store_true")
    args = ap.parse_args(argv)
    errors = validate()
    if errors:
        print("VALIDATION FAILURES:")
        for err in errors:
            print(f"  - {err}")
        return 1
    paths = export_assembly(Path(args.export_dir))
    for name, path in paths.items():
        print(f"{name}: {path}")
    if args.audit:
        print("OPEN AUDIT GATES (blocking production):")
        for gate in audit_gates():
            print(f"  - {gate}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
