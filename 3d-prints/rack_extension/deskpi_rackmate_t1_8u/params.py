"""Parametric source of truth: DeskPi RackMate T1 8U upper extension.

Preliminary CAD per PRD v0.6.4. All values below are either physically
measured (see status table in the PRD) or explicit design assumptions.
Nothing in this file claims production readiness; unresolved audit gates
are listed in README.md and reported by model.audit_gates().
"""

from __future__ import annotations

from dataclasses import dataclass

# --- T1 envelope (physically measured) ---------------------------------------
BODY_WIDTH_MM = 281.0
BODY_DEPTH_MM = 200.0
TOP_MEMBER_WIDTH_MM = 30.0
TOP_PLATE_THICK_MM = 4.4

# --- Rack geometry ------------------------------------------------------------
U_PITCH_MM = 44.45
EXTENSION_U = 8
ADDED_HEIGHT_MM = U_PITCH_MM * EXTENSION_U  # 355.6
STOCK_CLEAR_OPENING_MM = 222.5
RACK_HOLE_SPACING_MM = 236.0
RACK_HOLE_CENTER_X_MM = (BODY_WIDTH_MM - RACK_HOLE_SPACING_MM) / 2
MAX_INWARD_PROJECTION_PER_SIDE_MM = (BODY_WIDTH_MM - STOCK_CLEAR_OPENING_MM) / 2

# Stock rail phase, z=0 at original lid-support plane, +z upward (measured).
LID_TO_TOP_HOLE_MM = 28.8
HOLE_1_TO_2_MM = 15.9
HOLE_2_TO_3_MM = 15.9
HOLE_3_TO_4_MM = 12.7

# --- Lower attachment interface ----------------------------------------------
STRUCTURAL_HOLE_X_LEFT_MM = 22.8
STRUCTURAL_HOLE_X_RIGHT_MM = 258.2
HANDLE_HOLE_X_LEFT_MM = 13.0
HANDLE_HOLE_X_RIGHT_MM = 268.0
HANDLE_HOLE_Y_FRONT_MM = 34.5
HANDLE_HOLE_Y_REAR_MM = 165.5
HANDLE_SCREW_SPACING_MEASURED_MM = 131.0
ATTACH_Y_MM = (25.0, 38.0, 157.0, 170.0)
LOWER_ATTACH_THREAD = "M4x0.7"

# --- Frames -------------------------------------------------------------------
FRAME_ZONE_MM = 12.0

# Path A: conventional end bearing (PRD section 12). Frames are contained
# *within* the 355.6 mm lid-support-plane spacing.
PATH_A_BOTTOM_FRAME_MM = 12.0
PATH_A_TOP_FRAME_MM = 12.0
PATH_A_CLEAR_BODY_MM = (
    ADDED_HEIGHT_MM - PATH_A_BOTTOM_FRAME_MM - PATH_A_TOP_FRAME_MM
) / 2


def path_a_stack_mm() -> float:
    """Assembled stack height; must equal ADDED_HEIGHT_MM exactly."""
    return PATH_A_BOTTOM_FRAME_MM + 2 * PATH_A_CLEAR_BODY_MM + PATH_A_TOP_FRAME_MM


# --- Columns ------------------------------------------------------------------
COLUMN_DEPTH_MM = 30.0
COLUMN_INWARD_MM = 29.0
COLUMN_WALL_MM = 4.0
TIE_BORE_DIA_MM = 6.5

# --- Splice -------------------------------------------------------------------
SPLICE_ENGAGEMENT_MM = 22.0
SPLICE_CLEARANCE_PER_SIDE_MM = 0.25
SPIGOT_SHOULDER_MM = 3.0
SPLICE_Z_MM = 169.85

# --- Rack inserts --------------------------------------------------------------
INSERT_THREAD = "M5x0.8"
INSERT_OD_MM = 6.7
INSERT_LEN_MM = 7.9
BOSS_DEPTH_TARGET_MM = 9.3
BOSS_OD_MIN_MM = 12.0
BOSS_OD_MAX_MM = 18.0

