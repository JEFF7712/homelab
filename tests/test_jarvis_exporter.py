from __future__ import annotations

import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_exporter_module() -> dict:
    docs = list(
        yaml.safe_load_all(
            (REPO_ROOT / "gitops" / "voice" / "exporter.yaml").read_text()
        )
    )
    code = next(
        doc["data"]["exporter.py"]
        for doc in docs
        if doc.get("kind") == "ConfigMap"
        and doc["metadata"]["name"] == "jarvis-exporter-code"
    )
    module: dict = {"__name__": "jarvis_exporter_under_test"}
    exec(compile(code, "exporter.py", "exec"), module)  # noqa: S102
    return module


EXPORTER = load_exporter_module()


class ExporterNetworkPolicyTest(unittest.TestCase):
    def test_home_assistant_egress_targets_host_entity_on_only_port_8123(self) -> None:
        docs = yaml.safe_load_all(
            (REPO_ROOT / "gitops" / "voice" / "network-policy.yaml").read_text()
        )
        policy = next(
            doc
            for doc in docs
            if doc.get("metadata", {}).get("name") == "allow-exporter-to-home-assistant"
        )

        self.assertEqual(policy["apiVersion"], "cilium.io/v2")
        self.assertEqual(policy["kind"], "CiliumNetworkPolicy")
        self.assertEqual(
            policy["spec"]["endpointSelector"]["matchLabels"]["app"],
            "jarvis-exporter",
        )
        self.assertEqual(policy["spec"]["egress"][0]["toEntities"], ["kube-apiserver"])
        self.assertEqual(
            policy["spec"]["egress"][0]["toPorts"][0]["ports"],
            [{"port": "8123", "protocol": "TCP"}],
        )


class ExporterRenderTest(unittest.TestCase):
    def test_counter_render(self) -> None:
        counter = EXPORTER["Counter"]("demo_total", "Demo.", ("satellite",))
        counter.labels("x").inc()
        counter.labels("x").inc(2.0)
        text = "\n".join(counter.render())
        self.assertIn('demo_total{satellite="x"} 3.0', text)

    def test_histogram_buckets(self) -> None:
        histogram = EXPORTER["Histogram"]("demo_seconds", "Demo.", ("satellite",))
        bound = histogram.labels("x")
        bound.observe(0.3)
        bound.observe(5.0)
        text = "\n".join(histogram.render())
        self.assertIn('demo_seconds_bucket{satellite="x",le="0.25"} 0', text)
        self.assertIn('demo_seconds_bucket{satellite="x",le="0.5"} 1', text)
        self.assertIn('demo_seconds_bucket{satellite="x",le="8.0"} 2', text)
        self.assertIn('demo_seconds_bucket{satellite="x",le="+Inf"} 2', text)
        self.assertIn('demo_seconds_count{satellite="x"} 2', text)

    def test_entity_ids_from(self) -> None:
        parse = EXPORTER["entity_ids_from"]
        self.assertEqual(parse({"entity_id": "light.a"}), ["light.a"])
        self.assertEqual(parse({"entity_id": ["light.a", 7]}), ["light.a"])
        self.assertEqual(parse({}), [])

    def test_connection_metrics_distinguish_ready_subscription(self) -> None:
        metrics = EXPORTER["Metrics"]("sat")
        metrics.mark_reconnect()
        metrics.mark_auth_failure()
        metrics.set_subscription_state("authenticated")
        metrics.set_connected(True)
        text = metrics.render()
        self.assertIn(
            'jarvis_exporter_subscription_state{state="authenticated"} 1', text
        )
        self.assertIn("jarvis_exporter_reconnects_total", text)
        self.assertIn("jarvis_exporter_auth_failures_total", text)


class TurnTrackerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.metrics = EXPORTER["Metrics"]("sat")
        self.tracker = EXPORTER["TurnTracker"](self.metrics, "sat")

    def drive(self, *states: str, start: float = 1000.0, step: float = 1.0) -> None:
        now = start
        for state in states:
            self.tracker.observe_state(
                state, now, lambda areas: areas[0] if areas else "unknown"
            )
            now += step

    def test_happy_path_records_all_stages(self) -> None:
        self.drive("listening", "processing", "responding", "idle")
        text = self.metrics.render()
        self.assertIn('jarvis_requests_total{satellite="sat"} 1.0', text)
        self.assertIn('jarvis_stt_latency_seconds_count{satellite="sat"} 1', text)
        self.assertIn('jarvis_llm_latency_seconds_count{satellite="sat"} 1', text)
        self.assertIn('jarvis_tts_latency_seconds_count{satellite="sat"} 1', text)
        self.assertIn('jarvis_requests_by_room_total{room="unknown"} 1.0', text)
        self.assertNotIn('jarvis_failed_requests_total{satellite="sat"', text)

    def test_abandoned_turn_counts_failed(self) -> None:
        self.drive("listening", "processing", "idle")
        text = self.metrics.render()
        self.assertIn("jarvis_requests_total", text)
        self.assertIn(
            'jarvis_failed_requests_total{satellite="sat",stage="processing"} 1.0', text
        )
        self.assertNotIn('jarvis_requests_total{satellite="sat"} 1.0', text)

    def test_listening_cancel_counts_aborted_not_failed(self) -> None:
        self.drive("listening", "idle")
        text = self.metrics.render()
        self.assertIn('jarvis_aborted_requests_total{satellite="sat"} 1.0', text)
        self.assertNotIn('jarvis_failed_requests_total{satellite="sat"', text)
        self.assertNotIn('jarvis_requests_total{satellite="sat"} 1.0', text)

    def test_concurrent_observe_and_render(self) -> None:
        import threading

        metrics = EXPORTER["Metrics"]("sat")
        errors: list[Exception] = []

        def hammer() -> None:
            try:
                for _ in range(200):
                    metrics.requests.labels("sat").inc()
                    metrics.stt.labels("sat").observe(0.1)
                    metrics.render()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30.0)
        self.assertEqual(errors, [])

    def test_tool_calls_only_count_while_processing(self) -> None:
        self.tracker.observe_state("listening", 1000.0, None)
        self.tracker.observe_service_call(["light.a"])
        self.tracker.observe_state("processing", 1001.0, None)
        self.tracker.observe_service_call(["light.a", "light.b"])
        text = self.metrics.render()
        self.assertIn('jarvis_tool_calls_total{satellite="sat"} 1.0', text)

    def test_room_from_first_area(self) -> None:
        areas = {"light.a": "kitchen", "light.b": "living_room"}
        self.tracker.observe_state("listening", 1000.0, None)
        self.tracker.observe_state("processing", 1001.0, None)
        self.tracker.observe_service_call(["light.b"])
        self.tracker.observe_state("responding", 1002.0, None)
        self.tracker.observe_state(
            "idle",
            1003.0,
            lambda ids: areas.get(ids[0], "unknown") if ids else "unknown",
        )
        text = self.metrics.render()
        self.assertIn('jarvis_requests_by_room_total{room="living_room"} 1.0', text)

    def test_fast_turn_records_fast_class(self) -> None:
        self.drive("listening", "processing", "responding", "idle")
        text = self.metrics.render()
        self.assertIn(
            'jarvis_turn_speed_class_total{satellite="sat",class="fast"} 1.0', text
        )

    def test_slow_turn_records_slow_class(self) -> None:
        self.drive("listening", "processing", "responding", "idle", step=3.0)
        text = self.metrics.render()
        self.assertIn(
            'jarvis_turn_speed_class_total{satellite="sat",class="slow"} 1.0', text
        )

    def test_speed_threshold_is_configurable(self) -> None:
        metrics = EXPORTER["Metrics"]("sat")
        tracker = EXPORTER["TurnTracker"](metrics, "sat", fast_max_seconds=10.0)
        now = 1000.0
        for state in ("listening", "processing", "responding", "idle"):
            tracker.observe_state(state, now, None)
            now += 3.0
        text = metrics.render()
        self.assertIn(
            'jarvis_turn_speed_class_total{satellite="sat",class="fast"} 1.0', text
        )

    def test_seed_suppresses_partial_turn(self) -> None:
        self.tracker.seed("processing")
        self.tracker.observe_state("responding", 1001.0, None)
        self.tracker.observe_state("idle", 1002.0, None)
        text = self.metrics.render()
        self.assertNotIn('jarvis_requests_total{satellite="sat"} 1.0', text)


