"""Stock-to-extension seam proof (G1 computation, PRD section 6).

Datum: z=0 at the original aluminum lid-support plane, +z upward.
Measured stock phase gives hole 1 at z=-28.8 with downward gaps
15.9/15.9/12.7. Upward continuation uses the reversed cycle
12.7/15.9/15.9 (see params.UP_GAP_CYCLE_MM).

Headline verdict: the lid plane falls mid-cycle, so two theoretical
positions (-16.1, -0.2) are unmanufacturable and the first extension
hole (+15.7) is a third-hole, not a U-start. Result: 7 full standard U
plus a bottom single-hole partial and a top single-hole partial.
FR-01 ("8U usable") must be revised; see fr01_status().
"""

from __future__ import annotations

from . import params

SEAM_TOL_MM = 0.5
STOCK_TOP_HOLE_Z = -params.LID_TO_TOP_HOLE_MM


def continued_centers(
    z_min: float = -80.0, z_max: float = params.ADDED_HEIGHT_MM + params.U_PITCH_MM
) -> list[float]:
    """Full continued pattern across the seam region."""
    out = [STOCK_TOP_HOLE_Z]
    i = 0
    while out[-1] > z_min - params.U_PITCH_MM:
        out.append(out[-1] - params.UP_GAP_CYCLE_MM[(2 - i) % 3])
        i += 1
    out = sorted(out)
    i = 0
    while out[-1] < z_max:
        out.append(out[-1] + params.UP_GAP_CYCLE_MM[i % 3])
        i += 1
    return [c for c in out if z_min <= c <= z_max]


def classify(z: float) -> str:
    """Classify one theoretical hole center per the PRD seam proof."""
    if abs(z - STOCK_TOP_HOLE_Z) < SEAM_TOL_MM:
        return "STOCK_PRESENT"
    if z < -SEAM_TOL_MM:
        return "ABSENT_BELOW_PLANE"
    if z <= SEAM_TOL_MM:
        return "SEAM_OMITTED"
    if z >= params.ADDED_HEIGHT_MM - SEAM_TOL_MM:
        return "ABOVE_TOP"
    return "EXTENSION_PROVIDED"


def extension_hole_centers() -> list[float]:
    """Centers the printed extension must provide (0, 355.6 exclusive)."""
    return [
        c
        for c in continued_centers()
        if SEAM_TOL_MM < c < params.ADDED_HEIGHT_MM - SEAM_TOL_MM
    ]


def standard_u_triples() -> list[tuple[float, float, float]]:
    """Consecutive triples matching the 15.9/15.9/12.7 standard U."""
    holes = extension_hole_centers()
    triples: list[tuple[float, float, float]] = []
    for a, b, c in zip(holes, holes[1:], holes[2:]):
        if (
            abs((b - a) - params.HOLE_1_TO_2_MM) < 0.05
            and abs((c - b) - params.HOLE_2_TO_3_MM) < 0.05
        ):
            triples.append((a, b, c))
    return triples


def usable_full_u_count() -> int:
    """Full standard U positions wholly inside the extension."""
    return len(standard_u_triples())


def two_hole_skip_pairs() -> list[tuple[float, float]]:
    """31.8 mm pairs usable by 2-hole ears skipping the middle hole."""
    holes = extension_hole_centers()
    return [
        (a, b)
        for a, b in zip(holes, holes[2:])
        if abs((b - a) - 2 * params.HOLE_1_TO_2_MM) < 0.1
    ]


def fr01_status() -> str:
    """Locked FR-01 revision (2026-09-16): 8U height, 7U standard capacity."""
    return (
        "LOCKED: 355.6 mm added lid-plane height with 7U standard capacity "
        f"({usable_full_u_count()} triples); partials at "
        f"{first_extension_hole():.1f} and {last_extension_hole():.1f} mm "
        "are not usable U; no equipment spans the -28.8..+28.4 mm dead band"
    )


def rev1_locks() -> list[str]:
    """Revision 1 locked product/architecture decisions (2026-09-16)."""
    return [
        (
            "Product: 8U-height extension with 7U standard rack-mount "
            "capacity plus partial positions at the seam/top"
        ),
        (
            "Standard triples run {28.4, 44.3, 60.2} through "
            "{295.4, 311.3, 327.2}; +15.7 and +339.9 are excluded "
            "from the usable count"
        ),
        "No seam adapter or custom bottom blank in Revision 1",
        (
            "Bottom corner: first-hole boss merged with frame into one "
            "reinforced end block"
        ),
        (
            "Splice: joint at 169.85 mm between complete insert bosses; "
            "spigot relieved behind the rack bosses"
        ),
        (
            "Top corner: one integrated block packing washer seat, 7.8 mm "
            "nyloc pocket, M5 insert boss, and M5 bore, proved in section"
        ),
    ]


def first_extension_hole() -> float:
    """Lowest provided center (a third-hole, not a U-start)."""
    return min(extension_hole_centers())


def last_extension_hole() -> float:
    """Highest provided center (a third-hole of a partial top U)."""
    return max(extension_hole_centers())


def seam_table() -> list[tuple[float, str]]:
    """Classified centers across the seam for the dimensioned drawing."""
    return [(c, classify(c)) for c in continued_centers(-80.0, 60.0)]