# --- Tie-rod hardware (purchased, physically measured where noted) -------------
M5_WASHER_ID_MM = 5.3
M5_WASHER_OD_MM = 20.0
M5_WASHER_THICK_MM = 1.2
M5_NYLOC_HEIGHT_MM = 7.8
FULL_THREAD_PROTRUSION_MM = 1.6
TIP_ALLOWANCE_MM = 0.0
EXTERIOR_CLEARANCE_MM = 0.2
ROD_STOCK_LEN_MM = 400.0


def pocket_depth_required_mm() -> float:
    """Required pocket depth per end (illustrative, PRD section 34)."""
    return (
        M5_WASHER_THICK_MM
        + M5_NYLOC_HEIGHT_MM
        + FULL_THREAD_PROTRUSION_MM
        + TIP_ALLOWANCE_MM
        + EXTERIOR_CLEARANCE_MM
    )


def seat_spacing_illustrative_mm() -> float:
    """Washer-seat spacing for the illustrative pocket geometry."""
    return ADDED_HEIGHT_MM - 2 * pocket_depth_required_mm()


def rod_length_illustrative_mm() -> float:
    """Idealized rod length L = S + 2*(W+N+P+T) for the example geometry."""
    return seat_spacing_illustrative_mm() + 2 * (
        M5_WASHER_THICK_MM
        + M5_NYLOC_HEIGHT_MM
        + FULL_THREAD_PROTRUSION_MM
        + TIP_ALLOWANCE_MM
    )


def residual_web_illustrative_mm() -> float:
    """Frame material left behind a nominal 12 mm pocket (must not be relied on)."""
    return FRAME_ZONE_MM - pocket_depth_required_mm()


# --- Thread engagement (M-04, peer-reported) --------------------------------------
# Peer agent reports the top-frame holes as through-threaded in 5.8 mm bar
# stock: no blind bottom, max engagement ~5.8 mm. Provenance is peer report,
# not a direct measurement in this session; engagement target is derated to
# 4.5-5.0 mm (≈6-7 threads of M4 x 0.7) for tolerance. Screw length rule:
# printed boss thickness + target engagement; protrusion past the printed
# boss must stay below 5.8 mm. Final screw length stays TBD until the
# printed attachment boss thickness is designed.
USABLE_THREAD_DEPTH_MM: float | None = 5.8
THREAD_ENGAGEMENT_MIN_MM = 4.5
THREAD_ENGAGEMENT_MAX_MM = 5.0

# --- Closed physical measurements --------------------------------------------------
# M-06 measured 2026-09-16: ~5.9 mm flat bar, top face completely flat
# (earlier ~10 mm was an overestimate). Consistent with the peer-reported
# 5.8 mm through-thread (M-04). M-11 closed same date: top face flat
# around all attachment holes, no countersink or ribs. M-12 closed earlier:
# source photo filed as ../m12_top_frame.jpg, notes in
# ../m12_top_frame_notes.md; residual seating risk rides on the coupon fit.
TOP_MEMBER_THICK_APPROX_MM = 5.9

# Anti-tip decision (G6, 2026-09-16, revised same date): the finished rack
# is free-standing with no wall attachment. Stability therefore rests on
# footprint, low center of gravity (heavy equipment stays in the stock
# lower T1 per the placement strategy), and the staged ballast test.
# No tether hardware or CAD mounting points will be added in Revision 1.
ANTI_TIP_STRATEGY = "free-standing (no wall attachment)"


def open_measurements() -> list[str]:
    """Measurement IDs still blocking production geometry."""
    return []