class HandshakeTest(unittest.TestCase):
    def _recv_exact(self, sock, count: int) -> bytes:
        chunks = []
        while count > 0:
            chunk = sock.recv(count)
            if not chunk:
                raise RuntimeError("fake server: connection closed")
            chunks.append(chunk)
            count -= len(chunk)
        return b"".join(chunks)

    def _read_client_frame(self, sock) -> dict:
        import json

        header = self._recv_exact(sock, 2)
        length = header[1] & 0x7F
        if length == 126:
            import struct

            (length,) = struct.unpack("!H", self._recv_exact(sock, 2))
        mask = self._recv_exact(sock, 4)
        payload = self._recv_exact(sock, length)
        return json.loads(
            bytes(b ^ mask[i % 4] for i, b in enumerate(payload)).decode()
        )

    def _send_server_frame(self, sock, payload: bytes) -> None:
        sock.sendall(bytes([0x81, len(payload)]) + payload)

    def _fake_ha(self, sock, status_line: str) -> None:
        import base64
        import hashlib
        import json

        request = b""
        while b"\r\n\r\n" not in request:
            request += sock.recv(4096)
        key = next(
            line.split(b":", 1)[1].strip()
            for line in request.split(b"\r\n")
            if line.lower().startswith(b"sec-websocket-key:")
        )
        accept = base64.b64encode(
            hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest()
        )
        handshake = (
            status_line.encode()
            + b"\r\nUpgrade: websocket\r\nSec-WebSocket-Accept: "
            + accept
            + b"\r\n\r\n"
        )
        if " 101 " not in status_line:
            sock.sendall(handshake)
            return
        auth_required = json.dumps({"type": "auth_required"}).encode()
        frame = bytes([0x81, len(auth_required)]) + auth_required
        sock.sendall(handshake + frame)
        auth = self._read_client_frame(sock)
        assert auth.get("type") == "auth", auth
        self._send_server_frame(sock, json.dumps({"type": "auth_ok"}).encode())

    def _connect_through_pair(self, status_line: str):
        import socket
        import threading

        client, server = socket.socketpair()
        thread = threading.Thread(target=self._fake_ha, args=(server, status_line))
        thread.start()
        sock_class = EXPORTER["HomeAssistantSocket"]
        sock = sock_class.__new__(sock_class)
        sock._host = "fake"
        sock._port = 1
        sock._ssl = False
        sock._token = "token"
        sock._timeout = 5.0
        sock._sock = client
        sock._msg_id = 1
        import socket as socket_module

        real_create = socket_module.create_connection
        socket_module.create_connection = lambda *args, **kwargs: client
        try:
            sock.connect()
        finally:
            socket_module.create_connection = real_create
            thread.join(timeout=5.0)
            client.close()
            server.close()
        return sock

    def test_connect_consumes_http_handshake(self) -> None:
        self._connect_through_pair("HTTP/1.1 101 Switching Protocols")

    def test_connect_rejects_non_101_handshake(self) -> None:
        with self.assertRaises(RuntimeError):
            self._connect_through_pair("HTTP/1.1 400 Bad Request")

    def test_fragmented_json_with_ping_is_reassembled(self) -> None:
        import socket
        import threading

        client, server = socket.socketpair()
        sock_class = EXPORTER["HomeAssistantSocket"]
        sock = sock_class.__new__(sock_class)
        sock._sock = client
        sock._buf = b""
        sock._timeout = 5.0

        def send_frames() -> None:
            server.sendall(bytes([0x01, 5]) + b'{"typ')
            server.sendall(bytes([0x89, 1]) + b"?")
            server.sendall(bytes([0x80, 8]) + b'e":"ok"}')

        thread = threading.Thread(target=send_frames)
        thread.start()
        try:
            self.assertEqual(sock._read_message(), {"type": "ok"})
            pong = server.recv(32)
            self.assertEqual(pong[0] & 0x0F, 0xA)
            self.assertTrue(pong[1] & 0x80)
            mask = pong[2:6]
            payload = bytes(
                value ^ mask[index % 4] for index, value in enumerate(pong[6:7])
            )
            self.assertEqual(payload, b"?")
        finally:
            thread.join(timeout=5.0)
            client.close()
            server.close()

    def test_close_frame_is_acknowledged_and_idle_timeout_is_tolerated(self) -> None:
        import socket

        client, server = socket.socketpair()
        sock_class = EXPORTER["HomeAssistantSocket"]
        sock = sock_class.__new__(sock_class)
        sock._sock = client
        sock._buf = b""
        sock._timeout = 0.01
        server.sendall(bytes([0x88, 0x02]) + b"\x03\xe8")
        with self.assertRaises(RuntimeError):
            sock._read_message()
        response = server.recv(32)
        self.assertEqual(response[0] & 0x0F, 0x8)
        server.close()

        client, server = socket.socketpair()
        sock._sock = client
        sock._timeout = 0.01
        self.assertIsNone(sock.read_event(0.01))
        client.close()
        server.close()


if __name__ == "__main__":
    unittest.main()
