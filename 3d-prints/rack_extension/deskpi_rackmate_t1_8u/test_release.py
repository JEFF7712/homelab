"""Assembly checks for the upper-frame prototype, not load certification."""

from __future__ import annotations

import itertools
import unittest

from build123d import Align, Box, Cylinder, Location

from . import params, release, top


def overlap_volume(first, second) -> float:
    a, b = first.bounding_box(), second.bounding_box()
    if any(
        min(getattr(a.max, axis), getattr(b.max, axis))
        - max(getattr(a.min, axis), getattr(b.min, axis))
        < 1e-6
        for axis in ("X", "Y", "Z")
    ):
        return 0.0
    result = first.intersect(second)
    return sum(s.volume for s in result.solids()) if result else 0.0


class UpperFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.parts = release.printed_assembly()
        cls.hardware = release.hardware()

    def test_all_printed_parts_valid_and_pairwise_clear(self) -> None:
        for name, part in self.parts.items():
            with self.subTest(part=name):
                self.assertTrue(part.is_valid)
                self.assertEqual(len(part.solids()), 1)
        for (a, first), (b, second) in itertools.combinations(self.parts.items(), 2):
            with self.subTest(first=a, second=b):
                self.assertLess(overlap_volume(first, second), 1e-5)

    def test_all_hardware_clear_all_printed_parts(self) -> None:
        for index, hardware in enumerate(self.hardware):
            for name, part in self.parts.items():
                with self.subTest(hardware=index, printed=name):
                    self.assertLess(overlap_volume(hardware, part), 1e-5)

    def test_lid_height_and_bearing(self) -> None:
        lid = top.lid_reference()
        for hardware in self.hardware:
            self.assertLess(overlap_volume(lid, hardware), 1e-5)
        for name, part in self.parts.items():
            with self.subTest(part=name):
                self.assertLess(overlap_volume(lid, part), 1e-5)
        for right in (False, True):
            beam = top.side_beam(right)
            self.assertAlmostEqual(beam.bounding_box().max.Z, params.ADDED_HEIGHT_MM)
            self.assertGreater(
                overlap_volume(lid.moved(Location((0, 0, -0.05))), beam), 100
            )

    def test_top_beam_bears_on_both_posts(self) -> None:
        for right in (False, True):
            suffix = "right" if right else "left"
            beam = top.side_beam(right).moved(
                Location((-0.05 if right else 0.05, 0, 0))
            )
            for end in ("front", "rear"):
                self.assertGreater(
                    overlap_volume(beam, self.parts[f"upper_{end}_{suffix}"]), 10
                )

    def test_top_bores_and_tip_clearance(self) -> None:
        self.assertGreater(
            params.LID_SCREW_PROTRUSION_APPROX_MM, params.M4_HEAT_SET_DEPTH_MM
        )
        self.assertGreater(
            params.LID_INSERT_BORE_DEPTH_MM - params.LID_SCREW_PROTRUSION_APPROX_MM, 1
        )
        for right in (False, True):
            x = params.HANDLE_HOLE_X_RIGHT_MM if right else params.HANDLE_HOLE_X_LEFT_MM
            for y in (params.HANDLE_HOLE_Y_FRONT_MM, params.HANDLE_HOLE_Y_REAR_MM):
                probe = Cylinder(
                    2,
                    params.LID_SCREW_PROTRUSION_APPROX_MM,
                    align=(Align.CENTER, Align.CENTER, Align.MIN),
                ).moved(
                    Location(
                        (
                            x,
                            y,
                            params.ADDED_HEIGHT_MM
                            - params.LID_SCREW_PROTRUSION_APPROX_MM,
                        )
                    )
                )
                self.assertLess(overlap_volume(probe, top.side_beam(right)), 1e-5)

    def test_stock_transverse_members(self) -> None:
        for rear in (False, True):
            member = Box(
                params.BODY_WIDTH_MM,
                params.TOP_MEMBER_WIDTH_MM,
                params.LIP_DEPTH_MM,
                align=(Align.MIN, Align.MIN, Align.MIN),
            ).moved(
                Location(
                    (
                        0,
                        params.BODY_DEPTH_MM - params.TOP_MEMBER_WIDTH_MM
                        if rear
                        else 0,
                        -params.LIP_DEPTH_MM,
                    )
                )
            )
            for right in (False, True):
                name = f"mount_{'rear' if rear else 'front'}_{'right' if right else 'left'}"
                self.assertLess(overlap_volume(member, self.parts[name]), 1e-5)

    def test_coupon_geometry(self) -> None:
        for part in (
            top.lid_fit_coupon(),
            release.m4_pilot_coupon(),
            release.top_post_coupon(),
        ):
            self.assertTrue(part.is_valid)
            self.assertEqual(len(part.solids()), 1)

    def test_diagonal_lap_has_clamping_contact(self) -> None:
        lower, upper = self.parts["diagonal_0"], self.parts["diagonal_1"]
        self.assertLess(overlap_volume(lower, upper), 1e-5)
        self.assertGreater(
            overlap_volume(lower, upper.moved(Location((0, -0.05, 0)))), 10
        )


if __name__ == "__main__":
    unittest.main()
