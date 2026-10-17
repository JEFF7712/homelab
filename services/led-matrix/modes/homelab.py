"""Four rotating views of live homelab Prometheus telemetry."""

from __future__ import annotations

import json
import math
import threading
import time
from collections import deque
from urllib.parse import urlencode
from urllib.request import urlopen

from PIL import Image, ImageDraw, ImageFont

from modes.base import ModeRenderer, RenderContext

QUERIES = {
    "K8S": {
        "nodes": 'kube_node_status_condition{condition="Ready",status="true"}',
        "pods": "sum by (node) (kube_pod_info)",
        "bad": 'sum by (node) (kube_pod_info * on(namespace,pod) group_left() (kube_pod_status_phase{phase=~"Pending|Failed|Unknown"} == 1))',
    },
    "CI/CD": {
        "ci": "repo_ci_state",
        "services": "repo_service_up",
        "age": "time() - repo_monitor_last_success_timestamp_seconds",
        "deploy_ready": "sum(kube_deployment_status_replicas_available)",
        "deploy_desired": "sum(kube_deployment_spec_replicas)",
    },
    "NETWORK": {
        "rx": 'sum by (device) (rate(node_network_receive_bytes_total{job="nas-01-node",device!~"lo|veth.*|docker.*|br.*"}[2m]))',
        "tx": 'sum by (device) (rate(node_network_transmit_bytes_total{job="nas-01-node",device!~"lo|veth.*|docker.*|br.*"}[2m]))',
        "packets": 'sum(rate(node_network_receive_packets_total{job="nas-01-node",device!~"lo|veth.*|docker.*|br.*"}[2m]) + rate(node_network_transmit_packets_total{job="nas-01-node",device!~"lo|veth.*|docker.*|br.*"}[2m]))',
    },
    "HEALTH": {
        "names": 'node_uname_info{job="node-exporter"}',
        "cpu": '100 * (1 - avg by (instance,host,job) (rate(node_cpu_seconds_total{job=~"nas-01-node|homelab-04-node|node-exporter",mode="idle"}[2m])))',
        "ram": '100 * (1 - (node_memory_MemAvailable_bytes{job=~"nas-01-node|homelab-04-node|node-exporter"} / node_memory_MemTotal_bytes{job=~"nas-01-node|homelab-04-node|node-exporter"}))',
        "temp": 'max by (instance,host,job) (node_hwmon_temp_celsius{job=~"nas-01-node|homelab-04-node|node-exporter"})',
        "uptime": 'time() - node_boot_time_seconds{job=~"nas-01-node|homelab-04-node|node-exporter"}',
        "gpu": "100 * nvidia_smi_utilization_gpu_ratio",
    },
}


class PrometheusSource:
    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/") + "/api/v1/query"
        self._lock = threading.Lock()
        self._requested: str | None = None
        self._last_request = 0.0
        self._view = ""
        self._values: dict[str, list[tuple[dict[str, str], float]]] = {}
        self._updated = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def snapshot(
        self, view: str
    ) -> tuple[dict[str, list[tuple[dict[str, str], float]]], bool]:
        with self._lock:
            self._requested = view
            self._last_request = time.monotonic()
            return (self._values if self._view == view else {}), (
                self._view == view and time.monotonic() - self._updated < 25
            )

    def _query(self, expression: str) -> list[tuple[dict[str, str], float]]:
        address = self.url + "?" + urlencode({"query": expression})
        with urlopen(address, timeout=2) as response:
            payload = json.load(response)
        if payload.get("status") != "success":
            raise ValueError("Prometheus query failed")
        result = []
        for item in payload["data"]["result"][:80]:
            value = float(item["value"][1])
            if math.isfinite(value):
                result.append((item["metric"], value))
        return result

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                requested = self._requested
                recent = time.monotonic() - self._last_request < 20
            if requested is None or not recent:
                self._stop.wait(1)
                continue
            values = {}
            for key, expression in QUERIES[requested].items():
                try:
                    values[key] = self._query(expression)
                except (OSError, ValueError, KeyError, TimeoutError):
                    values[key] = []
            with self._lock:
                self._view = requested
                self._values = values
                self._updated = time.monotonic() if any(values.values()) else 0
            self._stop.wait(5)