# The bottom mount ends between the 28.4 and 44.3 mm bosses. The upper
# washer bears at z=344.8; its loading opening extends through the lid plane.
HEX_POCKET_AF_MM = 8.3
HEX_POCKET_DEPTH_MM = 8.0
HEX_POCKET_FLOOR_Z_MM = 1.6
WASHER_RECESS_DIA_MM = 20.5
WASHER_RECESS_DEPTH_MM = 1.4
TOP_NUT_POCKET_DIA_MM = 12.0
TOP_NUT_POCKET_DEPTH_MM = 9.4
BOTTOM_BLOCK_TOP_Z_MM = 36.35
TOP_BLOCK_BOTTOM_Z_MM = 330.0
RAIL_STRIP_HALF_MM = 10.0
BOSS_OD_NOMINAL_MM = 15.0
INSERT_PILOT_DIA_MM = 6.2
MAX_SCREW_PENETRATION_MM = 9.0
BORE_CENTER_X_MM = 14.5
BORE_CENTER_Y_MM = 15.0

# M5 rack-insert hardware measured 2026-09-23. The 6.2 mm pilot was physically
# accepted in PETG on 2026-09-24; 5.8 and 6.0 mm would not accept the insert.
M5_INSERT_LEN_MEASURED_MM = 7.9
M5_INSERT_OD_MEASURED_MM = 6.7
M5_INSERT_LEAD_DIA_MEASURED_MM = 5.8
M5_INSERT_PILOT_CANDIDATES_MM = (5.8, 6.0, 6.2)
M5_INSERT_COUPON_DEPTH_MM = 9.3
M5_INSERT_PILOT_ACCEPTED_MM = 6.2


def bottom_pocket_depth_mm() -> float:
    """Stack: floor clearance + hex + washer recess."""
    return HEX_POCKET_FLOOR_Z_MM + HEX_POCKET_DEPTH_MM + WASHER_RECESS_DEPTH_MM


def bottom_bearing_seat_z_mm() -> float:
    """Washer bearing face height in the bottom block."""
    return bottom_pocket_depth_mm()


def bottom_rod_end_z_mm() -> float:
    """Rod tip: nut bottom (floor+0.2) minus full-thread protrusion."""
    return HEX_POCKET_FLOOR_Z_MM + 0.2 - FULL_THREAD_PROTRUSION_MM


def top_bearing_seat_z_mm() -> float:
    """Washer bearing face height in the top block."""
    return ADDED_HEIGHT_MM - TOP_NUT_POCKET_DEPTH_MM - WASHER_RECESS_DEPTH_MM


def top_rod_end_z_mm() -> float:
    """Rod tip: 0.2 exterior clearance below the lid plane."""
    return ADDED_HEIGHT_MM - EXTERIOR_CLEARANCE_MM


def bore_wall_front_y_mm() -> float:
    """Front wall of the M5 bore channel (insert side)."""
    return BORE_CENTER_Y_MM - TIE_BORE_DIA_MM / 2


# --- Phase 1 interface coupon ----------------------------------------------------
# Rail section over one corner attachment pair (Y=25/38) with M4 clearance
# holes, seating face, and side locating lips with print clearance around
# the 30 mm aluminum member. Print upside-down (seating face up): no supports.
COUPON_Y_START_MM = 8.0
COUPON_Y_END_MM = 68.0
LATERAL_COUPON_LENGTH_MM = 12.0
COUPON_HOLE_DIA_MM = 4.5
LIP_CLEARANCE_MM = 0.15
LIP_THICK_MM = 2.0
LIP_DEPTH_MM = 3.0
LOWER_MOUNT_SPIGOT_HEIGHT_MM = 12.0
LOWER_MOUNT_SOCKET_CLEARANCE_MM = 0.25
ATTACH_ACCESS_DIA_MM = 8.0
ATTACH_SEAT_Z_MM = 11.5
M4_MOUNT_SCREW_LEN_MM = 16.0
CORNER_LIP_START_MM = TOP_MEMBER_WIDTH_MM + 2 * LIP_CLEARANCE_MM
BOTTOM_RETAINER_CLEARANCE_MM = 0.2
BOTTOM_RETAINER_KEY_LENGTH_MM = 4.0
BOTTOM_RETAINER_KEY_WIDTH_MM = 4.0
BOTTOM_RETAINER_KEY_START_X_MM = 8.0


