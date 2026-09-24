"""Critical corner section checks (G2/G3 computation, PRD sections 12/14).

Answers, in numbers, where the printed corner must merge structure that
cannot fit side by side: first-hole boss vs lower frame/end-block,
splice-interface hole vs spigot joint, top-hole boss vs frame/nut pocket.
Findings marked BLOCKING require resolved CAD geometry before production;
NOTE findings constrain detailed design.
"""

from __future__ import annotations

from . import params, seam

SPLICE_Z = params.SPLICE_Z_MM
BOTTOM_FRAME_TOP_Z = params.PATH_A_BOTTOM_FRAME_MM
TOP_FRAME_BOTTOM_Z = params.ADDED_HEIGHT_MM - params.PATH_A_TOP_FRAME_MM


def boss_extent(hole_z: float, od_mm: float) -> tuple[float, float]:
    """Axial z-extent of an insert boss of given OD around a hole center."""
    return (hole_z - od_mm / 2, hole_z + od_mm / 2)


def bottom_pocket_zone() -> tuple[float, float]:
    """Illustrative bottom tie-rod pocket z-range (G2)."""
    depth = params.pocket_depth_required_mm()
    return (0.0, depth)


def top_pocket_zone() -> tuple[float, float]:
    """Illustrative top tie-rod pocket z-range (G2)."""
    depth = params.pocket_depth_required_mm()
    return (params.ADDED_HEIGHT_MM - depth, params.ADDED_HEIGHT_MM)


def overlaps(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """True when two z-ranges overlap with positive length."""
    return min(a[1], b[1]) - max(a[0], b[0]) > 1e-9


def corner_findings() -> list[str]:
    """Dimensioned interference verdicts for the critical corner."""
    findings: list[str] = []
    first = seam.first_extension_hole()
    for od in (params.BOSS_OD_MIN_MM, params.BOSS_OD_MAX_MM):
        lo, hi = boss_extent(first, od)
        if lo < BOTTOM_FRAME_TOP_Z:
            findings.append(
                f"BLOCKING: first-hole boss (OD {od:.0f}, z {lo:.1f}..{hi:.1f}) "
                f"overlaps the {BOTTOM_FRAME_TOP_Z:.0f} mm frame/end-block "
                "zone: merge into one solid end block, do not web it"
            )
            break
    splice_holes = splice_zone_holes()
    if splice_holes:
        findings.append(
            f"BLOCKING: hole at {splice_holes[0]:.1f} sits on the splice "
            f"interface (z={SPLICE_Z:.1f}): split boss across modules as a "
            "continuous rail strip with spigot relief, per PRD section 12"
        )
    last = seam.last_extension_hole()
    lo, hi = boss_extent(last, params.BOSS_OD_MAX_MM)
    if hi > TOP_FRAME_BOTTOM_Z:
        findings.append(
            f"BLOCKING: top-hole boss (z {lo:.1f}..{hi:.1f}) overlaps the "
            f"top frame (from {TOP_FRAME_BOTTOM_Z:.1f}) and the tie-rod "
            "pocket: single reinforced end block must pack washer seat, "
            "nut, insert boss, and M5 bore without intersection"
        )
    if overlaps(boss_extent(last, params.BOSS_OD_MIN_MM), top_pocket_zone()):
        findings.append(
            "NOTE: even the minimum top boss enters the illustrative nut "
            "pocket: pocket depth is not a free parameter (PRD section 14)"
        )
    if params.residual_web_illustrative_mm() < 2.0:
        findings.append(
            f"NOTE: {params.residual_web_illustrative_mm():.1f} mm residual "
            "web behind a nominal pocket is non-structural: end blocks must "
            "extend into the column/frame overlap"
        )
    return findings


def bore_to_boss_clearance_mm(od_mm: float) -> float:
    """Radial clearance between M5 bore wall and worst-case insert boss."""
    boss_front_y = params.BOSS_DEPTH_TARGET_MM
    return params.bore_wall_front_y_mm() - boss_front_y


def rev1_proof() -> list[str]:
    """Analytic proof of the locked Rev-1 corner resolutions.

    Empty return means every resolution is dimensioned and consistent.
    """
    violations: list[str] = []
    first = seam.first_extension_hole()
    lo, hi = boss_extent(first, params.BOSS_OD_MAX_MM)
    if not (0.0 <= lo and hi <= params.BOTTOM_BLOCK_TOP_Z_MM):
        violations.append("first-hole max boss escapes the bottom end block")
    last = seam.last_extension_hole()
    lo, hi = boss_extent(last, params.BOSS_OD_MAX_MM)
    if not (params.TOP_BLOCK_BOTTOM_Z_MM <= lo and hi <= params.ADDED_HEIGHT_MM):
        violations.append("top-hole max boss escapes the top end block")
    if not bore_to_boss_clearance_mm(params.BOSS_OD_MAX_MM) > 0:
        violations.append("M5 bore intersects the insert boss envelope")
    if not params.MAX_SCREW_PENETRATION_MM < params.bore_wall_front_y_mm():
        violations.append("rack screw can reach the M5 bore")
    if not params.bottom_rod_end_z_mm() >= 0.0:
        violations.append("bottom rod tip fouls the T1 seat plane")
    if not params.top_rod_end_z_mm() <= params.ADDED_HEIGHT_MM:
        violations.append("top rod tip fouls the lid plane")
    strip = (
        SPLICE_Z - params.RAIL_STRIP_HALF_MM,
        SPLICE_Z + params.RAIL_STRIP_HALF_MM,
    )
    for h in splice_zone_holes():
        lo, hi = boss_extent(h, params.BOSS_OD_NOMINAL_MM)
        if not (strip[0] <= lo and hi <= strip[1]):
            violations.append(f"splice hole {h:.1f} escapes the rail strip")
    return violations


def splice_zone_holes() -> list[float]:
    """Extension holes whose modeled boss crosses the splice interface plane."""
    return [
        h
        for h in seam.extension_hole_centers()
        if boss_extent(h, params.BOSS_OD_NOMINAL_MM)[0]
        < SPLICE_Z
        < boss_extent(h, params.BOSS_OD_NOMINAL_MM)[1]
    ]
