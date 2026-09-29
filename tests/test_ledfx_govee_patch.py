from __future__ import annotations

import ast
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent


class FakeArray:
    """Minimal stand-in for the numpy array flush() receives.

    The driver only needs reshape/mean/round/astype on the render output, so
    this keeps the test runnable in the pinned dev shell, which has no numpy.
    """

    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def reshape(self, rows: int, cols: int) -> FakeArray:
        assert cols == 3, cols
        if rows != -1:
            assert rows * cols == len(self._values), (rows, cols, len(self._values))
        return FakeArray(self._values)

    def mean(self, axis: int) -> FakeArray:
        assert axis == 0
        assert len(self._values) % 3 == 0
        pixels = [self._values[i : i + 3] for i in range(0, len(self._values), 3)]
        return FakeArray([sum(column) / len(pixels) for column in zip(*pixels)])

    def round(self) -> FakeArray:
        return FakeArray([int(value + 0.5) for value in self._values])

    def astype(self, _kind: object) -> FakeArray:
        return self

    def __getitem__(self, index: int) -> int:
        return self._values[index]

    def __len__(self) -> int:
        return len(self._values)


PATCH = REPO_ROOT / "gitops/music-assistant/ledfx-patches/govee.py"
MANIFEST = REPO_ROOT / "gitops/music-assistant/ledfx.yaml"
UPSTREAM_FILE = "ledfx/devices/govee.py"
UPSTREAM_VERSION = "2.1.9"


def load_govee():
    """Import the patched driver with the ledfx package stubbed out.

    The real ledfx package is not a test dependency, so the two base classes
    and helpers the driver imports are replaced with minimal stand-ins. That
    keeps flush() testable as real behavior rather than a source-string
    assertion.
    """
    created: dict[str, types.ModuleType] = {}

    def stub(name: str, **attrs: object) -> types.ModuleType:
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        created[name] = module
        return module

    class NetworkedDevice:
        def __init__(self, ledfx, config):
            self._config = config
            self.name = config.get("name", "test")

        def activate(self):
            pass

        def deactivate(self):
            pass

        def set_offline(self):
            pass

        def update_config(self, config):
            pass

    class SocketSingleton:
        def __init__(self, recv_port: int):
            self.recv_port = recv_port

    stub("ledfx")
    stub("ledfx.devices", NetworkedDevice=NetworkedDevice)
    stub("ledfx.devices.__init__", fps_validator=lambda v: v)
    stub("ledfx.devices.utils")
    stub("ledfx.devices.utils.socket_singleton", SocketSingleton=SocketSingleton)
    stub("ledfx.utils", AVAILABLE_FPS=[20, 30, 60, 120])

    saved = {name: sys.modules.get(name) for name in created}
    sys.modules.update(created)
    try:
        spec = importlib.util.spec_from_file_location("patched_govee", PATCH)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def patch_dependencies() -> None:
    """Stub the non-stdlib imports so the driver can be imported in the
    pinned dev shell, which has neither voluptuous nor numpy."""
    if "voluptuous" in sys.modules:
        return

    class _Marker:
        def __init__(self, *_args, **_kwargs):
            pass

        def __hash__(self):
            return id(self)

    module = types.ModuleType("voluptuous")
    module.Schema = _Marker
    module.Required = _Marker
    module.Optional = _Marker
    module.All = _Marker
    module.Range = _Marker
    sys.modules["voluptuous"] = module


patch_dependencies()


govee = load_govee()


def make_device(sent: list[dict]):
    device = govee.Govee.__new__(govee.Govee)
    device._config = {
        "ip_address": "10.0.20.166",
        "pixel_count": 1,
        "ignore_status": False,
    }
    device.name = "Floor Lamp"
    device.udp_server = types.SimpleNamespace(
        sendto=lambda data, addr: sent.append((json.loads(data.decode()), addr))
    )
    return device


class FlushEmitsColorwcTests(unittest.TestCase):
    """The H6004 ignores the razer tunnel; colorwc is the working path."""

    def test_single_pixel_is_exact(self) -> None:
        sent: list = []
        device = make_device(sent)
        device.flush(FakeArray([255, 0, 128]))
        self.assertEqual(len(sent), 1)
        payload, addr = sent[0]
        self.assertEqual(addr, ("10.0.20.166", 4003))
        self.assertEqual(payload["msg"]["cmd"], "colorwc")
        self.assertEqual(
            payload["msg"]["data"],
            {"color": {"r": 255, "g": 0, "b": 128}, "colorTemInKelvin": 0},
        )

    def test_never_emits_razer_tunnel(self) -> None:
        sent: list = []
        device = make_device(sent)
        device.flush(FakeArray([10, 20, 30]))
        self.assertEqual([p for p, _ in sent if p["msg"]["cmd"] == "razer"], [])

    def test_zero_color_temp_forces_rgb_mode(self) -> None:
        # A nonzero colorTemInKelvin leaves the bulb in CCT mode, where it
        # ignores colorwc. This is the state music mode got stuck in.
        sent: list = []
        device = make_device(sent)
        device.flush(FakeArray([1, 2, 3]))
        self.assertEqual(sent[0][0]["msg"]["data"]["colorTemInKelvin"], 0)

    def test_multi_zone_averages_to_single_color(self) -> None:
        sent: list = []
        device = make_device(sent)
        device.flush(FakeArray([255, 0, 0, 0, 0, 255]))
        self.assertEqual(
            sent[0][0]["msg"]["data"]["color"],
            {"r": 128, "g": 0, "b": 128},
        )

    def test_flat_pixel_input_is_handled(self) -> None:
        # flush() receives a flattened array from the render pipeline.
        sent: list = []
        device = make_device(sent)
        device.flush(FakeArray([0, 255, 0]))
        self.assertEqual(sent[0][0]["msg"]["data"]["color"], {"r": 0, "g": 255, "b": 0})