def coupon_channel_mm() -> float:
    """Lip-to-lip channel the aluminum member must fit."""
    return TOP_MEMBER_WIDTH_MM + 2 * LIP_CLEARANCE_MM


def mount_thread_engagement_mm() -> float:
    return M4_MOUNT_SCREW_LEN_MM - ATTACH_SEAT_Z_MM


# --- Decoupled Equipment Rail (Option 1) ---------------------------------------
RAIL_WIDTH_MM = 16.0
RAIL_THICKNESS_MM = 9.5
RAIL_REBATE_DEPTH_MM = 9.5
RAIL_X_START_MM = 13.0
RAIL_X_END_MM = 29.0
RAIL_SPLICE_Z_MM = 169.85
RAIL_LOWER_Z_START_MM = PATH_A_BOTTOM_FRAME_MM  # 12.0
RAIL_LOWER_Z_END_MM = RAIL_SPLICE_Z_MM  # 169.85
RAIL_UPPER_Z_START_MM = RAIL_SPLICE_Z_MM  # 169.85
RAIL_UPPER_Z_END_MM = ADDED_HEIGHT_MM - PATH_A_TOP_FRAME_MM  # 343.6
RAIL_FASTENER_DIA_MM = 3.5  # M3 clearance
RAIL_FASTENER_HEAD_DIA_MM = 6.5  # M3 socket head counterbore
RAIL_FASTENER_HEAD_DEPTH_MM = 3.5
RAIL_FASTENER_X_MM = 22.5  # aligned with rack hole line, clearing M5 tie rod by 1.85 mm
RAIL_FASTENER_Z_LOWER_MM = (52.25, 96.75, 141.25)
RAIL_FASTENER_Z_UPPER_MM = (214.35, 258.85, 303.35, 319.25)

# --- Captive M3 Nut Slots in Column (Option A) ---------------------------------
M3_NUT_SLOT_WIDTH_MM = 5.8  # across flats + clearance
M3_NUT_SLOT_THICK_MM = 2.7  # nut thickness + clearance
M3_NUT_SLOT_Y_MM = 12.5  # centered behind 3.0 mm column face wall

# --- Frame Bracing and Restraint (Option 1) ------------------------------------
REAR_CROSSBAR_HEIGHT_MM = 20.0
REAR_CROSSBAR_THICK_MM = 10.0
REAR_CROSSBAR_SPAN_MM = BODY_WIDTH_MM - 2 * COLUMN_INWARD_MM  # 223.0 mm
REAR_CROSSBAR_FASTENER_X_LEFT_MM = 23.0  # 5.25 mm clear of M5 tie rod
REAR_CROSSBAR_FASTENER_X_RIGHT_MM = BODY_WIDTH_MM - 23.0  # 258.0 mm
REAR_LOWER_CROSSBAR_Z_MM = 50.0
REAR_UPPER_CROSSBAR_Z_MM = 325.0

REAR_BRACE_WIDTH_MM = 18.0
REAR_BRACE_THICK_MM = 6.0
REAR_BRACE_FASTENER_DIA_MM = 4.5  # M4 clearance
BRACE_FASTENER_HEAD_DIA_MM = 8.5
M4_BRACE_SCREW_THREAD = "M4x0.7"
M4_BRACE_SCREW_DIA_MM = 4.0
M4_BRACE_SCREW_LENGTH_MM = 16.0
M4_BRACE_SCREW_HEAD_HEIGHT_MM = 4.0
M4_FRAME_CLEARANCE_DIA_MM = 4.5
M4_HEAT_SET_PILOT_DIA_MM = 5.8
M4_HEAT_SET_DEPTH_MM = 6.0
M4_HEAT_SET_OD_MM = 6.0
M4_HEAT_SET_BORE_DEPTH_MM = 11.0
M4_TIP_CLEARANCE_MM = 1.0
M4_FRAME_HARDWARE_SCHEDULE = {
    "column_to_side_restraint": "M4x16, 4.5 mm bar clearance, 6x6 mm insert in 5.8 mm column pilot",
    "column_to_rear_crossbar": "M4x16, recessed head, 4.5 mm crossbar clearance, 6x6 mm insert in column",
    "crossbar_to_diagonal": "M4x16, unrecessed head, 4.5 mm diagonal clearance, 6x6 mm insert in crossbar",
    "column_to_upper_side_beam": "M4x16, 4.5 mm beam clearance, 6x6 mm insert in 5.8 mm column pilot",
    "lid_to_side_beam": "M4x20 same head style as stock, approximately 7 mm protrusion, confirm physically",
}

