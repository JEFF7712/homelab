"""Offline unit tests for the RackMate T1 8U extension params (no CAD kernel)."""

from __future__ import annotations

import unittest

from . import params
from .params import (
    ADDED_HEIGHT_MM,
    ATTACH_Y_MM,
    BODY_DEPTH_MM,
    BODY_WIDTH_MM,
    BOSS_DEPTH_TARGET_MM,
    COLUMN_INWARD_MM,
    EXTERIOR_CLEARANCE_MM,
    FRAME_ZONE_MM,
    FULL_THREAD_PROTRUSION_MM,
    HANDLE_HOLE_X_LEFT_MM,
    HANDLE_HOLE_X_RIGHT_MM,
    HANDLE_HOLE_Y_FRONT_MM,
    HANDLE_HOLE_Y_REAR_MM,
    HANDLE_SCREW_SPACING_MEASURED_MM,
    HOLE_1_TO_2_MM,
    HOLE_2_TO_3_MM,
    HOLE_3_TO_4_MM,
    INSERT_LEN_MM,
    LID_TO_TOP_HOLE_MM,
    M5_NYLOC_HEIGHT_MM,
    M5_WASHER_THICK_MM,
    MAX_INWARD_PROJECTION_PER_SIDE_MM,
    PATH_A_BOTTOM_FRAME_MM,
    PATH_A_TOP_FRAME_MM,
    STOCK_CLEAR_OPENING_MM,
    STRUCTURAL_HOLE_X_LEFT_MM,
    STRUCTURAL_HOLE_X_RIGHT_MM,
    TIP_ALLOWANCE_MM,
    U_PITCH_MM,
)


