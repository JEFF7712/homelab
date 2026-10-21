#!/usr/bin/env python3
"""Prometheus exporter for NVIDIA GPU metrics, read through NVML."""

import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

HEADER = [
    "# HELP nvidia_smi_gpu_temp_celsius GPU temperature in Celsius",
    "# TYPE nvidia_smi_gpu_temp_celsius gauge",
    "# HELP nvidia_smi_utilization_gpu_ratio GPU utilization ratio (0 to 1)",
    "# TYPE nvidia_smi_utilization_gpu_ratio gauge",
    "# HELP nvidia_smi_utilization_memory_ratio GPU memory utilization ratio (0 to 1)",
    "# TYPE nvidia_smi_utilization_memory_ratio gauge",
    "# HELP nvidia_smi_memory_total_bytes Total GPU memory in bytes",
    "# TYPE nvidia_smi_memory_total_bytes gauge",
    "# HELP nvidia_smi_memory_used_bytes Used GPU memory in bytes",
    "# TYPE nvidia_smi_memory_used_bytes gauge",
    "# HELP nvidia_smi_memory_free_bytes Free GPU memory in bytes",
    "# TYPE nvidia_smi_memory_free_bytes gauge",
    "# HELP nvidia_smi_fan_speed_ratio GPU fan speed ratio (0 to 1)",
    "# TYPE nvidia_smi_fan_speed_ratio gauge",
]


def optional(nvml: Any, read: Any, *args: Any) -> float:
    """Passive-cooled cards report fan speed as NotSupported; export 0."""
    try:
        return float(read(*args))
    except nvml.NVMLError:
        return 0.0


def collect_metrics(nvml: Any = None) -> str:
    if nvml is None:
        import pynvml as nvml
    lines = list(HEADER)
    nvml.nvmlInit()
    try:
        for index in range(nvml.nvmlDeviceGetCount()):
            handle = nvml.nvmlDeviceGetHandleByIndex(index)
            name = nvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode()
            temperature = nvml.nvmlDeviceGetTemperature(
                handle, nvml.NVML_TEMPERATURE_GPU
            )
            rates = nvml.nvmlDeviceGetUtilizationRates(handle)
            memory = nvml.nvmlDeviceGetMemoryInfo(handle)
            fan = optional(nvml, nvml.nvmlDeviceGetFanSpeed, handle)
            labels = f'gpu="{index}",name="{name}"'
            lines += [
                f"nvidia_smi_gpu_temp_celsius{{{labels}}} {float(temperature)}",
                f"nvidia_smi_utilization_gpu_ratio{{{labels}}} {rates.gpu / 100:.4f}",
                f"nvidia_smi_utilization_memory_ratio{{{labels}}} {rates.memory / 100:.4f}",
                f"nvidia_smi_memory_total_bytes{{{labels}}} {memory.total:.0f}",
                f"nvidia_smi_memory_used_bytes{{{labels}}} {memory.used:.0f}",
                f"nvidia_smi_memory_free_bytes{{{labels}}} {memory.free:.0f}",
                f"nvidia_smi_fan_speed_ratio{{{labels}}} {fan / 100:.4f}",
            ]
    finally:
        nvml.nvmlShutdown()
    return "\n".join(lines) + "\n"


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path not in ("/metrics", "/"):
            self.send_response(404)
            self.end_headers()
            return
        try:
            content = collect_metrics().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:  # noqa: BLE001
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode("utf-8"))

    def log_message(self, format: str, *args: object) -> None:
        pass


def main() -> None:
    port = int(os.environ.get("PORT", "9835"))
    server = HTTPServer(("0.0.0.0", port), MetricsHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