# Parametric diagonal brace anchors on the crossbars
DIAGONAL_ANCHOR_LOWER_X_MM = 35.0
DIAGONAL_ANCHOR_LOWER_Z_MM = 55.0
DIAGONAL_ANCHOR_UPPER_X_MM = 246.0
DIAGONAL_ANCHOR_UPPER_Z_MM = 335.0
DIAGONAL_SPAN_MM = (
    (DIAGONAL_ANCHOR_UPPER_X_MM - DIAGONAL_ANCHOR_LOWER_X_MM) ** 2
    + (DIAGONAL_ANCHOR_UPPER_Z_MM - DIAGONAL_ANCHOR_LOWER_Z_MM) ** 2
) ** 0.5  # 374.995 mm (~375.0 mm)
DIAGONAL_LAP_LEN_MM = 30.0
DIAGONAL_HALF_LEN_MM = DIAGONAL_SPAN_MM / 2 + DIAGONAL_LAP_LEN_MM / 2 + 10.0  # 212.5 mm

SIDE_RESTRAINT_HEIGHT_MM = 20.0
SIDE_RESTRAINT_THICK_MM = 6.0
SIDE_RESTRAINT_SPAN_MM = BODY_DEPTH_MM - 2 * COLUMN_DEPTH_MM  # 140.0 mm
SIDE_RESTRAINT_Z_MM = 145.0  # positioned in lower column body, clear of splice

TOP_SIDE_PLATE_THICK_MM = 6.0
TOP_SIDE_BEAM_HEIGHT_MM = 42.0
TOP_SIDE_FASTENER_Z_MM = (319.6, 336.6)
TOP_SIDE_FASTENER_Y_MM = (22.5, 177.5)
LID_INSERT_BORE_DEPTH_MM = 11.0
LID_SCREW_LENGTH_MM = 20.0
LID_SCREW_PROTRUSION_APPROX_MM = 7.0

# --- Joint overlap -----------------------------------------------------------------
# Boolean unions need volumetric overlap, not face touching: every
# additive joint embeds by JOINT_OVERLAP_MM or the slicer sees separate
# shells (found on the Phase 1 coupon lips, 2026-09-17).
JOINT_OVERLAP_MM = 2.0

# --- Manufacturing --------------------------------------------------------------
BED_X_MM = 256.0
BED_Y_MM = 256.0
BED_Z_MM = 256.0


def fits_bed(x_mm: float, y_mm: float, z_mm: float) -> bool:
    """Axis-aligned A1 fit check (rotation handled by caller)."""
    dims = sorted((x_mm, y_mm, z_mm))
    bed = sorted((BED_X_MM, BED_Y_MM, BED_Z_MM))
    return all(d <= b + 1e-9 for d, b in zip(dims, bed))


# --- Seam: theoretical continuation positions ------------------------------------
# Upward gap order is 12.7, 15.9, 15.9: the reverse of the downward-measured
# 15.9, 15.9, 12.7 triple, by 44.45 mm periodicity. Upward from hole 1 at
# -28.8 mm this gives -16.1, -0.2, +15.7, +28.4, ... (NOT -12.9/+3.0).
UP_GAP_CYCLE_MM = (HOLE_3_TO_4_MM, HOLE_1_TO_2_MM, HOLE_2_TO_3_MM)


