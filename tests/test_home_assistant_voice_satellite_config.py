import unittest
from pathlib import Path

import yaml


class VoiceSatelliteConfigTests(unittest.TestCase):
    def test_mic_volume_overrides_upstream_maximum_default(self) -> None:
        root = Path(__file__).resolve().parents[1]
        manifests = yaml.safe_load_all(
            (root / "gitops/voice/satellite.yaml").read_text()
        )
        manifest = next(item for item in manifests if item["kind"] == "Deployment")
        container = manifest["spec"]["template"]["spec"]["containers"][0]
        args = container["args"]
        volume_index = args.index("--mic-volume")
        self.assertEqual(args[volume_index + 1], "75")


if __name__ == "__main__":
    unittest.main()
