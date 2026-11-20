import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "moonlight_input_status",
    Path(__file__).resolve().parents[1] / "flake/modules/moonlight-input-status.py",
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class InputSettingsTests(unittest.TestCase):
    def test_reports_input_settings_without_pairing_material(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Moonlight.conf"
            path.write_text(
                "[General]\ngamepadmouse=false\nswapfacebuttons=true\nprivatekey=secret\n"
                "[gcmapping]\nsize=1\n1\\guid=controller-guid\n"
                "1\\mapping=Controller,start:b9,back:b8,\n"
                "[hosts]\n1\\certificate=secret\n"
            )
            settings = module.input_settings(path)
        self.assertEqual(settings["gamepadmouse"], "false")
        self.assertEqual(settings["swapfacebuttons"], "true")
        self.assertIn("1\\mapping", settings["mappings"])
        self.assertNotIn("secret", str(settings))

    def test_uses_moonlight_defaults_for_absent_settings(self):
        settings = module.input_settings(Path("/does/not/exist/Moonlight.conf"))
        self.assertEqual(settings["gamepadmouse"], "true")
        self.assertEqual(settings["mappings"], {})