def theoretical_hole_centers_below_or_near_seam() -> list[float]:
    """z positions of the stock-pattern continuation near z=0 (lid plane)."""
    top = -LID_TO_TOP_HOLE_MM
    out = [top]
    i = 0
    while out[-1] < ADDED_HEIGHT_MM + U_PITCH_MM:
        out.append(out[-1] + UP_GAP_CYCLE_MM[i % 3])
        i += 1
    return out


@dataclass(frozen=True)
class CradleEnvelope:
    x_mm: float
    y_mm: float
    z_mm: float


def column_module_envelope() -> CradleEnvelope:
    """Largest height of the two printable upright prototypes."""
    return CradleEnvelope(
        x_mm=COLUMN_INWARD_MM,
        y_mm=COLUMN_DEPTH_MM,
        z_mm=max(
            SPLICE_Z_MM - BOTTOM_BLOCK_TOP_Z_MM + SPLICE_ENGAGEMENT_MM,
            ADDED_HEIGHT_MM - SPLICE_Z_MM,
        ),
    )


def rail_lower_envelope() -> CradleEnvelope:
    """Bounding envelope for the lower equipment rail."""
    return CradleEnvelope(
        x_mm=RAIL_WIDTH_MM,
        y_mm=RAIL_THICKNESS_MM,
        z_mm=RAIL_LOWER_Z_END_MM - RAIL_LOWER_Z_START_MM,
    )


def rail_upper_envelope() -> CradleEnvelope:
    """Bounding envelope for the upper equipment rail."""
    return CradleEnvelope(
        x_mm=RAIL_WIDTH_MM,
        y_mm=RAIL_THICKNESS_MM,
        z_mm=RAIL_UPPER_Z_END_MM - RAIL_UPPER_Z_START_MM,
    )


def rear_crossbar_envelope() -> CradleEnvelope:
    """Bounding envelope for the rear horizontal crossbars."""
    return CradleEnvelope(
        x_mm=REAR_CROSSBAR_SPAN_MM + 2 * (COLUMN_INWARD_MM - 14.0),
        y_mm=REAR_CROSSBAR_THICK_MM,
        z_mm=REAR_CROSSBAR_HEIGHT_MM,
    )


def side_restraint_envelope() -> CradleEnvelope:
    """Bounding envelope for the mid-height side restraint bars."""
    return CradleEnvelope(
        x_mm=SIDE_RESTRAINT_THICK_MM,
        y_mm=SIDE_RESTRAINT_SPAN_MM + 2 * (COLUMN_DEPTH_MM - 10.0),
        z_mm=SIDE_RESTRAINT_HEIGHT_MM,
    )


