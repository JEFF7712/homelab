from __future__ import annotations

import argparse
import datetime
import errno
import hashlib
import http.server
import ipaddress
import json
import os
import socket
import socketserver
import ssl
import stat
import struct
import subprocess
import tempfile
import threading
from pathlib import Path

NAME = "qualification.recovery.test"
ADDRESS = "192.0.2.15"


def dns_question(name: str, identifier: int = 7712) -> bytes:
    labels = b"".join(
        bytes([len(label)]) + label.encode("ascii") for label in name.split(".")
    )
    return (
        struct.pack("!6H", identifier, 0x100, 1, 0, 0, 0)
        + labels
        + b"\0"
        + struct.pack("!HH", 1, 1)
    )


def dns_reply(query: bytes) -> bytes | None:
    if len(query) < 17 or query[2] & 0x80 or struct.unpack("!H", query[4:6])[0] != 1:
        return None
    offset = 12
    labels = []
    while offset < len(query):
        length = query[offset]
        offset += 1
        if not length:
            break
        if length > 63 or offset + length > len(query):
            return None
        labels.append(query[offset : offset + length].lower())
        offset += length
    if offset + 4 != len(query):
        return None
    known = b".".join(labels) == NAME.encode()
    question_type, question_class = struct.unpack("!HH", query[offset:])
    answer = known and question_type == 1 and question_class == 1
    header = query[:2] + struct.pack(
        "!5H", 0x8400 if known else 0x8403, 1, int(answer), 0, 0
    )
    result = header + query[12:]
    if answer:
        result += (
            b"\xc0\x0c"
            + struct.pack("!HHIH", 1, 1, 0, 4)
            + ipaddress.IPv4Address(ADDRESS).packed
        )
    return result


class DNSHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        query, connection = self.request
        response = dns_reply(query)
        if response is not None:
            connection.sendto(response, self.client_address)


class HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"recovery-host-qualified\n"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def run(*arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        arguments, check=False, capture_output=True, text=True, timeout=120
    )
    if check and result.returncode:
        raise RuntimeError(
            f"{arguments[0]} exited {result.returncode}: {result.stderr[-2000:]}"
        )
    return result.stdout.strip()


def negative_connections() -> dict:
    results = {}
    for address, port in [
        ("10.0.30.20", 443),
        ("10.0.30.11", 6443),
        ("10.42.0.1", 80),
        ("10.43.0.1", 443),
        ("1.1.1.1", 443),
        ("192.0.2.2", 22),
        ("192.0.2.3", 443),
        ("2606:4700:4700::1111", 443),
    ]:
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        with socket.socket(family) as connection:
            connection.settimeout(1)
            try:
                result = connection.connect_ex((address, port))
            except OSError as error:
                result = error.errno or errno.ETIMEDOUT
        if result == 0:
            raise RuntimeError(
                f"Forbidden guest connection succeeded: {address}:{port}"
            )
        results[f"{address}:{port}"] = result
    for address in ["1.1.1.1", "192.0.2.2", "192.0.2.3"]:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            connection.settimeout(2)
            connection.sendto(dns_question("example.org"), (address, 53))
            try:
                connection.recvfrom(4096)
            except OSError as error:
                results[f"dns_udp_{address}"] = {
                    "response": False,
                    "errno": error.errno,
                }
            else:
                raise RuntimeError("Public or synthetic gateway DNS endpoint responded")
    return results


