from __future__ import annotations

import argparse
import json
import os
import select
import socket
import stat
import subprocess
import sys
import termios
import time
import tty
from pathlib import Path
from typing import Any


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")
    path.chmod(0o600)


def boundary_state() -> dict[str, Any]:
    routes = Path("/proc/net/route").read_text().splitlines()[1:]
    return {
        "namespace": os.readlink("/proc/self/ns/net"),
        "interfaces": sorted(name for _, name in socket.if_nameindex()),
        "ipv4_routes": routes,
        "ipv6_routes": Path("/proc/net/ipv6_route").read_text().splitlines(),
    }


def require_boundary(state: dict[str, Any], original: str) -> None:
    if state["namespace"] == original:
        raise ValueError("QEMU must not run in the original host network namespace")
    ipv6_reject_only = all(
        len(fields := line.split()) == 10
        and fields[0] == "0" * 32
        and fields[1] == "00"
        and fields[4] == "0" * 32
        and int(fields[8], 16) & 0x200
        and fields[9] == "lo"
        for line in state["ipv6_routes"]
    )
    if state["interfaces"] != ["lo"] or state["ipv4_routes"] or not ipv6_reject_only:
        raise ValueError(
            "Recovery boundary must have only loopback and no usable routes"
        )


def qemu_arguments(work: Path, checkpoint: Path, qemu: str, iso: Path) -> list[str]:
    return [
        qemu,
        "-name",
        "darkbit-recovery-qualification",
        "-machine",
        "q35,accel=kvm",
        "-cpu",
        "host",
        "-m",
        "6144",
        "-smp",
        "2",
        "-display",
        "none",
        "-vga",
        "none",
        "-monitor",
        "none",
        "-boot",
        "order=d,menu=off",
        "-cdrom",
        str(iso),
        "-drive",
        f"file={work / 'scratch.qcow2'},format=qcow2,if=virtio",
        "-netdev",
        "user,id=recovery,restrict=on,ipv6=off,net=192.0.2.0/24,dhcpstart=192.0.2.15",
        "-device",
        "virtio-net-pci,netdev=recovery",
        "-fsdev",
        f"local,id=checkpoint,path={checkpoint},security_model=none,readonly=on",
        "-device",
        "virtio-9p-pci,fsdev=checkpoint,mount_tag=checkpoint",
        "-serial",
        f"unix:{work / 'serial.sock'},server=on,wait=off",
        "-qmp",
        f"unix:{work / 'qmp.sock'},server=on,wait=off",
        "-S",
    ]


def prepare(work: Path, checkpoint: Path, signers: Path) -> None:
    from scripts.recovery_pack import file_digest

    if work.exists() or work == checkpoint or checkpoint in work.parents:
        raise ValueError("Use a new workspace outside the sealed checkpoint")
    if any(c in str(path) for path in (work, checkpoint) for c in [",", "\n"]):
        raise ValueError("QEMU media paths cannot contain commas or newlines")
    if len(str(work / "serial.sock").encode()) >= 108:
        raise ValueError("Workspace path is too long for control sockets")
    subprocess.run(
        [
            "ssh-keygen",
            "-Y",
            "verify",
            "-f",
            str(signers),
            "-I",
            "Rupan",
            "-n",
            "homelab-recovery",
            "-s",
            str(checkpoint / "manifest.json.sig"),
        ],
        input=(checkpoint / "manifest.json").read_bytes(),
        capture_output=True,
        check=True,
    )
    manifest = json.loads((checkpoint / "manifest.json").read_text())
    assets = {a["id"]: a for a in manifest["assets"]}
    entries = [*assets["installer"]["files"], *assets["tools"]["files"]]
    for entry in entries:
        path = checkpoint / entry["path"]
        if (
            path.is_symlink()
            or path.stat().st_size != entry["bytes"]
            or file_digest(path) != entry["sha256"]
        ):
            raise ValueError(
                "Retained installer/tool export does not match authenticated manifest"
            )
    tools = json.loads((checkpoint / "receipts/export-tools.json").read_text())[
        "entrypoints"
    ]
    qemu = tools["qemu-system-x86_64"]
    iso = checkpoint / next(
        e["path"] for e in assets["installer"]["files"] if e["path"].endswith(".iso")
    )
    work.mkdir(mode=0o700)
    subprocess.run(
        [
            str(Path(qemu).with_name("qemu-img")),
            "create",
            "-f",
            "qcow2",
            str(work / "scratch.qcow2"),
            "96G",
        ],
        check=True,
        capture_output=True,
    )
    configuration = {
        "checkpoint": str(checkpoint),
        "hypervisor": socket.gethostname(),
        "original_namespace": os.readlink("/proc/self/ns/net"),
        "qemu": qemu_arguments(work, checkpoint, qemu, iso),
        "scenario": "Replacement VM on surviving operator laptop; no production hypervisor",
        "scope": "Installer and isolation qualification; no restored platform service start",
        "memory_mib": 6144,
        "vcpu": 2,
        "scratch_gib": 96,
        "checkpoint_readonly": True,
        "host_store_shared": False,
        "host_devices_shared": False,
        "manifest_sha256": file_digest(checkpoint / "manifest.json"),
    }
    write_json(work / "configuration.json", configuration)


