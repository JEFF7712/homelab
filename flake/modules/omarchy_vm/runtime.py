from __future__ import annotations

import argparse
import json
import secrets
import socket
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def read_values(path: Path) -> dict[str, int]:
    return {
        key: int(value)
        for key, value in (line.split() for line in path.read_text().splitlines())
    }


def metrics(
    cgroup: Path, disk: Path, vcpus: int, memory_mib: int, disk_gib: int
) -> str:
    values: dict[str, float] = {
        "omarchy_vm_allocated_vcpus": vcpus,
        "omarchy_vm_allocated_memory_bytes": memory_mib * 1024**2,
        "omarchy_vm_allocated_disk_bytes": disk_gib * 1024**3,
        "omarchy_vm_running": int(cgroup.exists()),
        "omarchy_vm_collected_timestamp_seconds": time.time(),
    }
    if cgroup.exists():
        cpu = read_values(cgroup / "cpu.stat")
        events = read_values(cgroup / "memory.events")
        values.update(
            {
                "omarchy_vm_cpu_seconds_total": cpu["usage_usec"] / 1e6,
                "omarchy_vm_cpu_throttled_seconds_total": cpu.get("throttled_usec", 0)
                / 1e6,
                "omarchy_vm_memory_current_bytes": int(
                    (cgroup / "memory.current").read_text()
                ),
                "omarchy_vm_memory_limit_bytes": int(
                    (cgroup / "memory.max").read_text()
                ),
                "omarchy_vm_oom_kills_total": events.get("oom_kill", 0),
                "omarchy_vm_memory_high_events_total": events.get("high", 0),
            }
        )
        for resource in ["cpu", "memory", "io"]:
            for line in (cgroup / f"{resource}.pressure").read_text().splitlines():
                kind, *fields = line.split()
                total = dict(field.split("=", 1) for field in fields)["total"]
                values[f"omarchy_vm_{resource}_pressure_{kind}_seconds_total"] = (
                    int(total) / 1e6
                )
    if disk.exists():
        values["omarchy_vm_disk_host_allocated_bytes"] = disk.stat().st_blocks * 512
    types = {name: "counter" if name.endswith("_total") else "gauge" for name in values}
    return "".join(
        f"# HELP {name} {name.removeprefix('omarchy_vm_').replace('_', ' ')}.\n# TYPE {name} {types[name]}\n{name} {value}\n"
        for name, value in sorted(values.items())
    )


def guest_powerdown(path: Path) -> bool:
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(3)
            client.connect(str(path))
            stream = client.makefile("rwb")
            identifier = secrets.randbits(63)
            command = {"execute": "guest-sync", "arguments": {"id": identifier}}
            stream.write(b"\xff\n" + json.dumps(command).encode() + b"\n")
            stream.flush()
            for _ in range(64):
                line = stream.readline()
                if not line:
                    return False
                try:
                    reply = json.loads(line.lstrip(b"\xff"))
                except ValueError:
                    continue
                if reply.get("return") == identifier:
                    break
            else:
                return False
            command = {"execute": "guest-shutdown", "arguments": {"mode": "powerdown"}}
            stream.write(json.dumps(command).encode() + b"\n")
            stream.flush()
            try:
                line = stream.readline()
                return not line or "error" not in json.loads(line)
            except TimeoutError:
                return True
    except OSError:
        return False


def acpi_powerdown(path: str) -> None:
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(5)
        client.connect(path)
        stream = client.makefile("rwb")
        json.loads(stream.readline())
        for command in ["qmp_capabilities", "system_powerdown"]:
            stream.write(json.dumps({"execute": command}).encode() + b"\n")
            stream.flush()
            while True:
                reply = json.loads(stream.readline())
                if "error" in reply:
                    raise RuntimeError(reply["error"])
                if "return" in reply:
                    break


def stop_vm(path: str) -> None:
    if not Path(path).exists():
        return
    if not guest_powerdown(Path(path).with_name("guest-agent.sock")):
        acpi_powerdown(path)
    for _ in range(110):
        try:
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(1)
                client.connect(path)
        except OSError:
            return
        time.sleep(1)
    raise TimeoutError("guest did not finish shutdown")


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    stop = commands.add_parser("stop")
    stop.add_argument("--socket", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--disk", required=True, type=Path)
    serve.add_argument(
        "--cgroup",
        type=Path,
        default=Path("/sys/fs/cgroup/system.slice/omarchy-akshath.service"),
    )
    serve.add_argument("--vcpus", required=True, type=int)
    serve.add_argument("--memory-mib", required=True, type=int)
    serve.add_argument("--disk-gib", required=True, type=int)
    serve.add_argument("--listen", default="10.0.30.15")
    serve.add_argument("--port", type=int, default=9126)
    args = parser.parse_args()
    if args.command == "stop":
        stop_vm(args.socket)
        return

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            try:
                if self.path == "/metrics":
                    body = metrics(
                        args.cgroup,
                        args.disk,
                        args.vcpus,
                        args.memory_mib,
                        args.disk_gib,
                    ).encode()
                elif self.path == "/guest-metrics":
                    with urllib.request.urlopen(
                        "http://10.250.60.2:9100/metrics", timeout=3
                    ) as response:
                        body = response.read(4 * 1024 * 1024)
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (OSError, ValueError, urllib.error.URLError):
                self.send_error(503, "VM metrics unavailable")

        def log_message(self, format: str, *args: object) -> None:
            return

    ThreadingHTTPServer((args.listen, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
