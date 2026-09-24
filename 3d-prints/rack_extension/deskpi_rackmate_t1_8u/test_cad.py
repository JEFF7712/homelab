"""Kernel regressions: run explicitly in the Build123d environment."""

from __future__ import annotations

import unittest

from build123d import Align, Cylinder, Location

from . import model, params, seam


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


if __name__ == "__main__":
    unittest.main()