class HomelabRenderer(ModeRenderer):
    PERIOD = 12.0
    VIEWS = tuple(QUERIES)

    def __init__(self, source: PrometheusSource) -> None:
        self.source = source
        self.font = ImageFont.load_default(size=9)
        self.tiny = ImageFont.load_default(size=7)
        self.network_history: deque[float] = deque([0.0] * 30, maxlen=30)
        self.last_network_update = 0.0
        self.health_history: dict[tuple[str, str], deque[float]] = {}
        self.last_health_update = 0.0

    def render(self, ctx: RenderContext, size: int = 64) -> Image.Image:
        view = self.VIEWS[int(ctx.tick / self.PERIOD) % len(self.VIEWS)]
        values, fresh = self.source.snapshot(view)
        frame = Image.new("RGB", (size, size), (2, 6, 15))
        draw = ImageDraw.Draw(frame)
        draw.text((2, 1), view, font=self.font, fill=(95, 181, 255))
        draw.line((1, 12, 62, 12), fill=(24, 70, 116))
        if not fresh:
            draw.text((5, 27), "DATA OFFLINE", font=self.tiny, fill=(255, 95, 85))
            return frame
        if view == "K8S":
            self._cluster(draw, values, ctx.tick)
        elif view == "CI/CD":
            self._ci(draw, values, ctx.tick)
        elif view == "NETWORK":
            self._network(draw, values, ctx.tick)
        else:
            self._health(draw, values, ctx.tick)
        return frame

    def _cluster(self, draw: ImageDraw.ImageDraw, values: dict, tick: float) -> None:
        nodes = values.get("nodes", [])
        pods = {m.get("node", ""): n for m, n in values.get("pods", [])}
        bad = {m.get("node", ""): n for m, n in values.get("bad", [])}
        start = int(tick / 4) * 4 % len(nodes) if nodes else 0
        visible = (nodes + nodes)[start : start + min(4, len(nodes))]
        for i, (metric, ready) in enumerate(visible):
            name = metric.get("node", "?")
            x, y = 3 + i % 2 * 31, 17 + i // 2 * 18
            healthy = ready >= 1 and bad.get(name, 0) == 0
            color = (
                (70, 170, 255)
                if healthy
                else (255, 30, 35)
                if int(tick * 3) % 2
                else (55, 10, 12)
            )
            draw.rectangle((x, y, x + 5, y + 10), fill=color)
            for pod in range(min(int(pods.get(name, 0)), 18)):
                px, py = x + 8 + pod % 6 * 3, y + pod // 6 * 3
                draw.rectangle((px, py, px + 1, py + 1), fill=color)
        draw.text(
            (2, 55),
            f"{len(nodes)} NODES  {sum(pods.values()):.0f} PODS",
            font=self.tiny,
            fill=(130, 185, 230),
        )

    def _ci(self, draw: ImageDraw.ImageDraw, values: dict, tick: float) -> None:
        services = {
            metric.get("service", ""): value
            for metric, value in values.get("services", [])
        }
        age = values.get("age", [])
        if age:
            draw.text(
                (30, 1), f"{age[0][1] / 60:.0f}m", font=self.tiny, fill=(110, 155, 195)
            )
        for x, name in ((49, "forgejo"), (57, "woodpecker")):
            color = (40, 215, 145) if services.get(name) == 1 else (250, 70, 65)
            draw.ellipse((x, 3, x + 3, 6), fill=color)
        states: dict[str, str] = {}
        for metric, value in values.get("ci", []):
            if value == 1:
                states[metric.get("repository", "?")] = metric.get("state", "?")
        if not states:
            draw.text((3, 25), "CI UNAVAILABLE", font=self.tiny, fill=(255, 95, 85))
        all_states = sorted(states.items())
        start = int(tick / 4) * 4 % len(all_states) if all_states else 0
        visible = (all_states + all_states)[start : start + min(4, len(all_states))]
        for i, (name, state) in enumerate(visible):
            y = 16 + i * 9
            color = (
                (45, 218, 138)
                if state == "success"
                else (255, 70, 60)
                if state in {"failure", "error"}
                else (255, 190, 55)
            )
            draw.text(
                (2, y),
                name.rsplit("/", 1)[-1][:8],
                font=self.tiny,
                fill=(180, 205, 225),
            )
            draw.rectangle((38, y + 2, 61, y + 5), outline=color)
            if state == "success":
                draw.rectangle((39, y + 3, 60, y + 4), fill=color)
            elif state == "pending":
                x = 39 + int(tick * 12) % 17
                draw.rectangle((x, y + 3, x + 4, y + 4), fill=color)
            else:
                draw.rectangle((39, y + 3, 42, y + 4), fill=color)
        ready = sum(value for _, value in values.get("deploy_ready", []))
        desired = sum(value for _, value in values.get("deploy_desired", []))
        if desired:
            draw.text(
                (2, 55),
                f"PODS {ready:.0f}/{desired:.0f}",
                font=self.tiny,
                fill=(100, 180, 255),
            )
            width = min(18, int(18 * ready / desired))
            draw.rectangle((43, 57, 62, 60), outline=(40, 95, 145))
            draw.rectangle((44, 58, 44 + width, 59), fill=(45, 215, 145))
            if ready < desired:
                draw.point((44 + int(tick * 7) % 18, 58), fill=(255, 190, 50))

    def _network(self, draw: ImageDraw.ImageDraw, values: dict, tick: float) -> None:
        rx = sum(value for _, value in values.get("rx", []))
        tx = sum(value for _, value in values.get("tx", []))
        packets = sum(value for _, value in values.get("packets", []))
        if tick - self.last_network_update > 0.5:
            self.network_history.append(rx + tx)
            self.last_network_update = tick
        peak = max(max(self.network_history), 1)
        for i, value in enumerate(self.network_history):
            level = min(7, int(value / peak * 8))
            for row in range(8):
                color = (
                    (8, 30, 48)
                    if row >= level
                    else (25 + level * 12, 75 + level * 15, 160 + level * 10)
                )
                draw.rectangle(
                    (2 + i * 2, 15 + (7 - row) * 4, 3 + i * 2, 17 + (7 - row) * 4),
                    fill=color,
                )
        draw.text(
            (2, 49),
            f"R {rx / 1e6:.1f} T {tx / 1e6:.1f} MB/s",
            font=self.tiny,
            fill=(150, 205, 255),
        )
        draw.text(
            (2, 57), f"{packets:.0f} PACKETS/s", font=self.tiny, fill=(95, 165, 215)
        )

    def _health(self, draw: ImageDraw.ImageDraw, values: dict, tick: float) -> None:
        hosts: dict[str, dict[str, float]] = {}
        names = {
            metric.get("instance", ""): metric.get("nodename", "")
            for metric, _ in values.get("names", [])
        }
        for key, series in values.items():
            if key == "names":
                continue
            for metric, value in series:
                host = (
                    metric.get("host")
                    or metric.get("node")
                    or names.get(metric.get("instance", ""))
                    or metric.get("instance")
                    or metric.get("job", "?")
                )
                hosts.setdefault(host, {})[key] = value
        if not hosts:
            draw.text((3, 25), "HOSTS OFFLINE", font=self.tiny, fill=(255, 95, 85))
            return
        name = sorted(hosts)[int(tick / 4) % len(hosts)]
        data = hosts[name]
        draw.text((2, 15), name[:13], font=self.tiny, fill=(210, 230, 255))
        if tick - self.last_health_update > 1:
            for host, metrics in hosts.items():
                for key in ("cpu", "gpu", "ram", "temp"):
                    if key in metrics:
                        self.health_history.setdefault(
                            (host, key), deque(maxlen=20)
                        ).append(metrics[key])
            self.last_health_update = tick
        for i, (key, title) in enumerate(
            (("cpu", "CPU"), ("gpu", "GPU"), ("ram", "RAM"), ("temp", "TMP"))
        ):
            y = 25 + i * 8
            value = data.get(key)
            draw.text((2, y), title, font=self.tiny, fill=(135, 185, 225))
            if value is None:
                draw.text((24, y), "--", font=self.tiny, fill=(80, 95, 110))
                continue
            draw.text((23, y), f"{value:.0f}", font=self.tiny, fill=(225, 235, 245))
            history = list(self.health_history.get((name, key), ()))
            if len(history) > 1:
                low, high = min(history), max(history)
                span = max(high - low, 1)
                points = [
                    (43 + i, y + 6 - int((sample - low) / span * 5))
                    for i, sample in enumerate(history[-20:])
                ]
                draw.line(points, fill=(65, 165, 245), width=1)
        uptime = data.get("uptime")
        draw.text(
            (2, 57),
            f"UP {uptime / 86400:.0f}d" if uptime is not None else "UP --",
            font=self.tiny,
            fill=(125, 180, 215),
        )