class FlushThrottleTests(unittest.TestCase):
    """Music mode renders faster than the H6004 tolerates; flush() caps it."""

    def test_rapid_large_change_is_dropped(self) -> None:
        sent: list = []
        device = make_device(sent)
        with mock.patch.object(govee.time, "monotonic", return_value=1000.0):
            device.flush(FakeArray([255, 0, 0]))
        self.assertEqual(len(sent), 1)
        with mock.patch.object(govee.time, "monotonic", return_value=1000.01):
            device.flush(FakeArray([0, 0, 255]))
        self.assertEqual(len(sent), 1)

    def test_large_change_after_interval_sends(self) -> None:
        sent: list = []
        device = make_device(sent)
        with mock.patch.object(govee.time, "monotonic", return_value=1000.0):
            device.flush(FakeArray([255, 0, 0]))
        with mock.patch.object(
            govee.time,
            "monotonic",
            return_value=1000.0 + govee._MIN_INTERVAL_S + 0.01,
        ):
            device.flush(FakeArray([0, 0, 255]))
        self.assertEqual(len(sent), 2)

    def test_near_duplicate_suppressed_after_interval(self) -> None:
        sent: list = []
        device = make_device(sent)
        with mock.patch.object(govee.time, "monotonic", return_value=1000.0):
            device.flush(FakeArray([100, 100, 100]))
        with mock.patch.object(govee.time, "monotonic", return_value=1010.0):
            device.flush(FakeArray([105, 103, 108]))
        self.assertEqual(len(sent), 1)

    def test_burst_caps_to_first_send(self) -> None:
        sent: list = []
        device = make_device(sent)
        with mock.patch.object(govee.time, "monotonic", return_value=1000.0):
            for i in range(30):
                device.flush(FakeArray([255, 0, 0] if i % 2 == 0 else [0, 0, 255]))
        self.assertEqual(len(sent), 1)

    def test_throttle_bounds_are_sane(self) -> None:
        self.assertLessEqual(govee._MAX_SEND_HZ, 10.0)
        self.assertGreaterEqual(govee._MIN_INTERVAL_S, 0.1)
        self.assertGreaterEqual(govee._COLOR_THRESHOLD, 1)


class ActivationTests(unittest.TestCase):
    def test_activate_sends_no_razer_handshake(self) -> None:
        sent: list = []
        device = make_device(sent)
        with self.assertRaises(AttributeError):
            device.send_activate()

    def test_brightness_uses_lan_api_name(self) -> None:
        sent: list = []
        device = make_device(sent)
        device.set_brightness(100)
        self.assertEqual(
            sent[0][0],
            {"msg": {"cmd": "brightness", "data": {"value": 100}}},
        )

    def test_razer_tunnel_helpers_are_gone(self) -> None:
        for attribute in (
            "create_razer_packet",
            "send_encoded_packet",
            "send_deactivate",
            "send_activate",
            "calculate_xor_checksum_fast",
        ):
            self.assertFalse(
                hasattr(govee.Govee, attribute),
                f"{attribute} should not exist in the colorwc driver",
            )

    def test_dead_razer_headers_removed(self) -> None:
        source = PATCH.read_text(encoding="utf-8")
        for header in ("pre_dreams", "pre_chroma", "pre__govee", "pre_active"):
            self.assertNotIn(header, source)


class ManifestMountTests(unittest.TestCase):
    """The patch is mounted over the installed module, not baked into an image."""

    def test_manifest_mounts_patched_driver(self) -> None:
        manifest = MANIFEST.read_text(encoding="utf-8")
        self.assertIn("ledfx-govee-patch", manifest)
        self.assertIn("ledfx/devices/govee.py", manifest)
        self.assertIn("subPath: govee.py", manifest)

    def test_upstream_digest_stays_pinned(self) -> None:
        manifest = MANIFEST.read_text(encoding="utf-8")
        self.assertIn("@sha256:", manifest)
        self.assertNotIn("image: ledfx", manifest)

    def test_patch_file_is_valid_python(self) -> None:
        ast.parse(PATCH.read_text(encoding="utf-8"))

    def test_patch_documents_upstream_provenance(self) -> None:
        source = PATCH.read_text(encoding="utf-8")
        self.assertIn(UPSTREAM_FILE, source)
        self.assertIn(UPSTREAM_VERSION, source)

    def test_configmap_body_matches_patched_file(self) -> None:
        # The ConfigMap is the deployed copy; the standalone file is the
        # reviewable source. They must not drift.
        import yaml

        documents = list(yaml.safe_load_all(MANIFEST.read_text(encoding="utf-8")))
        configmap = next(
            d
            for d in documents
            if d["kind"] == "ConfigMap"
            and d["metadata"]["name"] == "ledfx-govee-patch-code"
        )
        embedded = configmap["data"]["govee.py"]
        self.assertEqual(embedded.rstrip(), PATCH.read_text(encoding="utf-8").rstrip())


if __name__ == "__main__":
    unittest.main()