class ExtensionParamsTests(unittest.TestCase):
    def test_eight_u_datum_is_exact(self) -> None:
        self.assertAlmostEqual(ADDED_HEIGHT_MM, 355.6)

    def test_path_a_stack_stays_inside_envelope(self) -> None:
        self.assertAlmostEqual(params.path_a_stack_mm(), ADDED_HEIGHT_MM)
        self.assertAlmostEqual(PATH_A_BOTTOM_FRAME_MM, FRAME_ZONE_MM)
        self.assertAlmostEqual(PATH_A_TOP_FRAME_MM, FRAME_ZONE_MM)

    def test_footprint_matches_stock(self) -> None:
        self.assertAlmostEqual(BODY_WIDTH_MM, 281.0)
        self.assertAlmostEqual(BODY_DEPTH_MM, 200.0)

    def test_opening_is_not_narrowed(self) -> None:
        self.assertAlmostEqual(STOCK_CLEAR_OPENING_MM, 222.5)
        self.assertAlmostEqual(MAX_INWARD_PROJECTION_PER_SIDE_MM, 29.25)
        self.assertLessEqual(COLUMN_INWARD_MM, MAX_INWARD_PROJECTION_PER_SIDE_MM)
        opening = BODY_WIDTH_MM - 2 * COLUMN_INWARD_MM
        self.assertGreaterEqual(opening, STOCK_CLEAR_OPENING_MM)

    def test_rack_phase_matches_measured_pattern(self) -> None:
        self.assertAlmostEqual(LID_TO_TOP_HOLE_MM, 28.8)
        self.assertAlmostEqual(HOLE_1_TO_2_MM, 15.9)
        self.assertAlmostEqual(HOLE_2_TO_3_MM, 15.9)
        self.assertAlmostEqual(HOLE_3_TO_4_MM, 12.7)
        self.assertAlmostEqual(U_PITCH_MM, 44.45)

    def test_seam_continuation_needs_drawing(self) -> None:
        centers = params.theoretical_hole_centers_below_or_near_seam()
        near = [round(c, 1) for c in centers if c < 15.65 + 1e-6]
        self.assertEqual(near, [-28.8, -16.1, -0.2])

    def test_lower_attachment_uses_corner_pairs(self) -> None:
        self.assertEqual(tuple(ATTACH_Y_MM), (25.0, 38.0, 157.0, 170.0))
        self.assertAlmostEqual(STRUCTURAL_HOLE_X_LEFT_MM, 22.8)
        self.assertAlmostEqual(STRUCTURAL_HOLE_X_RIGHT_MM, BODY_WIDTH_MM - 22.8)
        self.assertAlmostEqual(HANDLE_HOLE_X_LEFT_MM, 13.0)
        self.assertAlmostEqual(HANDLE_HOLE_X_RIGHT_MM, BODY_WIDTH_MM - 13.0)

    def test_handle_spacing_matches_measured_m01(self) -> None:
        self.assertAlmostEqual(
            HANDLE_HOLE_Y_REAR_MM - HANDLE_HOLE_Y_FRONT_MM,
            HANDLE_SCREW_SPACING_MEASURED_MM,
        )
        self.assertAlmostEqual(HANDLE_SCREW_SPACING_MEASURED_MM, 131.0)

    def test_thread_engagement_is_derated(self) -> None:
        self.assertAlmostEqual(params.USABLE_THREAD_DEPTH_MM or 0.0, 5.8)
        self.assertGreaterEqual(params.THREAD_ENGAGEMENT_MIN_MM, 4.5)
        self.assertLessEqual(params.THREAD_ENGAGEMENT_MAX_MM, 5.0)
        self.assertLess(
            params.THREAD_ENGAGEMENT_MAX_MM, params.USABLE_THREAD_DEPTH_MM or 0.0
        )

    def test_all_measurements_closed(self) -> None:
        self.assertEqual(params.open_measurements(), [])
        self.assertAlmostEqual(params.TOP_MEMBER_THICK_APPROX_MM, 5.9)
        self.assertIn("free-standing", params.ANTI_TIP_STRATEGY)

    def test_m12_evidence_is_filed(self) -> None:
        from pathlib import Path

        base = Path(__file__).resolve().parents[1]
        notes = base / "m12_top_frame_notes.md"
        photo = base / "m12_top_frame.jpg"
        self.assertTrue(notes.is_file(), f"missing {notes}")
        self.assertIn("281", notes.read_text())
        self.assertTrue(photo.is_file(), f"missing {photo}")
        with open(photo, "rb") as fh:
            self.assertEqual(fh.read(3), b"\xff\xd8\xff")

    def test_boss_contains_insert(self) -> None:
        self.assertGreater(BOSS_DEPTH_TARGET_MM, INSERT_LEN_MM)
        self.assertEqual(params.INSERT_THREAD, "M5x0.8")
        self.assertAlmostEqual(params.INSERT_OD_MM, 6.7)
        self.assertAlmostEqual(BOSS_DEPTH_TARGET_MM, 9.3)
        self.assertAlmostEqual(params.INSERT_PILOT_DIA_MM, 6.2)

    def test_rod_math_separates_protrusion_from_clearance(self) -> None:
        pocket = params.pocket_depth_required_mm()
        self.assertAlmostEqual(
            pocket,
            M5_WASHER_THICK_MM
            + M5_NYLOC_HEIGHT_MM
            + FULL_THREAD_PROTRUSION_MM
            + TIP_ALLOWANCE_MM
            + EXTERIOR_CLEARANCE_MM,
        )
        seat = params.seat_spacing_illustrative_mm()
        self.assertAlmostEqual(seat, ADDED_HEIGHT_MM - 2 * pocket)
        rod = params.rod_length_illustrative_mm()
        self.assertAlmostEqual(
            rod,
            seat
            + 2
            * (
                M5_WASHER_THICK_MM
                + M5_NYLOC_HEIGHT_MM
                + FULL_THREAD_PROTRUSION_MM
                + TIP_ALLOWANCE_MM
            ),
        )
        self.assertLess(rod, 400.0)
        self.assertLess(params.residual_web_illustrative_mm(), 2.0)

    def test_column_module_fits_a1_bed(self) -> None:
        env = params.column_module_envelope()
        self.assertTrue(params.fits_bed(env.x_mm, env.y_mm, env.z_mm))

    def test_validate_passes(self) -> None:
        self.assertEqual(params.validate(), [])

    def test_joints_have_volumetric_overlap(self) -> None:
        self.assertGreaterEqual(params.JOINT_OVERLAP_MM, 2.0)
        lip_top = -params.LIP_DEPTH_MM + params.LIP_DEPTH_MM + params.JOINT_OVERLAP_MM
        self.assertGreater(lip_top, 0.0)
        self.assertLess(lip_top, params.FRAME_ZONE_MM)

    def test_phase1_coupon_covers_attachment_pair(self) -> None:
        self.assertLess(params.COUPON_Y_START_MM, 25.0)
        self.assertGreater(params.COUPON_Y_END_MM, 38.0)
        self.assertAlmostEqual(params.COUPON_HOLE_DIA_MM, 4.5)
        self.assertAlmostEqual(params.coupon_channel_mm(), 30.3)
        self.assertLess(
            params.LATERAL_COUPON_LENGTH_MM,
            params.COUPON_Y_END_MM - params.COUPON_Y_START_MM,
        )
        self.assertLess(
            params.COUPON_Y_END_MM - params.COUPON_Y_START_MM, params.BED_Y_MM
        )

    def test_m4_frame_hardware_schedule_has_tip_clearance(self) -> None:
        self.assertEqual(params.M4_BRACE_SCREW_THREAD, "M4x0.7")
        self.assertAlmostEqual(params.M4_FRAME_CLEARANCE_DIA_MM, 4.5)
        self.assertAlmostEqual(params.M4_HEAT_SET_PILOT_DIA_MM, 5.8)
        self.assertAlmostEqual(params.M4_HEAT_SET_DEPTH_MM, 6.0)
        self.assertAlmostEqual(params.M4_HEAT_SET_OD_MM, 6.0)
        self.assertLessEqual(
            params.M4_BRACE_SCREW_LENGTH_MM,
            params.SIDE_RESTRAINT_THICK_MM
            + params.M4_HEAT_SET_BORE_DEPTH_MM
            - params.M4_TIP_CLEARANCE_MM,
        )
        self.assertLessEqual(
            params.M4_BRACE_SCREW_LENGTH_MM,
            params.REAR_CROSSBAR_THICK_MM
            - params.M4_BRACE_SCREW_HEAD_HEIGHT_MM
            + params.M4_HEAT_SET_BORE_DEPTH_MM
            - params.M4_TIP_CLEARANCE_MM,
        )
        self.assertEqual(
            set(params.M4_FRAME_HARDWARE_SCHEDULE),
            {
                "column_to_side_restraint",
                "column_to_rear_crossbar",
                "crossbar_to_diagonal",
                "column_to_upper_side_beam",
                "lid_to_side_beam",
            },
        )

    def test_phase2_mount_keeps_attachment_access(self) -> None:
        self.assertGreater(params.ATTACH_ACCESS_DIA_MM, params.COUPON_HOLE_DIA_MM)
        self.assertGreater(params.LOWER_MOUNT_SOCKET_CLEARANCE_MM, 0.0)
        self.assertLess(
            params.BOTTOM_BLOCK_TOP_Z_MM + params.LOWER_MOUNT_SPIGOT_HEIGHT_MM,
            params.BED_Z_MM,
        )
        self.assertGreaterEqual(
            params.CORNER_LIP_START_MM,
            params.TOP_MEMBER_WIDTH_MM + params.LIP_CLEARANCE_MM,
        )
        self.assertGreater(params.BOTTOM_RETAINER_CLEARANCE_MM, 0.0)
        self.assertAlmostEqual(params.M4_MOUNT_SCREW_LEN_MM, 16.0)
        self.assertAlmostEqual(params.mount_thread_engagement_mm(), 4.5)

    def test_m5_insert_coupon_uses_measured_hardware(self) -> None:
        self.assertAlmostEqual(params.M5_INSERT_LEN_MEASURED_MM, 7.9)
        self.assertAlmostEqual(params.M5_INSERT_OD_MEASURED_MM, 6.7)
        self.assertAlmostEqual(params.M5_INSERT_LEAD_DIA_MEASURED_MM, 5.8)
        self.assertEqual(params.M5_INSERT_PILOT_CANDIDATES_MM, (5.8, 6.0, 6.2))
        self.assertAlmostEqual(params.M5_INSERT_PILOT_ACCEPTED_MM, 6.2)
        self.assertAlmostEqual(
            params.INSERT_PILOT_DIA_MM, params.M5_INSERT_PILOT_ACCEPTED_MM
        )
        self.assertGreater(
            params.M5_INSERT_COUPON_DEPTH_MM, params.M5_INSERT_LEN_MEASURED_MM
        )

    def test_every_extension_hole_has_one_owner(self) -> None:
        from . import corner, seam

        holes = seam.extension_hole_centers()
        bottom = [z for z in holes if z <= params.BOTTOM_BLOCK_TOP_Z_MM]
        lower = [z for z in holes if params.BOTTOM_BLOCK_TOP_Z_MM < z < corner.SPLICE_Z]
        splice = [z for z in holes if z == corner.SPLICE_Z]
        upper = [z for z in holes if corner.SPLICE_Z < z < params.TOP_BLOCK_BOTTOM_Z_MM]
        top = [z for z in holes if z >= params.TOP_BLOCK_BOTTOM_Z_MM]
        self.assertEqual(bottom + lower + splice + upper + top, holes)
        self.assertEqual(
            (len(bottom), len(lower), len(splice), len(upper), len(top)),
            (2, 9, 0, 11, 1),
        )

    def test_decoupled_rail_fits_bed_and_envelope(self) -> None:
        lower_env = params.rail_lower_envelope()
        upper_env = params.rail_upper_envelope()
        self.assertTrue(params.fits_bed(lower_env.x_mm, lower_env.y_mm, lower_env.z_mm))
        self.assertTrue(params.fits_bed(upper_env.x_mm, upper_env.y_mm, upper_env.z_mm))
        self.assertAlmostEqual(
            lower_env.z_mm + upper_env.z_mm,
            params.ADDED_HEIGHT_MM
            - params.PATH_A_BOTTOM_FRAME_MM
            - params.PATH_A_TOP_FRAME_MM,
        )

    def test_all_extension_holes_covered_by_decoupled_rails(self) -> None:
        from . import seam

        holes = seam.extension_hole_centers()
        lower_holes = [
            z
            for z in holes
            if params.RAIL_LOWER_Z_START_MM <= z <= params.RAIL_LOWER_Z_END_MM
        ]
        upper_holes = [
            z
            for z in holes
            if params.RAIL_UPPER_Z_START_MM <= z <= params.RAIL_UPPER_Z_END_MM
        ]
        self.assertEqual(len(lower_holes) + len(upper_holes), len(holes))
        self.assertEqual(len(lower_holes), 11)
        self.assertEqual(len(upper_holes), 12)

    def test_rail_fasteners_clear_rack_inserts(self) -> None:
        from . import seam

        holes = seam.extension_hole_centers()
        for fz in params.RAIL_FASTENER_Z_LOWER_MM + params.RAIL_FASTENER_Z_UPPER_MM:
            # Check Z distance to every rack hole center
            min_dist = min(abs(fz - hz) for hz in holes)
            self.assertGreater(
                min_dist,
                (params.RAIL_FASTENER_HEAD_DIA_MM + params.M5_INSERT_OD_MEASURED_MM) / 2
                + 1.0,
                f"Fastener at z={fz} too close to rack hole (dist={min_dist})",
            )

    def test_rear_crossbar_and_side_restraint_fit_bed(self) -> None:
        cb_env = params.rear_crossbar_envelope()
        sr_env = params.side_restraint_envelope()
        self.assertTrue(params.fits_bed(cb_env.x_mm, cb_env.y_mm, cb_env.z_mm))
        self.assertTrue(params.fits_bed(sr_env.x_mm, sr_env.y_mm, sr_env.z_mm))
        self.assertAlmostEqual(params.REAR_CROSSBAR_SPAN_MM, 223.0)
        self.assertAlmostEqual(params.SIDE_RESTRAINT_SPAN_MM, 140.0)


if __name__ == "__main__":
    unittest.main()
