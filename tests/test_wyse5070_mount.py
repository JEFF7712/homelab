"""Repo gate for the Wyse 5070 Extended T1 mount (offline, no CAD kernel)."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARAMS_PATH = ROOT / "3d-prints" / "wyse5070_extended_t1" / "params.py"


def load_params():  # type: ignore[no-untyped-def]
    import sys

    name = "wyse5070_params"
    spec = importlib.util.spec_from_file_location(name, PARAMS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


try:
    params = load_params()
except OSError:
    params = None


@unittest.skipIf(params is None, "3d-prints/ is git-ignored and absent")
class Wyse5070MountTests(unittest.TestCase):
    def test_params_source_exists(self) -> None:
        self.assertTrue(PARAMS_PATH.is_file(), f"missing {PARAMS_PATH}")

    def test_device_spec_matches_dell(self) -> None:
        self.assertAlmostEqual(params.DEVICE_X_MM, 184.0)
        self.assertAlmostEqual(params.DEVICE_BODY_Y_MM, 55.9)
        self.assertAlmostEqual(params.DEVICE_Y_MM, 59.5)
        self.assertAlmostEqual(params.DEVICE_Z_MM, 184.0)

    def test_cradle_fits_t1_and_a1(self) -> None:
        env = params.cradle_envelope()
        self.assertTrue(params.fits_rack(), f"envelope {env} exceeds T1")
        self.assertTrue(params.fits_bed(), "ear span exceeds A1 bed")

    def test_ear_span_matches_existing_prints(self) -> None:
        self.assertGreaterEqual(params.EAR_SPAN_MM, 250.0)
        self.assertLessEqual(params.EAR_SPAN_MM, params.BED_X_MM)

    def test_slot_pattern_matches_deskpi_panel(self) -> None:
        self.assertAlmostEqual(params.SLOT_EDGE_OFFSET_MM, 10.0)
        self.assertAlmostEqual(params.SLOT_HEIGHT_MM, 13.0)
        self.assertAlmostEqual(params.SLOT_WIDTH_MM, 7.0)

    def test_open_support_rails_are_sane(self) -> None:
        self.assertGreater(params.SUPPORT_RAIL_MM, params.TIE_SLOT_X_MM)
        self.assertGreater(params.SUPPORT_THICKNESS_MM, 0)
        self.assertGreater(params.LOWER_AIR_GAP_MM, 0)
        self.assertGreater(
            params.SUPPORT_RAIL_MM, params.FOOT_INSET_X_MM + params.FOOT_X_MM
        )

    def test_side_vents_keep_structural_ribs(self) -> None:
        self.assertGreaterEqual(params.SIDE_VENT_COUNT, 8)
        self.assertLessEqual(params.SIDE_VENT_DEPTH_MM, 12.0)
        self.assertGreaterEqual(params.SIDE_VENT_BOTTOM_RIB_MM, 8.0)
        self.assertGreaterEqual(params.SIDE_VENT_TOP_RIB_MM, 8.0)

    def test_retention_and_load_path_are_explicit(self) -> None:
        self.assertEqual(params.CAPTURE_TAB_COUNT, 3)
        self.assertGreater(params.RETAINER_SCREW_DIA_MM, 3.0)
        self.assertLess(params.WEB_WALL_MM, params.WALL_MM)
        self.assertGreaterEqual(params.GUSSET_DEPTH_MM, 30.0)

    def test_measured_feet_are_represented(self) -> None:
        self.assertEqual((params.FOOT_X_MM, params.FOOT_Z_MM), (5.8, 20.0))
        self.assertEqual(params.FOOT_ROW_SPACING_Z_MM, 128.7)
        self.assertLess(params.FOOT_POCKET_DEPTH_MM, params.SUPPORT_THICKNESS_MM)

    def test_front_position_and_compliant_preload_are_explicit(self) -> None:
        self.assertEqual(params.DEVICE_FRONT_Z_MM, params.FRONT_STOP_DEPTH_MM)
        self.assertGreater(
            params.ANTI_RATTLE_TAB_PROTRUSION_MM,
            params.SIDE_CLEARANCE_MM,
        )
        self.assertLess(params.ANTI_RATTLE_TAB_THICKNESS_MM, params.WEB_WALL_MM)

    def test_top_capture_is_reinforced_and_uses_seated_height(self) -> None:
        self.assertGreaterEqual(params.TOP_LIP_MM, 6.0)
        self.assertGreaterEqual(params.TOP_LIP_THICKNESS_MM, 4.0)
        seated_top = (
            params.device_floor_y_mm()
            + params.DEVICE_Y_MM
            - params.FOOT_POCKET_DEPTH_MM
        )
        self.assertAlmostEqual(params.top_lip_y_mm() - seated_top, 0.8)
        self.assertGreaterEqual(params.FRONT_TOP_BRACE_HEIGHT_MM, 4.0)

    def test_fit_coupon_is_shorter_than_half_depth(self) -> None:
        self.assertGreater(params.FIT_TEST_DEPTH_MM, 30.0)
        self.assertLess(params.FIT_TEST_DEPTH_MM, 35.0)


if __name__ == "__main__":
    unittest.main()