def validate() -> list[str]:
    """Return a list of failed invariant descriptions (empty = pass)."""
    errors: list[str] = []
    if abs(ADDED_HEIGHT_MM - 355.6) > 1e-9:
        errors.append("8U datum must be exactly 355.6 mm")
    if abs(path_a_stack_mm() - ADDED_HEIGHT_MM) > 1e-9:
        errors.append("Path A stack must equal the 355.6 mm envelope")
    if COLUMN_INWARD_MM > MAX_INWARD_PROJECTION_PER_SIDE_MM + 1e-9:
        errors.append("column inward projection narrows the stock opening")
    if (
        not INSERT_OD_MM / 2
        < RACK_HOLE_CENTER_X_MM
        < COLUMN_INWARD_MM - INSERT_OD_MM / 2
    ):
        errors.append("rack insert must fit within the column width")
    if not fits_bed(
        column_module_envelope().x_mm,
        column_module_envelope().y_mm,
        column_module_envelope().z_mm,
    ):
        errors.append("column module exceeds the A1 bed")
    if not fits_bed(
        rail_lower_envelope().x_mm,
        rail_lower_envelope().y_mm,
        rail_lower_envelope().z_mm,
    ):
        errors.append("lower equipment rail exceeds the A1 bed")
    if not fits_bed(
        rail_upper_envelope().x_mm,
        rail_upper_envelope().y_mm,
        rail_upper_envelope().z_mm,
    ):
        errors.append("upper equipment rail exceeds the A1 bed")
    if not fits_bed(
        rear_crossbar_envelope().x_mm,
        rear_crossbar_envelope().y_mm,
        rear_crossbar_envelope().z_mm,
    ):
        errors.append("rear crossbar exceeds the A1 bed")
    if not fits_bed(
        side_restraint_envelope().x_mm,
        side_restraint_envelope().y_mm,
        side_restraint_envelope().z_mm,
    ):
        errors.append("side restraint exceeds the A1 bed")
    if not (RAIL_X_START_MM <= RACK_HOLE_CENTER_X_MM <= RAIL_X_END_MM):
        errors.append("rack hole center must fall inside the equipment rail")
    if not (RAIL_X_START_MM <= RAIL_FASTENER_X_MM <= RAIL_X_END_MM):
        errors.append("rail fastener must fall inside the equipment rail")
    if abs(STRUCTURAL_HOLE_X_RIGHT_MM - (BODY_WIDTH_MM - 22.8)) > 1e-9:
        errors.append("right structural column must mirror the left at 258.2 mm")
    if abs(HANDLE_HOLE_X_RIGHT_MM - (BODY_WIDTH_MM - 13.0)) > 1e-9:
        errors.append("right handle column must mirror the left at 268.0 mm")
    if (
        abs(
            (HANDLE_HOLE_Y_REAR_MM - HANDLE_HOLE_Y_FRONT_MM)
            - HANDLE_SCREW_SPACING_MEASURED_MM
        )
        > 1e-9
    ):
        errors.append("handle-hole Y spacing must match measured 131.0 mm")
    if BOSS_DEPTH_TARGET_MM <= INSERT_LEN_MM:
        errors.append("boss depth must contain the rack insert")
    if USABLE_THREAD_DEPTH_MM is not None and not (
        0
        < THREAD_ENGAGEMENT_MIN_MM
        <= THREAD_ENGAGEMENT_MAX_MM
        < USABLE_THREAD_DEPTH_MM
    ):
        errors.append("thread engagement target must stay below usable depth")
    if not bottom_pocket_depth_mm() < BOTTOM_BLOCK_TOP_Z_MM:
        errors.append("bottom pocket must fit inside the end block")
    if not bottom_rod_end_z_mm() >= 0.0:
        errors.append("bottom rod tip must stay above the T1 seat plane")
    if not top_rod_end_z_mm() <= ADDED_HEIGHT_MM:
        errors.append("top rod tip must stay below the lid plane")
    if not MAX_SCREW_PENETRATION_MM < bore_wall_front_y_mm():
        errors.append("rack screw tip must clear the M5 bore wall")
    if not BOSS_OD_NOMINAL_MM <= BOSS_OD_MAX_MM:
        errors.append("nominal boss must stay within the OD envelope")
    for joint in (BOTTOM_BLOCK_TOP_Z_MM, SPLICE_Z_MM):
        if any(
            abs(z - joint) <= BOSS_OD_NOMINAL_MM / 2
            for z in theoretical_hole_centers_below_or_near_seam()
        ):
            errors.append("joint plane must not split an insert boss")
    if not (COUPON_Y_START_MM < ATTACH_Y_MM[0] and ATTACH_Y_MM[1] < COUPON_Y_END_MM):
        errors.append("coupon must cover the corner attachment pair")
    if not LIP_CLEARANCE_MM > 0.0:
        errors.append("locating lips need positive clearance")
    if not LOWER_MOUNT_SOCKET_CLEARANCE_MM > 0.0:
        errors.append("lower mount socket needs positive clearance")
    if not ATTACH_ACCESS_DIA_MM > COUPON_HOLE_DIA_MM:
        errors.append("M4 attachment access must clear the screw head")
    if (
        not THREAD_ENGAGEMENT_MIN_MM
        <= mount_thread_engagement_mm()
        <= THREAD_ENGAGEMENT_MAX_MM
    ):
        errors.append("M4 mount screw engagement must stay inside the measured target")
    return errors
