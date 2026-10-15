from __future__ import annotations

import socket
import socketserver
import tempfile
import threading
import unittest
from pathlib import Path

from scripts.recovery_guest import ADDRESS, NAME, DNSHandler, dns_question, dns_reply
from scripts.recovery_host import qemu_arguments, require_boundary


class RecoveryBoundaryTests(unittest.TestCase):
    def state(self) -> dict:
        return {
            "namespace": "net:[2]",
            "interfaces": ["lo"],
            "ipv4_routes": [],
            "ipv6_routes": [],
        }

    def test_host_namespace_is_never_accepted(self) -> None:
        with self.assertRaises(ValueError):
            require_boundary(self.state(), "net:[2]")

    def test_external_interfaces_and_routes_fail_closed(self) -> None:
        for key, value in [
            ("interfaces", ["lo", "eth0"]),
            ("ipv4_routes", ["default via 192.0.2.2"]),
            ("ipv6_routes", ["malformed"]),
        ]:
            with self.subTest(key=key):
                state = self.state()
                state[key] = value
                with self.assertRaises(ValueError):
                    require_boundary(state, "net:[1]")

    def test_kernel_ipv6_reject_defaults_are_not_forwarding_routes(self) -> None:
        state = self.state()
        state["ipv6_routes"] = [
            "0" * 32
            + " 00 "
            + "0" * 32
            + " 00 "
            + "0" * 32
            + " ffffffff 00000001 00000000 00200200 lo"
        ]
        require_boundary(state, "net:[1]")
        state["ipv6_routes"][0] = state["ipv6_routes"][0].replace(
            "00200200", "00000001"
        )
        with self.assertRaises(ValueError):
            require_boundary(state, "net:[1]")

    def test_guest_has_restricted_network_and_only_declared_media(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            checkpoint = work / "checkpoint"
            arguments = qemu_arguments(
                work, checkpoint, "/qemu", checkpoint / "installer.iso"
            )
            network = arguments[arguments.index("-netdev") + 1]
            self.assertIn("restrict=on", network)
            self.assertIn("ipv6=off", network)
            self.assertNotIn("hostfwd", network)
            media = arguments[arguments.index("-fsdev") + 1]
            self.assertIn("readonly=on", media)
            self.assertIn("path=" + str(checkpoint), media)
            self.assertNotIn("/dev/", " ".join(arguments))
            self.assertIn("-S", arguments)
            self.assertIn("unix:" + str(work / "qmp.sock"), " ".join(arguments))


class RecoveryDNSTests(unittest.TestCase):
    def test_only_qualification_name_is_answered(self) -> None:
        with socketserver.ThreadingUDPServer(("127.0.0.1", 0), DNSHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                    client.settimeout(2)
                    client.sendto(dns_question(NAME), server.server_address)
                    response, _ = client.recvfrom(4096)
                    self.assertEqual(response[3] & 15, 0)
                    self.assertEqual(response[-4:], socket.inet_aton(ADDRESS))
                    client.sendto(dns_question("example.org"), server.server_address)
                    response, _ = client.recvfrom(4096)
                    self.assertEqual(response[3] & 15, 3)
                    self.assertEqual(response[6:8], b"\x00\x00")
            finally:
                server.shutdown()
                thread.join()

    def test_compressed_and_truncated_questions_are_dropped(self) -> None:
        query = dns_question(NAME)
        for invalid in [
            query[:11],
            query[:-1],
            query[:12] + b"\xc0\x0c\x00\x01\x00\x01",
        ]:
            self.assertIsNone(dns_reply(invalid))


if __name__ == "__main__":
    unittest.main()
