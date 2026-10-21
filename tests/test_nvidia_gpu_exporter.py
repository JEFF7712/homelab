import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "nvidia_gpu_exporter", ROOT / "flake/modules/nvidia_gpu_exporter.py"
)
assert spec and spec.loader
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class FakeNvml:
    NVML_TEMPERATURE_GPU = 0

    class NVMLError(Exception):
        pass

    def __init__(self) -> None:
        self.shutdown = False

    def nvmlInit(self) -> None:
        pass

    def nvmlShutdown(self) -> None:
        self.shutdown = True

    def nvmlDeviceGetCount(self) -> int:
        return 1

    def nvmlDeviceGetHandleByIndex(self, index: int) -> int:
        return index

    def nvmlDeviceGetName(self, handle: int) -> bytes:
        return b"NVIDIA T600"

    def nvmlDeviceGetTemperature(self, handle: int, sensor: int) -> int:
        return 41

    def nvmlDeviceGetUtilizationRates(self, handle: int) -> SimpleNamespace:
        return SimpleNamespace(gpu=37, memory=12)

    def nvmlDeviceGetMemoryInfo(self, handle: int) -> SimpleNamespace:
        return SimpleNamespace(total=4 << 30, used=1 << 30, free=3 << 30)

    def nvmlDeviceGetFanSpeed(self, handle: int) -> int:
        raise self.NVMLError("Not Supported")


class NvidiaGpuExporterTest(unittest.TestCase):
    def test_exports_the_nvidia_smi_metric_names_from_nvml(self) -> None:
        nvml = FakeNvml()
        lines = exporter.collect_metrics(nvml).splitlines()
        labels = 'gpu="0",name="NVIDIA T600"'
        for line in [
            f"nvidia_smi_gpu_temp_celsius{{{labels}}} 41.0",
            f"nvidia_smi_utilization_gpu_ratio{{{labels}}} 0.3700",
            f"nvidia_smi_utilization_memory_ratio{{{labels}}} 0.1200",
            f"nvidia_smi_memory_total_bytes{{{labels}}} 4294967296",
            f"nvidia_smi_memory_used_bytes{{{labels}}} 1073741824",
            f"nvidia_smi_fan_speed_ratio{{{labels}}} 0.0000",
        ]:
            self.assertIn(line, lines)
        self.assertTrue(nvml.shutdown)


if __name__ == "__main__":
    unittest.main()
