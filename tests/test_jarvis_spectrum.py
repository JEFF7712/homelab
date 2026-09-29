from __future__ import annotations

import importlib.util
import json
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from typing import Any
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
DAEMON_PATH = REPO_ROOT / "flake" / "modules" / "jarvis-spectrum.py"


def load_daemon_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "jarvis_spectrum_under_test", DAEMON_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DAEMON = load_daemon_module()

PW_DUMP_SAMPLE = [
    {
        "type": "PipeWire:Interface:Node",
        "info": {
            "props": {
                "node.name": "alsa_output.usb-Generic_USB_SPDIF_Adapter_202110200032-00.analog-stereo",
                "media.class": "Audio/Sink",
            }
        },
    },
    {
        "type": "PipeWire:Interface:Node",
        "info": {
            "props": {
                "node.name": "alsa_output.usb-Generic_USB_SPDIF_Adapter_202110200032-00.analog-stereo.monitor",
                "media.class": "Audio/Source",
            }
        },
    },
    {
        "type": "PipeWire:Interface:Node",
        "info": {
            "props": {
                "node.name": "alsa_input.usb-Kingston_HyperX_QuadCast_S-00.analog-stereo",
                "media.class": "Audio/Source",
            }
        },
    },
]


class MonitorSelectionTest(unittest.TestCase):
    def test_picks_spdif_monitor(self) -> None:
        self.assertEqual(
            DAEMON.pick_monitor_source(PW_DUMP_SAMPLE, "SPDIF"),
            "alsa_output.usb-Generic_USB_SPDIF_Adapter_202110200032-00.analog-stereo.monitor",
        )

    def test_falls_back_to_auto_without_match(self) -> None:
        self.assertEqual(DAEMON.pick_monitor_source(PW_DUMP_SAMPLE, "HDMI"), "auto")

    def test_falls_back_to_auto_on_garbage(self) -> None:
        for bad in (None, {}, "nope", [{"type": "PipeWire:Interface:Node"}]):
            with self.subTest(bad=bad):
                self.assertEqual(DAEMON.pick_monitor_source(bad, "SPDIF"), "auto")

    def test_never_selects_a_microphone(self) -> None:
        source = DAEMON.pick_monitor_source(PW_DUMP_SAMPLE, "QuadCast")
        self.assertEqual(source, "auto")


class CavaFrameTest(unittest.TestCase):
    def test_parses_ascii_frame(self) -> None:
        self.assertEqual(
            DAEMON.parse_cava_frame("0;50;100;150;\n", 4, 100), [0, 50, 100, 100]
        )

    def test_pads_short_frames(self) -> None:
        self.assertEqual(DAEMON.parse_cava_frame("7;\n", 3, 100), [7, 0, 0])

    def test_truncates_long_frames(self) -> None:
        self.assertEqual(len(DAEMON.parse_cava_frame("1;" * 40, 28, 100) or []), 28)

    def test_rejects_garbage(self) -> None:
        for bad in ("", "\n", "a;b;\n", "1;NaN;\n", ";;;\n"):
            with self.subTest(bad=bad):
                self.assertIsNone(DAEMON.parse_cava_frame(bad, 4, 100))


class CavaConfigTest(unittest.TestCase):
    def test_config_targets_monitor_with_28_bars(self) -> None:
        text = DAEMON.build_cava_config("my-sink.monitor", 28, 20, 100)
        self.assertIn("source = my-sink.monitor", text)
        self.assertIn("bars = 28", text)
        self.assertIn("method = raw", text)
        self.assertIn("data_format = ascii", text)


class ServerTest(unittest.TestCase):
    def test_spectrum_and_healthz_round_trip(self) -> None:
        state = DAEMON.SpectrumState(4)
        state.source = "test.monitor"
        state.cava_alive = True
        state.publish([10, 20, 30, 40])
        server = DAEMON.ThreadingHTTPServer(
            ("127.0.0.1", 0), DAEMON.make_handler(state, 100)
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_port
            conn = HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("GET", "/healthz")
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            health = json.loads(response.read().decode())
            self.assertTrue(health["ok"])
            self.assertEqual(health["source"], "test.monitor")
            self.assertEqual(health["bars"], 4)
            self.assertEqual(response.getheader("Access-Control-Allow-Origin"), "*")

            conn.request("GET", "/spectrum", headers={"Accept": "text/event-stream"})
            stream = conn.getresponse()
            self.assertEqual(stream.status, 200)
            self.assertIn("text/event-stream", stream.getheader("Content-Type"))
            payload = b""
            while b"\n\n" not in payload:
                chunk = stream.read(1)
                if not chunk:
                    break
                payload += chunk
            while payload.strip().startswith(b":"):
                payload = b""
                while b"\n\n" not in payload:
                    chunk = stream.read(1)
                    if not chunk:
                        break
                    payload += chunk
            self.assertTrue(payload.startswith(b"data: "))
            frame = json.loads(payload[len(b"data: ") :].decode())
            self.assertEqual(frame["bars"], [10, 20, 30, 40])
            self.assertEqual(frame["max"], 100)
        finally:
            server.shutdown()
            server.server_close()

    def test_unknown_path_is_404(self) -> None:
        state = DAEMON.SpectrumState(4)
        server = DAEMON.ThreadingHTTPServer(
            ("127.0.0.1", 0), DAEMON.make_handler(state, 100)
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            conn.request("GET", "/nope")
            self.assertEqual(conn.getresponse().status, 404)
        finally:
            server.shutdown()
            server.server_close()


class DeploymentContractTest(unittest.TestCase):
    def test_nixos_module_references_daemon_and_cava(self) -> None:
        module = (REPO_ROOT / "flake" / "modules" / "jarvis-spectrum.nix").read_text(
            encoding="utf-8"
        )
        self.assertIn("jarvis-spectrum.py", module)
        self.assertIn("pkgs.cava", module)
        self.assertIn("2975", module)

    def test_homelab05_enables_spectrum_and_pins_face_version(self) -> None:
        host = (REPO_ROOT / "flake" / "hosts" / "homelab-05" / "default.nix").read_text(
            encoding="utf-8"
        )
        self.assertIn("jarvis-spectrum", host)
        self.assertIn("homelab.jarvis-spectrum.enable = true;", host)
        config = json.loads(
            (REPO_ROOT / "home-assistant" / "www" / "jarvis" / "config.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("spectrum_url", config)
        self.assertIn(str(config["face_version"]), host)

    def test_resolve_source_uses_pw_dump(self) -> None:
        with patch.object(
            DAEMON.subprocess,
            "run",
            return_value=DAEMON.subprocess.CompletedProcess(
                args=["pw-dump"], returncode=0, stdout=json.dumps(PW_DUMP_SAMPLE)
            ),
        ):
            self.assertEqual(
                DAEMON.resolve_source("pw-dump", "SPDIF"),
                "alsa_output.usb-Generic_USB_SPDIF_Adapter_202110200032-00.analog-stereo.monitor",
            )


if __name__ == "__main__":
    unittest.main()