def inside(work: Path) -> None:
    configuration = json.loads((work / "configuration.json").read_text())
    state = boundary_state()
    require_boundary(state, configuration["original_namespace"])
    probes = {}
    for address, port in [
        ("10.0.30.20", 443),
        ("10.0.30.11", 6443),
        ("1.1.1.1", 443),
        ("2606:4700:4700::1111", 443),
    ]:
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        with socket.socket(family) as connection:
            connection.settimeout(2)
            result = connection.connect_ex((address, port))
        if result == 0:
            raise ValueError("Recovery hypervisor can reach a forbidden endpoint")
        probes[f"{address}:{port}"] = result
    state["negative_connection_results"] = probes
    write_json(work / "hypervisor-network.json", state)
    os.execv(configuration["qemu"][0], configuration["qemu"])


def launch(work: Path) -> None:
    if (work / "pid.json").exists():
        raise ValueError("Workspace already has a launch record")
    command = [
        "unshare",
        "--user",
        "--map-root-user",
        "--net",
        sys.executable,
        "-m",
        "scripts.recovery_host",
        "boundary",
        str(work),
    ]
    with (work / "qemu.log").open("wb") as log:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    write_json(work / "pid.json", {"pid": process.pid, "command": command})
    deadline = time.monotonic() + 10
    while not (work / "qmp.sock").exists():
        if process.poll() is not None:
            raise RuntimeError(
                f"Recovery boundary launch failed; see {work / 'qemu.log'}"
            )
        if time.monotonic() > deadline:
            raise TimeoutError("Recovery VM control socket did not become ready")
        time.sleep(0.05)
    print(json.dumps({"pid": process.pid, "paused": True, "workspace": str(work)}))


def qmp(work: Path, command: str) -> None:
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(10)
        connection.connect(str(work / "qmp.sock"))
        stream = connection.makefile("rwb", buffering=0)
        json.loads(stream.readline())
        for operation in ["qmp_capabilities", command]:
            stream.write(json.dumps({"execute": operation}).encode() + b"\n")
            while True:
                result = json.loads(stream.readline())
                if "error" in result:
                    raise RuntimeError(str(result["error"]))
                if "return" in result:
                    break


def console(work: Path, resume: bool) -> None:
    with (
        socket.socket(socket.AF_UNIX) as connection,
        (work / "console.log").open("ab", buffering=0) as log,
    ):
        connection.connect(str(work / "serial.sock"))
        old = termios.tcgetattr(sys.stdin) if sys.stdin.isatty() else None
        try:
            if old:
                tty.setraw(sys.stdin)
            if resume:
                qmp(work, "cont")
            while True:
                ready, _, _ = select.select([connection, sys.stdin], [], [])
                if connection in ready:
                    data = connection.recv(65536)
                    if not data:
                        break
                    log.write(data)
                    os.write(sys.stdout.fileno(), data)
                if sys.stdin in ready:
                    data = os.read(sys.stdin.fileno(), 4096)
                    if not data or b"\x1d" in data:
                        break
                    connection.sendall(data)
        finally:
            if old:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Boot a sealed recovery installer inside an isolated disposable VM"
    )
    parser.add_argument(
        "action", choices=["prepare", "start", "boundary", "console", "stop"]
    )
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--trusted-signers", type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    work = args.workspace.resolve()
    os.umask(0o077)
    if args.action == "prepare":
        if not args.checkpoint or not args.trusted_signers:
            parser.error("prepare requires checkpoint and independent trusted-signers")
        prepare(work, args.checkpoint.resolve(), args.trusted_signers.resolve())
    elif args.action == "start":
        launch(work)
    elif args.action == "boundary":
        inside(work)
    elif args.action == "console":
        console(work, args.resume)
    else:
        path = work / "qmp.sock"
        if not stat.S_ISSOCK(path.stat().st_mode):
            raise ValueError("Control endpoint is not a Unix socket")
        qmp(work, "quit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