def local_dns_tls(scratch: Path, openssl: str) -> dict:
    with tempfile.TemporaryDirectory(
        prefix="qualification-tls-", dir=scratch
    ) as directory:
        root = Path(directory)
        ca, ca_key, cert, key = [
            str(root / name)
            for name in ["ca.crt", "ca.key", "server.crt", "server.key"]
        ]
        request = str(root / "server.csr")
        run(
            openssl,
            "req",
            "-x509",
            "-newkey",
            "ed25519",
            "-nodes",
            "-keyout",
            ca_key,
            "-out",
            ca,
            "-days",
            "1",
            "-subj",
            "/CN=Recovery-Qualification-CA",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
        )
        run(
            openssl,
            "req",
            "-new",
            "-newkey",
            "ed25519",
            "-nodes",
            "-keyout",
            key,
            "-out",
            request,
            "-subj",
            "/CN=" + NAME,
        )
        extensions = root / "extensions"
        extensions.write_text(
            "subjectAltName=DNS:"
            + NAME
            + "\nextendedKeyUsage=serverAuth\nbasicConstraints=critical,CA:FALSE\n"
        )
        run(
            openssl,
            "x509",
            "-req",
            "-in",
            request,
            "-CA",
            ca,
            "-CAkey",
            ca_key,
            "-CAcreateserial",
            "-out",
            cert,
            "-days",
            "1",
            "-extfile",
            str(extensions),
        )
        server = http.server.ThreadingHTTPServer((ADDRESS, 8443), HealthHandler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        dns = socketserver.ThreadingUDPServer((ADDRESS, 53), DNSHandler)
        for service in [server, dns]:
            threading.Thread(target=service.serve_forever, daemon=True).start()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
                connection.settimeout(2)
                connection.sendto(dns_question(NAME), (ADDRESS, 53))
                response, _ = connection.recvfrom(4096)
                if response[:2] != dns_question(NAME)[:2] or response[
                    -4:
                ] != socket.inet_aton(ADDRESS):
                    raise RuntimeError("Local authoritative DNS failed")
                connection.sendto(dns_question("example.org"), (ADDRESS, 53))
                response, _ = connection.recvfrom(4096)
                if response[3] & 15 != 3:
                    raise RuntimeError("Drill DNS must refuse unknown external names")
            trusted = ssl.create_default_context(cafile=ca)
            with (
                socket.create_connection((ADDRESS, 8443), timeout=3) as raw,
                trusted.wrap_socket(raw, server_hostname=NAME) as client,
            ):
                client.sendall(
                    b"GET / HTTP/1.0\r\nHost: " + NAME.encode() + b"\r\n\r\n"
                )
                chunks = []
                while chunk := client.recv(4096):
                    chunks.append(chunk)
                if b"recovery-host-qualified" not in b"".join(chunks):
                    raise RuntimeError("Trusted HTTPS probe failed")
            for verification_context, name in [
                (ssl.create_default_context(), NAME),
                (trusted, "wrong.recovery.test"),
            ]:
                try:
                    with (
                        socket.create_connection((ADDRESS, 8443), timeout=3) as raw,
                        verification_context.wrap_socket(raw, server_hostname=name),
                    ):
                        raise RuntimeError("Invalid TLS trust or hostname was accepted")
                except ssl.SSLCertVerificationError:
                    continue
            return {
                "dns_name": NAME,
                "address": ADDRESS,
                "authoritative_dns_passed": True,
                "external_dns_name_refused": True,
                "trusted_https_passed": True,
                "unknown_ca_rejected": True,
                "wrong_hostname_rejected": True,
                "openssl": openssl,
                "ca_sha256": hashlib.sha256(Path(ca).read_bytes()).hexdigest(),
                "ca_scope": "Ephemeral drill-only CA, no production keys",
            }
        finally:
            for service in [server, dns]:
                service.shutdown()
                service.server_close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--media", type=Path, default=Path("/recovery-media"))
    parser.add_argument("--scratch", type=Path, default=Path("/scratch"))
    args = parser.parse_args()
    os.umask(0o077)
    media, scratch = args.media, args.scratch
    if (scratch / "import.exit").read_text().strip() != "0":
        raise RuntimeError("Cold-store NAR import did not pass")
    tools = json.loads((media / "receipts/export-tools.json").read_text())
    store = "local?root=" + str(scratch / "retained-root")
    paths = run("nix-store", "--store", store, "-qR", *tools["roots"]).splitlines()
    if len(paths) != tools["store_paths"]:
        raise RuntimeError("Retained closure reference count differs")
    run("nix-store", "--store", store, "--check-validity", *paths)
    run("nix-store", "--store", store, "--verify", "--check-contents")
    device = scratch / "retained-root/dev/null"
    device.parent.mkdir(exist_ok=True)
    if not device.exists():
        os.mknod(device, stat.S_IFCHR | 0o666, os.makedev(1, 3))
    metadata = device.stat()
    if not stat.S_ISCHR(metadata.st_mode) or metadata.st_rdev != os.makedev(1, 3):
        raise RuntimeError(
            "Chroot /dev/null is not the expected guest character device"
        )
    proc = scratch / "retained-root/proc"
    proc.mkdir(exist_ok=True)
    if proc.is_mount():
        raise RuntimeError("Unexpected pre-existing mount in the cold target")
    run("mount", "-t", "proc", "-o", "nosuid,nodev,noexec", "proc", str(proc))
    try:
        versions = {
            name: run(
                "chroot",
                str(scratch / "retained-root"),
                tools["entrypoints"][name],
                "--version",
            )
            for name in ["python3", "git", "age", "sops", "nix-store", "podman"]
        }
    finally:
        run("umount", str(proc))
    try:
        descriptor = os.open(media / "credentials/recipient.txt", os.O_WRONLY)
    except OSError as error:
        if error.errno != errno.EROFS:
            raise
    else:
        os.close(descriptor)
        raise RuntimeError("Checkpoint export was writable")
    original_routes = run("ip", "route", "show", "default")
    if original_routes:
        raise RuntimeError("Unexpected pre-existing guest default route")
    interface = json.loads(run("ip", "-j", "route", "show", "192.0.2.0/24"))[0]["dev"]
    run("ip", "route", "add", "default", "via", "192.0.2.2", "dev", interface)
    run("ip", "-6", "addr", "add", "2001:db8:7712::15/64", "dev", interface, "nodad")
    run("ip", "-6", "route", "add", "default", "dev", interface)
    try:
        denied = negative_connections()
        openssl = sorted(Path("/nix/store").glob("*openssl-*-bin/bin/openssl"))
        if not openssl:
            raise RuntimeError("Installer does not provide OpenSSL for the drill CA")
        positive = local_dns_tls(scratch, str(openssl[0]))
        report = {
            "checked_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "kernel": os.uname().release,
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "system_profile": os.path.realpath("/run/current-system"),
            "failed_units": run("systemctl", "--failed", "--no-legend", "--plain"),
            "disks": json.loads(
                run("lsblk", "--json", "-o", "NAME,SIZE,TYPE,MODEL,MOUNTPOINTS")
            ),
            "guest_output_policy": run("iptables", "-S", "OUTPUT"),
            "negative_connections_with_added_default_routes": denied,
            "private_dns_tls": positive,
            "media_readonly_write_open_rejected": True,
            "store": store,
            "imported_reference_paths": len(paths),
            "all_imported_store_contents_verified": True,
            "tool_versions_from_chroot": versions,
            "host_global_store_access_from_chroot": False,
            "iso_contains_some_same_tool_paths": True,
            "full_platform_restore": False,
            "probe_source_sha256": hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest(),
        }
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print("RECOVERY_QUALIFICATION_PASSED")
    finally:
        run("ip", "route", "del", "default", "via", "192.0.2.2", "dev", interface)
        run("ip", "-6", "route", "del", "default", "dev", interface)
        run("ip", "-6", "addr", "del", "2001:db8:7712::15/64", "dev", interface)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
