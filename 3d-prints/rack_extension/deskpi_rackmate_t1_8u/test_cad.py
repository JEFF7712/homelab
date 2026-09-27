"""Kernel regressions: run explicitly in the Build123d environment."""

from __future__ import annotations

import unittest

from build123d import Align, Box, Cylinder, Location

from . import corner, model, params, seam


class CornerCadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mount = model.phase2_corner_mount()
        cls.lower = model.phase2_lower_upright().moved(
            Location((0, 0, params.BOTTOM_BLOCK_TOP_Z_MM))
        )
        cls.upper = model.phase2_upper_upright().moved(
            Location((0, 0, params.SPLICE_Z_MM))
        )
        cls.parts = (cls.mount, cls.lower, cls.upper)

    def assert_empty_intersection(self, a, b) -> None:
        intersection = a & b
        self.assertLess(abs(intersection.volume) if intersection else 0.0, 1e-5)

    def test_parts_are_single_valid_printable_solids(self) -> None:
        for part in self.parts:
            with self.subTest(bounds=part.bounding_box().size):
                self.assertTrue(part.is_valid)
                self.assertEqual(len(part.solids()), 1)
                size = part.bounding_box().size
                self.assertTrue(params.fits_bed(size.X, size.Y, size.Z))

    def test_mating_parts_do_not_collide(self) -> None:
        self.assert_empty_intersection(self.mount, self.lower)
        self.assert_empty_intersection(self.lower, self.upper)

    def test_measured_rack_spacing_on_front_and_rear(self) -> None:
        self.assertAlmostEqual(
            params.BODY_WIDTH_MM - 2 * params.RACK_HOLE_CENTER_X_MM, 236.0
        )
        for ox, oy in model.column_origins():
            high = oy > 0
            part = model.lower_column_module(ox, oy, high)
            x = (
                params.BODY_WIDTH_MM - params.RACK_HOLE_CENTER_X_MM
                if ox > 0
                else params.RACK_HOLE_CENTER_X_MM
            )
            probe = model._y_cylinder(
                params.INSERT_PILOT_DIA_MM / 2 - 0.01,
                params.INSERT_LEN_MM,
                x,
                oy + params.COLUMN_DEPTH_MM if high else oy,
                seam.standard_u_triples()[0][1],
                not high,
            )
            self.assert_empty_intersection(part, probe)
            self.assertAlmostEqual(part.bounding_box().min.X, ox)
            self.assertAlmostEqual(
                part.bounding_box().max.X, ox + params.COLUMN_INWARD_MM
            )

    def test_each_insert_is_contained_in_one_part(self) -> None:
        for z in seam.extension_hole_centers():
            with self.subTest(z=z):
                boundaries = (params.BOTTOM_BLOCK_TOP_Z_MM, params.SPLICE_Z_MM)
                self.assertTrue(
                    all(abs(z - b) > params.INSERT_OD_MM / 2 for b in boundaries)
                )
                probe = model._y_cylinder(
                    params.INSERT_PILOT_DIA_MM / 2 - 0.01,
                    params.INSERT_LEN_MM,
                    params.RACK_HOLE_CENTER_X_MM,
                    0,
                    z,
                    True,
                )
                for part in self.parts:
                    self.assert_empty_intersection(part, probe)

    def test_rod_passes_through_complete_column(self) -> None:
        rod = Cylinder(
            2.5,
            params.top_rod_end_z_mm() - params.bottom_rod_end_z_mm(),
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        ).locate(
            Location(
                (
                    params.BORE_CENTER_X_MM,
                    params.BORE_CENTER_Y_MM,
                    params.bottom_rod_end_z_mm(),
                )
            )
        )
        for part in self.parts:
            self.assert_empty_intersection(part, rod)

    def test_top_washer_can_reach_its_bearing_seat(self) -> None:
        seat = params.top_bearing_seat_z_mm()
        swept_washer = Cylinder(
            params.M5_WASHER_OD_MM / 2,
            params.ADDED_HEIGHT_MM - seat + 1,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        ).locate(Location((params.BORE_CENTER_X_MM, params.BORE_CENTER_Y_MM, seat)))
        self.assert_empty_intersection(self.upper, swept_washer)
        below_seat = swept_washer.moved(Location((0, 0, -0.2))) & self.upper
        self.assertGreater(below_seat.volume, 10)

    def test_bottom_retainer_fits_and_cannot_spin(self) -> None:
        retainer = model.phase2_bottom_nut_retainer()
        offset = Location((params.BORE_CENTER_X_MM, params.BORE_CENTER_Y_MM, 0))
        self.assert_empty_intersection(self.mount, retainer.moved(offset))
        turned = retainer.moved(Location((0, 0, 0), (0, 0, 20))).moved(offset)
        collision = turned & self.mount
        self.assertGreater(collision.volume, 1)

    def test_height_and_joint_shoulders(self) -> None:
        self.assertAlmostEqual(self.upper.bounding_box().max.Z, params.ADDED_HEIGHT_MM)
        for lower, upper, z in (
            (self.mount, self.lower, params.BOTTOM_BLOCK_TOP_Z_MM),
            (self.lower, self.upper, params.SPLICE_Z_MM),
        ):
            seated = lower & upper.moved(Location((0, 0, -0.05)))
            self.assertGreater(seated.volume, 5, f"missing bearing shoulder at {z}")

    def test_decoupled_rail_solids_are_valid_and_fit_bed(self) -> None:
        from . import rail

        lower_rail = rail.lower_equipment_rail()
        upper_rail = rail.upper_equipment_rail()
        coupon = rail.rail_coupon()

        for part in (lower_rail, upper_rail, coupon):
            with self.subTest(bounds=part.bounding_box().size):
                self.assertTrue(part.is_valid)
                self.assertEqual(len(part.solids()), 1)
                size = part.bounding_box().size
                self.assertTrue(params.fits_bed(size.X, size.Y, size.Z))

    def test_bracing_solids_are_valid_and_fit_bed(self) -> None:
        from . import bracing

        rear_low = bracing.rear_lower_crossbar()
        rear_up = bracing.rear_upper_crossbar()
        side_bar = bracing.side_restraint_bar()
        diag_low = bracing.rear_diagonal_brace_half(upper=False)
        diag_up = bracing.rear_diagonal_brace_half(upper=True)

        for part in (rear_low, rear_up, side_bar, diag_low, diag_up):
            with self.subTest(bounds=part.bounding_box().size):
                self.assertTrue(part.is_valid)
                self.assertEqual(len(part.solids()), 1)
                size = part.bounding_box().size
                self.assertTrue(params.fits_bed(size.X, size.Y, size.Z))

    def test_rev3_column_solids_are_valid_and_fit_bed(self) -> None:
        from . import column

        col_coupon = column.rev3_column_joint_coupon()
        col_low = column.rev3_lower_column()
        col_up = column.rev3_upper_column()

        for part in (col_coupon, col_low, col_up):
            with self.subTest(bounds=part.bounding_box().size):
                self.assertTrue(part.is_valid)
                self.assertEqual(len(part.solids()), 1)
                size = part.bounding_box().size
                self.assertTrue(params.fits_bed(size.X, size.Y, size.Z))

    def test_rev3_corner_mount_solids_are_valid_and_fit_bed(self) -> None:
        from . import column

        fl = column.rev3_corner_mount_front_left()
        fr = column.rev3_corner_mount_front_right()
        rl = column.rev3_corner_mount_rear_left()
        rr = column.rev3_corner_mount_rear_right()

        for part in (fl, fr, rl, rr):
            with self.subTest(bounds=part.bounding_box().size):
                self.assertTrue(part.is_valid)
                self.assertEqual(len(part.solids()), 1)
                size = part.bounding_box().size
                self.assertTrue(params.fits_bed(size.X, size.Y, size.Z))

    def test_rev3_mount_has_bearing_material_at_every_attachment(self) -> None:
        from . import column

        for rear, y_values in (
            (False, params.ATTACH_Y_MM[:2]),
            (True, params.ATTACH_Y_MM[2:]),
        ):
            mount = column.rev3_corner_mount(rear=rear)
            if rear:
                mount = mount.moved(
                    Location((0, params.BODY_DEPTH_MM - params.COLUMN_DEPTH_MM, 0))
                )
            for y in y_values:
                probe = Cylinder(
                    radius=params.BRACE_FASTENER_HEAD_DIA_MM / 2,
                    height=0.2,
                    align=(Align.CENTER, Align.CENTER, Align.MIN),
                ).locate(Location((params.STRUCTURAL_HOLE_X_LEFT_MM, y, 11.3)))
                with self.subTest(rear=rear, y=y):
                    self.assertGreater((mount & probe).volume, 1.0)

    def test_rev3_mount_lips_clear_stock_transverse_members(self) -> None:
        from . import column

        for rear, right in ((False, False), (False, True), (True, False), (True, True)):
            mount = column.rev3_corner_mount(rear=rear, right=right)
            x0 = params.BODY_WIDTH_MM - 32.3 if right else 2.3
            y0 = params.BODY_DEPTH_MM - params.TOP_MEMBER_WIDTH_MM if rear else 0.0
            if right:
                mount = mount.moved(
                    Location((params.BODY_WIDTH_MM - params.COLUMN_INWARD_MM, 0, 0))
                )
            if rear:
                mount = mount.moved(
                    Location((0, params.BODY_DEPTH_MM - params.COLUMN_DEPTH_MM, 0))
                )
            member = Box(
                params.TOP_MEMBER_WIDTH_MM,
                params.TOP_MEMBER_WIDTH_MM,
                3.0,
                align=(Align.MIN, Align.MIN, Align.MIN),
            ).locate(Location((x0, y0, -3.0)))
            with self.subTest(rear=rear, right=right):
                self.assert_empty_intersection(mount, member)

    def test_rev3_right_rails_are_positioned_on_the_right(self) -> None:
        from . import rail

        lower_left = rail.lower_equipment_rail_left()
        lower_right = rail.lower_equipment_rail_right()
        upper_left = rail.upper_equipment_rail_left()
        upper_right = rail.upper_equipment_rail_right()
        self.assertAlmostEqual(lower_left.bounding_box().min.X, params.RAIL_X_START_MM)
        self.assertAlmostEqual(
            lower_right.bounding_box().min.X,
            params.BODY_WIDTH_MM - params.RAIL_X_END_MM,
        )
        self.assertAlmostEqual(
            upper_right.bounding_box().max.X,
            params.BODY_WIDTH_MM - params.RAIL_X_START_MM,
        )
        self.assert_empty_intersection(lower_left, lower_right)
        self.assert_empty_intersection(upper_left, upper_right)

    def test_rev3_rear_screws_are_seated_with_tip_clearance(self) -> None:
        for x, z, diagonal in (
            (params.REAR_CROSSBAR_FASTENER_X_LEFT_MM, 55.0, False),
            (params.REAR_CROSSBAR_FASTENER_X_RIGHT_MM, 55.0, False),
            (params.DIAGONAL_ANCHOR_LOWER_X_MM, 55.0, True),
            (params.DIAGONAL_ANCHOR_UPPER_X_MM, 335.0, True),
            (params.REAR_CROSSBAR_FASTENER_X_LEFT_MM, 335.0, False),
            (params.REAR_CROSSBAR_FASTENER_X_RIGHT_MM, 335.0, False),
        ):
            if diagonal:
                seat_y = (
                    params.BODY_DEPTH_MM
                    + params.REAR_CROSSBAR_THICK_MM
                    + params.REAR_BRACE_THICK_MM
                )
                bore_face_y = params.BODY_DEPTH_MM + params.REAR_CROSSBAR_THICK_MM
            else:
                seat_y = (
                    params.BODY_DEPTH_MM
                    + params.REAR_CROSSBAR_THICK_MM
                    - params.M4_BRACE_SCREW_HEAD_HEIGHT_MM
                )
                bore_face_y = params.BODY_DEPTH_MM
            screw = model._m4_screw_y(seat_y, -1.0, x, z)
            seat = Box(
                params.BRACE_FASTENER_HEAD_DIA_MM,
                0.1,
                params.BRACE_FASTENER_HEAD_DIA_MM,
                align=(Align.CENTER, Align.MIN, Align.CENTER),
            ).locate(Location((x, seat_y, z)))
            tip_y = seat_y - params.M4_BRACE_SCREW_LENGTH_MM
            bore_bottom_y = bore_face_y - params.M4_HEAT_SET_BORE_DEPTH_MM
            with self.subTest(x=x, z=z, diagonal=diagonal):
                self.assertGreater((screw & seat).volume, 0.1)
                self.assertAlmostEqual(
                    tip_y - bore_bottom_y, params.M4_TIP_CLEARANCE_MM
                )

    def test_rev3_hardware_paths_clear_printed_parts(self) -> None:
        from . import bracing, column, rail

        parts = [
            column.rev3_corner_mount_front_left(),
            column.rev3_corner_mount_front_right().moved(
                Location((params.BODY_WIDTH_MM - params.COLUMN_INWARD_MM, 0, 0))
            ),
            column.rev3_corner_mount_rear_left().moved(
                Location((0, params.BODY_DEPTH_MM - params.COLUMN_DEPTH_MM, 0))
            ),
            column.rev3_corner_mount_rear_right().moved(
                Location(
                    (
                        params.BODY_WIDTH_MM - params.COLUMN_INWARD_MM,
                        params.BODY_DEPTH_MM - params.COLUMN_DEPTH_MM,
                        0,
                    )
                )
            ),
            column.rev3_lower_column_left().moved(
                Location((0, 0, params.BOTTOM_BLOCK_TOP_Z_MM))
            ),
            column.rev3_lower_column_right().moved(
                Location(
                    (
                        params.BODY_WIDTH_MM - params.COLUMN_INWARD_MM,
                        0,
                        params.BOTTOM_BLOCK_TOP_Z_MM,
                    )
                )
            ),
            column.rev3_lower_column_left().moved(
                Location(
                    (
                        0,
                        params.BODY_DEPTH_MM - params.COLUMN_DEPTH_MM,
                        params.BOTTOM_BLOCK_TOP_Z_MM,
                    )
                )
            ),
            column.rev3_lower_column_right().moved(
                Location(
                    (
                        params.BODY_WIDTH_MM - params.COLUMN_INWARD_MM,
                        params.BODY_DEPTH_MM - params.COLUMN_DEPTH_MM,
                        params.BOTTOM_BLOCK_TOP_Z_MM,
                    )
                )
            ),
            column.rev3_upper_column_left().moved(Location((0, 0, corner.SPLICE_Z))),
            column.rev3_upper_column_right().moved(
                Location(
                    (params.BODY_WIDTH_MM - params.COLUMN_INWARD_MM, 0, corner.SPLICE_Z)
                )
            ),
            column.rev3_upper_column_left().moved(
                Location(
                    (0, params.BODY_DEPTH_MM - params.COLUMN_DEPTH_MM, corner.SPLICE_Z)
                )
            ),
            column.rev3_upper_column_right().moved(
                Location(
                    (
                        params.BODY_WIDTH_MM - params.COLUMN_INWARD_MM,
                        params.BODY_DEPTH_MM - params.COLUMN_DEPTH_MM,
                        corner.SPLICE_Z,
                    )
                )
            ),
            rail.lower_equipment_rail_left(),
            rail.lower_equipment_rail_right(),
            rail.upper_equipment_rail_left(),
            rail.upper_equipment_rail_right(),
            bracing.rear_lower_crossbar(),
            bracing.rear_upper_crossbar(),
            bracing.side_restraint_bar(),
            bracing.side_restraint_bar(right=True),
            *bracing.rear_diagonal_assembly().solids(),
        ]
        for hardware in model.rev3_hardware():
            for printed in parts:
                with self.subTest(
                    hardware=hardware.bounding_box().center,
                    printed=printed.bounding_box().center,
                ):
                    self.assert_empty_intersection(hardware, printed)

    def test_rev3_top_washer_clearance_and_bearing_seat(self) -> None:
        from . import column

        col_up = column.rev3_upper_column()
        seat_z = params.top_bearing_seat_z_mm() - corner.SPLICE_Z
        top_z = params.ADDED_HEIGHT_MM - corner.SPLICE_Z

        swept_washer = Cylinder(
            radius=params.M5_WASHER_OD_MM / 2,
            height=top_z - seat_z + 1.0,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        ).locate(Location((params.BORE_CENTER_X_MM, params.BORE_CENTER_Y_MM, seat_z)))

        self.assert_empty_intersection(col_up, swept_washer)
        below_seat = swept_washer.moved(Location((0, 0, -0.2))) & col_up
        self.assertGreater(below_seat.volume, 10.0)

    def test_rev3_middle_splice_rail_rebate_and_clearance(self) -> None:
        from . import column, rail

        col_low = column.rev3_lower_column().moved(
            Location((0, 0, params.BOTTOM_BLOCK_TOP_Z_MM))
        )
        col_up = column.rev3_upper_column().moved(Location((0, 0, corner.SPLICE_Z)))
        rail_low = rail.lower_equipment_rail()
        rail_up = rail.upper_equipment_rail()

        self.assert_empty_intersection(col_low, col_up)
        self.assert_empty_intersection(col_low, rail_low)
        self.assert_empty_intersection(col_low, rail_up)
        self.assert_empty_intersection(col_up, rail_up)

    def test_rev3_corner_mount_mating_and_rebate(self) -> None:
        from . import column, rail

        mount_fl = column.rev3_corner_mount_front_left()
        col_low = column.rev3_lower_column().moved(
            Location((0, 0, params.BOTTOM_BLOCK_TOP_Z_MM))
        )
        rail_low = rail.lower_equipment_rail()

        self.assert_empty_intersection(mount_fl, col_low)
        self.assert_empty_intersection(mount_fl, rail_low)

    def test_rev3_full_assembly_collision(self) -> None:
        from . import bracing, column, rail

        m_fl = column.rev3_corner_mount_front_left()
        m_fr = column.rev3_corner_mount_front_right().moved(
            Location((params.BODY_WIDTH_MM - params.COLUMN_INWARD_MM, 0, 0))
        )
        m_rl = column.rev3_corner_mount_rear_left().moved(
            Location((0, params.BODY_DEPTH_MM - params.COLUMN_DEPTH_MM, 0))
        )
        m_rr = column.rev3_corner_mount_rear_right().moved(
            Location(
                (
                    params.BODY_WIDTH_MM - params.COLUMN_INWARD_MM,
                    params.BODY_DEPTH_MM - params.COLUMN_DEPTH_MM,
                    0,
                )
            )
        )

        c_low_fl = column.rev3_lower_column_left().moved(
            Location((0, 0, params.BOTTOM_BLOCK_TOP_Z_MM))
        )
        c_low_fr = column.rev3_lower_column_right().moved(
            Location(
                (
                    params.BODY_WIDTH_MM - params.COLUMN_INWARD_MM,
                    0,
                    params.BOTTOM_BLOCK_TOP_Z_MM,
                )
            )
        )
        c_low_rl = column.rev3_lower_column_left().moved(
            Location(
                (
                    0,
                    params.BODY_DEPTH_MM - params.COLUMN_DEPTH_MM,
                    params.BOTTOM_BLOCK_TOP_Z_MM,
                )
            )
        )
        c_low_rr = column.rev3_lower_column_right().moved(
            Location(
                (
                    params.BODY_WIDTH_MM - params.COLUMN_INWARD_MM,
                    params.BODY_DEPTH_MM - params.COLUMN_DEPTH_MM,
                    params.BOTTOM_BLOCK_TOP_Z_MM,
                )
            )
        )

        c_up_fl = column.rev3_upper_column_left().moved(
            Location((0, 0, corner.SPLICE_Z))
        )
        c_up_fr = column.rev3_upper_column_right().moved(
            Location(
                (
                    params.BODY_WIDTH_MM - params.COLUMN_INWARD_MM,
                    0,
                    corner.SPLICE_Z,
                )
            )
        )
        c_up_rl = column.rev3_upper_column_left().moved(
            Location(
                (
                    0,
                    params.BODY_DEPTH_MM - params.COLUMN_DEPTH_MM,
                    corner.SPLICE_Z,
                )
            )
        )
        c_up_rr = column.rev3_upper_column_right().moved(
            Location(
                (
                    params.BODY_WIDTH_MM - params.COLUMN_INWARD_MM,
                    params.BODY_DEPTH_MM - params.COLUMN_DEPTH_MM,
                    corner.SPLICE_Z,
                )
            )
        )

        r_low_l = rail.lower_equipment_rail_left()
        r_low_r = rail.lower_equipment_rail_right()
        r_up_l = rail.upper_equipment_rail_left()
        r_up_r = rail.upper_equipment_rail_right()

        cb_low = bracing.rear_lower_crossbar()
        cb_up = bracing.rear_upper_crossbar()
        sr_l = bracing.side_restraint_bar(right=False)
        sr_r = bracing.side_restraint_bar(right=True)
        diag = bracing.rear_diagonal_assembly()

        rod_fl = Cylinder(
            2.5,
            params.ADDED_HEIGHT_MM + 20,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        ).locate(Location((params.BORE_CENTER_X_MM, params.BORE_CENTER_Y_MM, -10)))
        rod_fr = Cylinder(
            2.5,
            params.ADDED_HEIGHT_MM + 20,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        ).locate(
            Location(
                (
                    params.BODY_WIDTH_MM - params.BORE_CENTER_X_MM,
                    params.BORE_CENTER_Y_MM,
                    -10,
                )
            )
        )
        rod_rl = Cylinder(
            2.5,
            params.ADDED_HEIGHT_MM + 20,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        ).locate(
            Location(
                (
                    params.BORE_CENTER_X_MM,
                    params.BODY_DEPTH_MM - params.BORE_CENTER_Y_MM,
                    -10,
                )
            )
        )
        rod_rr = Cylinder(
            2.5,
            params.ADDED_HEIGHT_MM + 20,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        ).locate(
            Location(
                (
                    params.BODY_WIDTH_MM - params.BORE_CENTER_X_MM,
                    params.BODY_DEPTH_MM - params.BORE_CENTER_Y_MM,
                    -10,
                )
            )
        )

        pairs = [
            (m_fl, c_low_fl),
            (m_fr, c_low_fr),
            (m_rl, c_low_rl),
            (m_rr, c_low_rr),
            (c_low_fl, c_up_fl),
            (c_low_fr, c_up_fr),
            (c_low_rl, c_up_rl),
            (c_low_rr, c_up_rr),
            (m_fl, r_low_l),
            (m_fr, r_low_r),
            (c_low_fl, r_low_l),
            (c_low_fr, r_low_r),
            (c_low_fl, r_up_l),
            (c_low_fr, r_up_r),
            (c_up_fl, r_up_l),
            (c_up_fr, r_up_r),
            (c_low_rl, cb_low),
            (c_low_rr, cb_low),
            (c_up_rl, cb_up),
            (c_up_rr, cb_up),
            (c_low_fl, sr_l),
            (c_low_rl, sr_l),
            (c_low_fr, sr_r),
            (c_low_rr, sr_r),
            (rod_fl, m_fl),
            (rod_fl, c_low_fl),
            (rod_fl, c_up_fl),
            (rod_fr, m_fr),
            (rod_fr, c_low_fr),
            (rod_fr, c_up_fr),
            (rod_rl, m_rl),
            (rod_rl, c_low_rl),
            (rod_rl, c_up_rl),
            (rod_rr, m_rr),
            (rod_rr, c_low_rr),
            (rod_rr, c_up_rr),
        ]

        for p1, p2 in pairs:
            self.assert_empty_intersection(p1, p2)

        for diag_solid in diag.solids():
            self.assert_empty_intersection(diag_solid, cb_low)
            self.assert_empty_intersection(diag_solid, cb_up)


if __name__ == "__main__":
    unittest.main()
