"""Parametric structural column posts for DeskPi RackMate T1 8U extension (Option 1).

Decoupled Revision 3 column posts:
- Sits on the accepted Phase 2 corner mount.
- Central continuous M5 tie-rod bore.
- Proven spigot-socket middle splice at Z=169.85 mm.
- Keyed front rebate for the separate equipment rails.
- Captive horizontal M3 hex nut slots accessible from the open interior face.
- Clean tubular geometry with zero floating rack bosses or internal overhangs.
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

from . import corner, params


def _m3_nut_slot(z_pos: float, right: bool = False) -> Part:
    """Horizontal captive slot for an M3 hex nut, open to the inner column face."""
    w = params.M3_NUT_SLOT_WIDTH_MM
    t = params.M3_NUT_SLOT_THICK_MM
    # Nut slot reaches from X=20.5 mm to the inner face (X=29.0 mm + 1.0 mm open)
    slot_len = 10.0
    slot_x = params.BODY_WIDTH_MM - 29.0 - 1.0 if right else 20.0
    slot = Box(slot_len, t, w, align=(Align.MIN, Align.CENTER, Align.CENTER)).locate(
        Location((slot_x, params.M3_NUT_SLOT_Y_MM, z_pos))
    )
    return slot


def _m3_screw_hole(z_pos: float, right: bool = False) -> Part:
    """M3 clearance hole through the rail rebate wall into the nut slot."""
    cx = (
        params.BODY_WIDTH_MM - params.RAIL_FASTENER_X_MM
        if right
        else params.RAIL_FASTENER_X_MM
    )
    hole = Cylinder(
        radius=params.RAIL_FASTENER_DIA_MM / 2,
        height=params.COLUMN_DEPTH_MM,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(Location((cx, params.RAIL_THICKNESS_MM - 1.0, z_pos), (-90.0, 0, 0)))
    return hole


def _m4_insert_hole(axis: str, y_pos: float, z_pos: float, right: bool = False) -> Part:
    """Blind M4 heat-set insert pilot at a column fastening face."""
    radius = params.M4_HEAT_SET_PILOT_DIA_MM / 2
    height = params.M4_HEAT_SET_BORE_DEPTH_MM
    if axis == "x":
        start_x = params.COLUMN_INWARD_MM if right else 0.0
        rotation = (0.0, -90.0 if right else 90.0, 0.0)
        location = (start_x, y_pos, z_pos)
    else:
        start_y = params.COLUMN_DEPTH_MM
        rotation = (90.0, 0.0, 0.0)
        location = (params.REAR_CROSSBAR_FASTENER_X_LEFT_MM, start_y, z_pos)
        if right:
            location = (
                params.COLUMN_INWARD_MM - location[0],
                start_y,
                z_pos,
            )
    return Cylinder(
        radius=radius,
        height=height,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(Location(location, rotation))


def _rail_rebate(z_start: float, z_end: float, right: bool = False) -> Part:
    """Front pocket receiving the separate equipment rail."""
    w = params.RAIL_WIDTH_MM + 0.3  # clearance
    t = 1.0 + params.RAIL_REBATE_DEPTH_MM + 0.2  # cuts Y from -1.0 to 9.7 mm
    length = z_end - z_start + 2.0
    x_min = (
        params.BODY_WIDTH_MM - params.RAIL_X_END_MM - 0.15
        if right
        else params.RAIL_X_START_MM - 0.15
    )
    return Box(w, t, length, align=(Align.MIN, Align.MIN, Align.MIN)).locate(
        Location((x_min, -1.0, z_start - 1.0))
    )


def rev3_column_joint_coupon(right: bool = False) -> Part:
    """1U test coupon matching the rail coupon (z=70.0 to 114.45 mm).

    Tests the rail rebate seating, captive M3 nut slot, and M5 rod clearance.
    """
    z_start = 70.0
    length = params.U_PITCH_MM  # 44.45 mm
    w = params.COLUMN_INWARD_MM
    d = params.COLUMN_DEPTH_MM

    outer = Box(w, d, length, align=(Align.MIN, Align.MIN, Align.MIN)).locate(
        Location((0.0, 0.0, z_start))
    )
    bore = Cylinder(
        radius=params.TIE_BORE_DIA_MM / 2,
        height=length + 2.0,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(
        Location((params.BORE_CENTER_X_MM, params.BORE_CENTER_Y_MM, z_start - 1.0))
    )

    rebate = _rail_rebate(z_start, z_start + length, right)
    fz = params.RAIL_FASTENER_Z_LOWER_MM[1]
    screw_hole = _m3_screw_hole(fz, right)
    nut_slot = _m3_nut_slot(fz, right)

    part = cast(Part, outer - bore - rebate - screw_hole - nut_slot)
    return cast(Part, part.moved(Location((0, 0, -z_start))))


def rev3_lower_column(right: bool = False) -> Part:
    """Lower structural column from bottom mount (z=36.35) to splice (z=169.85)."""
    bottom = params.BOTTOM_BLOCK_TOP_Z_MM
    top = corner.SPLICE_Z
    length = top - bottom
    w = params.COLUMN_INWARD_MM
    d = params.COLUMN_DEPTH_MM
    wall = params.COLUMN_WALL_MM

    # Outer body
    outer = Box(w, d, length, align=(Align.MIN, Align.MIN, Align.MIN)).locate(
        Location((0.0, 0.0, bottom))
    )

    # Central tie-rod bore
    total_len = length + params.SPLICE_ENGAGEMENT_MM + 2.0
    bore = Cylinder(
        radius=params.TIE_BORE_DIA_MM / 2,
        height=total_len,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(Location((params.BORE_CENTER_X_MM, params.BORE_CENTER_Y_MM, bottom - 1.0)))

    # Lower mount socket (mating with phase2_corner_mount spigot)
    socket_w = w - 2 * wall - 0.5 + 2 * params.LOWER_MOUNT_SOCKET_CLEARANCE_MM
    socket_d = d - 2 * wall - 0.5 + 2 * params.LOWER_MOUNT_SOCKET_CLEARANCE_MM
    socket = Box(
        socket_w,
        socket_d,
        params.LOWER_MOUNT_SPIGOT_HEIGHT_MM + 1.0,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(Location((w / 2, d / 2, bottom - 1.0)))

    # Top male spigot for middle splice
    spigot_w = w - 2 * params.SPLICE_CLEARANCE_PER_SIDE_MM - 2 * wall
    spigot_d = d - 2 * params.SPLICE_CLEARANCE_PER_SIDE_MM - 2 * wall
    spigot = Box(
        spigot_w,
        spigot_d,
        params.SPLICE_ENGAGEMENT_MM + params.JOINT_OVERLAP_MM,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(Location((w / 2, d / 2, top - params.JOINT_OVERLAP_MM)))

    # Rail rebate from bottom through the top of the spigot
    rebate = _rail_rebate(bottom - 1.0, top + params.SPLICE_ENGAGEMENT_MM + 2.0, right)

    part = cast(Part, outer - socket + spigot - bore - rebate)

    # Add M3 screw holes and captive nut slots
    for fz in params.RAIL_FASTENER_Z_LOWER_MM:
        part = cast(
            Part,
            part - _m3_screw_hole(fz, right) - _m3_nut_slot(fz, right),
        )

    for y_pos in (7.5, 22.5):
        part = cast(
            Part,
            part - _m4_insert_hole("x", y_pos, params.SIDE_RESTRAINT_Z_MM, right),
        )
    part = cast(
        Part,
        part
        - _m4_insert_hole(
            "y",
            params.COLUMN_DEPTH_MM,
            params.REAR_LOWER_CROSSBAR_Z_MM - 5.0 + params.REAR_CROSSBAR_HEIGHT_MM / 2,
            right,
        ),
    )

    return cast(Part, part.moved(Location((0, 0, -bottom))))


def rev3_upper_column(right: bool = False) -> Part:
    """Upper structural column from splice (z=169.85) to top lid plane (z=355.6)."""
    bottom = corner.SPLICE_Z
    top = params.ADDED_HEIGHT_MM
    length = top - bottom
    w = params.COLUMN_INWARD_MM
    d = params.COLUMN_DEPTH_MM
    wall = params.COLUMN_WALL_MM

    outer = Box(w, d, length, align=(Align.MIN, Align.MIN, Align.MIN)).locate(
        Location((0.0, 0.0, bottom))
    )

    # Central tie-rod bore
    bore = Cylinder(
        radius=params.TIE_BORE_DIA_MM / 2,
        height=length + 2.0,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(Location((params.BORE_CENTER_X_MM, params.BORE_CENTER_Y_MM, bottom - 1.0)))

    # Middle splice female socket
    socket_w = w - 2 * wall + 2 * params.SPLICE_CLEARANCE_PER_SIDE_MM
    socket_d = d - 2 * wall + 2 * params.SPLICE_CLEARANCE_PER_SIDE_MM
    socket = Box(
        socket_w,
        socket_d,
        params.SPLICE_ENGAGEMENT_MM + 1.0,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(Location((w / 2, d / 2, bottom - 1.0)))

    # Top washer and nyloc hardware pocket: 20.5 mm diameter continuous bore to top
    seat_z = params.top_bearing_seat_z_mm()
    hardware_access = Cylinder(
        radius=params.WASHER_RECESS_DIA_MM / 2,
        height=top - seat_z + 2.0,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(Location((params.BORE_CENTER_X_MM, params.BORE_CENTER_Y_MM, seat_z)))

    # Rail rebate
    rebate = _rail_rebate(bottom, top - params.PATH_A_TOP_FRAME_MM, right)

    part = cast(Part, outer - socket - bore - hardware_access - rebate)

    for y_pos in (7.5, 22.5):
        for z_pos in params.TOP_SIDE_FASTENER_Z_MM:
            part = cast(Part, part - _m4_insert_hole("x", y_pos, z_pos, right))

    # Add M3 screw holes and captive nut slots
    for fz in params.RAIL_FASTENER_Z_UPPER_MM:
        part = cast(
            Part,
            part - _m3_screw_hole(fz, right) - _m3_nut_slot(fz, right),
        )

    part = cast(
        Part,
        part
        - _m4_insert_hole(
            "y",
            params.COLUMN_DEPTH_MM,
            params.REAR_UPPER_CROSSBAR_Z_MM + params.REAR_CROSSBAR_HEIGHT_MM / 2,
            right,
        ),
    )

    return cast(Part, part.moved(Location((0, 0, -bottom))))


def rev3_lower_column_left() -> Part:
    return rev3_lower_column(right=False)


def rev3_lower_column_right() -> Part:
    from build123d import Location, Plane

    left = rev3_lower_column(right=False)
    mid_x = params.COLUMN_INWARD_MM / 2
    return cast(
        Part,
        left.moved(Location((-mid_x, 0, 0)))
        .mirror(Plane.YZ)
        .moved(Location((mid_x, 0, 0))),
    )


def rev3_upper_column_left() -> Part:
    return rev3_upper_column(right=False)


def rev3_upper_column_right() -> Part:
    from build123d import Location, Plane

    left = rev3_upper_column(right=False)
    mid_x = params.COLUMN_INWARD_MM / 2
    return cast(
        Part,
        left.moved(Location((-mid_x, 0, 0)))
        .mirror(Plane.YZ)
        .moved(Location((mid_x, 0, 0))),
    )


def rev3_corner_mount(rear: bool = False, right: bool = False) -> Part:
    """T1 interface corner mount with continuous rail rebate and exact M4 hole datums."""
    from build123d import Location, Plane

    w = params.COLUMN_INWARD_MM
    d = params.COLUMN_DEPTH_MM
    wall = params.COLUMN_WALL_MM
    spigot_h = params.LOWER_MOUNT_SPIGOT_HEIGHT_MM
    mount_top = params.BOTTOM_BLOCK_TOP_Z_MM + spigot_h

    y0 = params.BODY_DEPTH_MM - d if rear else 0.0
    cy_tie = y0 + d / 2

    base = Box(
        w, d, params.BOTTOM_BLOCK_TOP_Z_MM, align=(Align.MIN, Align.MIN, Align.MIN)
    ).locate(Location((0, y0, 0)))

    lip_th = params.LIP_THICK_MM
    lip_dp = params.LIP_DEPTH_MM
    if rear:
        lip_y = y0 - 12.0
        lip_len = 12.0
    else:
        lip_y = params.CORNER_LIP_START_MM
        lip_len = d + 12.0 - lip_y
    lip_out = Box(
        lip_th,
        lip_len,
        lip_dp + params.FRAME_ZONE_MM,
        align=(Align.MIN, Align.MIN, Align.MIN),
    ).locate(Location((-lip_th, lip_y, -lip_dp)))
    lip_in = Box(
        lip_th,
        lip_len,
        lip_dp + params.FRAME_ZONE_MM,
        align=(Align.MIN, Align.MIN, Align.MIN),
    ).locate(Location((params.coupon_channel_mm(), lip_y, -lip_dp)))
    root_y = lip_y - params.JOINT_OVERLAP_MM
    root_len = lip_len + 2 * params.JOINT_OVERLAP_MM
    lip_root_out = Box(
        lip_th + params.JOINT_OVERLAP_MM,
        root_len,
        params.FRAME_ZONE_MM,
        align=(Align.MIN, Align.MIN, Align.MIN),
    ).locate(Location((-lip_th, root_y, 0)))
    bridge = Box(
        params.coupon_channel_mm() + lip_th - (w - params.JOINT_OVERLAP_MM),
        root_len,
        params.FRAME_ZONE_MM,
        align=(Align.MIN, Align.MIN, Align.MIN),
    ).locate(Location((w - params.JOINT_OVERLAP_MM, root_y, 0)))

    spigot_w = w - 2 * wall - 0.5
    spigot_d = d - 2 * wall - 0.5
    spigot = Box(
        spigot_w,
        spigot_d,
        spigot_h + params.JOINT_OVERLAP_MM,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(
        Location(
            (
                w / 2,
                cy_tie,
                params.BOTTOM_BLOCK_TOP_Z_MM - params.JOINT_OVERLAP_MM,
            )
        )
    )

    pad_y = 28.0 if not rear else 152.0
    attachment_pad = Box(
        w - 14.0,
        18.0,
        params.ATTACH_SEAT_Z_MM,
        align=(Align.MIN, Align.MIN, Align.MIN),
    ).locate(Location((14.0, pad_y, 0.0)))

    part = base + lip_out + lip_in + lip_root_out + bridge + spigot + attachment_pad

    tie_bore = Cylinder(
        radius=params.TIE_BORE_DIA_MM / 2,
        height=mount_top + lip_dp + 2.0,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(Location((params.BORE_CENTER_X_MM, cy_tie, -lip_dp - 1.0)))

    hw_open = Cylinder(
        radius=params.WASHER_RECESS_DIA_MM / 2,
        height=(
            params.HEX_POCKET_FLOOR_Z_MM
            + params.HEX_POCKET_DEPTH_MM
            + params.WASHER_RECESS_DEPTH_MM
            + 1.0
        ),
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).locate(Location((params.BORE_CENTER_X_MM, cy_tie, -1.0)))

    key = Box(
        params.BOTTOM_RETAINER_KEY_LENGTH_MM + params.BOTTOM_RETAINER_CLEARANCE_MM,
        params.BOTTOM_RETAINER_KEY_WIDTH_MM + 2 * params.BOTTOM_RETAINER_CLEARANCE_MM,
        params.HEX_POCKET_FLOOR_Z_MM + params.HEX_POCKET_DEPTH_MM,
        align=(Align.MIN, Align.CENTER, Align.MIN),
    ).locate(
        Location(
            (params.BORE_CENTER_X_MM + params.BOTTOM_RETAINER_KEY_START_X_MM, cy_tie, 0)
        )
    )

    y_holes = params.ATTACH_Y_MM[2:] if rear else params.ATTACH_Y_MM[:2]
    for yh in y_holes:
        m4_hole = Cylinder(
            radius=params.COUPON_HOLE_DIA_MM / 2,
            height=mount_top + 2.0,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        ).locate(Location((params.STRUCTURAL_HOLE_X_LEFT_MM, yh, -lip_dp - 1.0)))
        access = Cylinder(
            radius=params.ATTACH_ACCESS_DIA_MM / 2,
            height=mount_top - params.ATTACH_SEAT_Z_MM + 1.0,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        ).locate(
            Location((params.STRUCTURAL_HOLE_X_LEFT_MM, yh, params.ATTACH_SEAT_Z_MM))
        )
        part = part - m4_hole - access

    part = part - tie_bore - hw_open - key

    if not rear:
        rebate = Box(
            params.RAIL_WIDTH_MM + 0.3,
            1.0 + params.RAIL_REBATE_DEPTH_MM + 0.2,
            mount_top + 2.0 - params.PATH_A_BOTTOM_FRAME_MM,
            align=(Align.MIN, Align.MIN, Align.MIN),
        ).locate(
            Location(
                (
                    params.RAIL_X_START_MM - 0.15,
                    -1.0,
                    params.PATH_A_BOTTOM_FRAME_MM,
                )
            )
        )
        part = part - rebate

    if right:
        mid_x = params.COLUMN_INWARD_MM / 2
        part = (
            part.moved(Location((-mid_x, 0, 0)))
            .mirror(Plane.YZ)
            .moved(Location((mid_x, 0, 0)))
        )

    return cast(Part, part.moved(Location((0, -y0, 0))))


def rev3_corner_mount_front_left() -> Part:
    return rev3_corner_mount(rear=False, right=False)


def rev3_corner_mount_front_right() -> Part:
    return rev3_corner_mount(rear=False, right=True)


def rev3_corner_mount_rear_left() -> Part:
    return rev3_corner_mount(rear=True, right=False)


def rev3_corner_mount_rear_right() -> Part:
    return rev3_corner_mount(rear=True, right=True)
