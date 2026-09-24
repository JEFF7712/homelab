"""Offline tests for the seam proof and corner section (no CAD kernel)."""

from __future__ import annotations

import unittest

from . import corner, params, seam


class SeamProofTests(unittest.TestCase):
    def test_continuation_matches_measured_phase(self) -> None:
        centers = [round(c, 1) for c in seam.continued_centers(-80.0, 60.0)]
        self.assertEqual(
            centers,
            [-73.3, -60.6, -44.7, -28.8, -16.1, -0.2, 15.7, 28.4, 44.3],
        )

    def test_seam_classification(self) -> None:
        table = {round(c, 1): s for c, s in seam.seam_table()}
        self.assertEqual(table[round(-params.LID_TO_TOP_HOLE_MM, 1)], "STOCK_PRESENT")
        self.assertEqual(table[-16.1], "ABSENT_BELOW_PLANE")
        self.assertEqual(table[-0.2], "SEAM_OMITTED")
        self.assertEqual(table[15.7], "EXTENSION_PROVIDED")

    def test_first_and_last_extension_holes(self) -> None:
        self.assertAlmostEqual(seam.first_extension_hole(), 15.7, places=1)
        self.assertAlmostEqual(seam.last_extension_hole(), 339.9, places=1)

    def test_seven_full_standard_u(self) -> None:
        triples = seam.standard_u_triples()
        self.assertEqual(len(triples), 7)
        self.assertAlmostEqual(triples[0][0], 28.4, places=1)
        self.assertAlmostEqual(triples[-1][2], 327.2, places=1)
        self.assertEqual(seam.usable_full_u_count(), 7)

    def test_two_hole_ears_fit_each_full_u(self) -> None:
        pairs = seam.two_hole_skip_pairs()
        self.assertGreaterEqual(len(pairs), 7)
        for a, b in pairs:
            self.assertAlmostEqual(b - a, 31.8, places=1)

    def test_no_device_spans_the_seam(self) -> None:
        self.assertGreater(seam.first_extension_hole() - (-28.8), 40.0)

    def test_fr01_locked_to_seven_u(self) -> None:
        self.assertTrue(seam.fr01_status().startswith("LOCKED"))
        self.assertIn("7U standard capacity", seam.fr01_status())

    def test_rev1_locks_match_computed_geometry(self) -> None:
        locks = "\n".join(seam.rev1_locks())
        self.assertEqual(len(seam.rev1_locks()), 6)
        first, _, last = (
            seam.standard_u_triples()[0][0],
            None,
            seam.standard_u_triples()[-1][2],
        )
        self.assertAlmostEqual(first, 28.4, places=1)
        self.assertAlmostEqual(last, 327.2, places=1)
        self.assertIn("169.85", locks)
        self.assertIn("7.8 mm", locks)
        self.assertNotIn("8U usable", locks)


class CornerSectionTests(unittest.TestCase):
    def test_splice_height_matches_module_stack(self) -> None:
        self.assertAlmostEqual(corner.SPLICE_Z, params.SPLICE_Z_MM)

    def test_first_boss_overlaps_frame(self) -> None:
        lo, _ = corner.boss_extent(seam.first_extension_hole(), params.BOSS_OD_MIN_MM)
        self.assertLess(lo, corner.BOTTOM_FRAME_TOP_Z)

    def test_joints_do_not_split_insert_bosses(self) -> None:
        self.assertEqual(corner.splice_zone_holes(), [])
        for boundary in (params.BOTTOM_BLOCK_TOP_Z_MM, corner.SPLICE_Z):
            for hole in seam.extension_hole_centers():
                self.assertGreater(abs(hole - boundary), params.BOSS_OD_NOMINAL_MM / 2)

    def test_top_boss_overlaps_frame_and_pocket(self) -> None:
        _, hi = corner.boss_extent(seam.last_extension_hole(), params.BOSS_OD_MAX_MM)
        self.assertGreater(hi, corner.TOP_FRAME_BOTTOM_Z)
        findings = "\n".join(corner.corner_findings())
        self.assertIn("tie-rod", findings)

    def test_blocking_findings_are_explicit(self) -> None:
        blocking = [f for f in corner.corner_findings() if f.startswith("BLOCKING")]
        self.assertGreaterEqual(len(blocking), 2)


class Rev1ProofTests(unittest.TestCase):
    def test_rev1_proof_has_no_violations(self) -> None:
        self.assertEqual(corner.rev1_proof(), [])

    def test_bottom_pocket_stack_and_rod_end(self) -> None:
        self.assertAlmostEqual(params.bottom_pocket_depth_mm(), 11.0)
        self.assertLess(params.bottom_pocket_depth_mm(), params.BOTTOM_BLOCK_TOP_Z_MM)
        self.assertAlmostEqual(params.bottom_rod_end_z_mm(), 0.2)
        self.assertGreaterEqual(params.bottom_rod_end_z_mm(), 0.0)

    def test_top_pocket_stack_and_rod_end(self) -> None:
        self.assertAlmostEqual(params.top_bearing_seat_z_mm(), 344.8)
        self.assertAlmostEqual(params.top_rod_end_z_mm(), 355.4)
        self.assertLessEqual(params.top_rod_end_z_mm(), params.ADDED_HEIGHT_MM)

    def test_screw_tip_clears_bore(self) -> None:
        self.assertAlmostEqual(params.bore_wall_front_y_mm(), 11.75)
        self.assertLess(params.MAX_SCREW_PENETRATION_MM, params.bore_wall_front_y_mm())
        self.assertGreater(corner.bore_to_boss_clearance_mm(params.BOSS_OD_MAX_MM), 0)

    def test_max_bosses_fit_inside_blocks(self) -> None:
        lo, hi = corner.boss_extent(seam.first_extension_hole(), params.BOSS_OD_MAX_MM)
        self.assertGreaterEqual(lo, 0.0)
        self.assertLessEqual(hi, params.BOTTOM_BLOCK_TOP_Z_MM)
        lo, hi = corner.boss_extent(seam.last_extension_hole(), params.BOSS_OD_MAX_MM)
        self.assertGreaterEqual(lo, params.TOP_BLOCK_BOTTOM_Z_MM)
        self.assertLessEqual(hi, params.ADDED_HEIGHT_MM)

    def test_validate_passes(self) -> None:
        self.assertEqual(params.validate(), [])


if __name__ == "__main__":
    unittest.main()
