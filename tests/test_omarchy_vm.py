from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from flake.modules.omarchy_vm.runtime import guest_powerdown, metrics
from scripts.omarchy_vm.seed import installer_config


class OmarchyVMTests(unittest.TestCase):
    def test_guest_agent_ignores_stale_errors_before_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guest-agent.sock"
            commands = []
            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(str(path))
                listener.listen()

                def serve() -> None:
                    client, _ = listener.accept()
                    with client:
                        stream = client.makefile("rwb")
                        self.assertEqual(stream.readline(), b"\xff\n")
                        sync = json.loads(stream.readline())
                        stream.write(
                            b'{"error":{"class":"GenericError","desc":"stale input"}}\n'
                        )
                        stream.write(
                            json.dumps({"return": sync["arguments"]["id"]}).encode()
                            + b"\n"
                        )
                        stream.flush()
                        commands.append(json.loads(stream.readline()))

                thread = threading.Thread(target=serve, daemon=True)
                thread.start()
                self.assertTrue(guest_powerdown(path))
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive())
                self.assertEqual(
                    commands,
                    [{"execute": "guest-shutdown", "arguments": {"mode": "powerdown"}}],
                )

    def test_shutdown_does_not_wait_for_stale_socket_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qmp.sock"
            listener = socket.socket(socket.AF_UNIX)
            listener.bind(str(path))
            listener.listen()

            def serve() -> None:
                client, _ = listener.accept()
                with client:
                    stream = client.makefile("rwb")
                    stream.write(b'{"QMP":{}}\n')
                    stream.flush()
                    for _ in range(2):
                        json.loads(stream.readline())
                        stream.write(b'{"return":{}}\n')
                        stream.flush()
                listener.close()

            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            runtime = (
                Path(__file__).resolve().parents[1]
                / "flake/modules/omarchy_vm/runtime.py"
            )
            result = subprocess.run(
                [sys.executable, str(runtime), "stop", "--socket", str(path)],
                capture_output=True,
                text=True,
                timeout=4,
            )
            thread.join(timeout=2)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(path.exists())

    def test_down_guest_does_not_report_old_usage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = metrics(root / "missing", root / "missing.qcow2", 4, 8192, 100)
            self.assertIn("omarchy_vm_running 0", output)
            self.assertIn("omarchy_vm_allocated_memory_bytes 8589934592", output)
            self.assertNotIn("omarchy_vm_cpu_seconds_total", output)

    def test_live_cgroup_usage_and_pressure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, content in {
                "cpu.stat": "usage_usec 12500000\nthrottled_usec 500000\n",
                "memory.events": "high 2\noom_kill 1\n",
                "memory.current": "8589934592\n",
                "memory.max": "9663676416\n",
                "cpu.pressure": "some avg10=0.00 avg60=0.00 avg300=0.00 total=1000000\n",
                "memory.pressure": "some avg10=0.00 avg60=0.00 avg300=0.00 total=2000000\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=500000\n",
                "io.pressure": "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n",
            }.items():
                (root / name).write_text(content)
            disk = root / "disk.qcow2"
            disk.write_bytes(b"allocated")
            output = metrics(root, disk, 4, 8192, 100)
            self.assertIn("omarchy_vm_cpu_seconds_total 12.5", output)
            self.assertIn("omarchy_vm_cpu_throttled_seconds_total 0.5", output)
            self.assertIn("omarchy_vm_oom_kills_total 1", output)
            self.assertIn("omarchy_vm_memory_pressure_full_seconds_total 0.5", output)
            self.assertIn(
                f"omarchy_vm_disk_host_allocated_bytes {disk.stat().st_blocks * 512}",
                output,
            )

    def test_installer_targets_guest_disk_and_fits_within_it(self) -> None:
        config = installer_config(100)
        disk = config["disk_config"]["device_modifications"][0]
        self.assertEqual(disk["device"], "/dev/vda")
        self.assertEqual(config["kernels"], ["linux-omarchy"])
        self.assertEqual(
            config["omarchy_install"]["storage"]["kernel"], "linux-omarchy"
        )
        self.assertNotIn("disk_encryption", config["disk_config"])
        boot, root = disk["partitions"]
        self.assertEqual(
            boot["start"]["value"] + boot["size"]["value"], root["start"]["value"]
        )
        self.assertLess(root["start"]["value"] + root["size"]["value"], 100 * 1024**3)


if __name__ == "__main__":
    unittest.main()
