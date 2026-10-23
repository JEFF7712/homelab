from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

Runner = Callable[..., subprocess.CompletedProcess[str]]


def configured_targets(root: Path) -> dict[str, Any]:
    fleet = json.loads((root / "inventory/fleet.json").read_text(encoding="utf-8"))
    ci = (root / ".gitlab-ci.yml").read_text(encoding="utf-8")
    opnsense = re.search(r'OPNSENSE_URI:\s*"([^"]+)"', ci)
    return {
        "cluster_api": fleet["cluster_api"],
        "cluster_nodes": fleet["cluster_nodes"],
        "opnsense": opnsense.group(1) if opnsense else None,
        "kubeconfig": "$KUBECONFIG or kubectl default resolution",
        "opnsense_ca_variable": "OPNSENSE_CA_FILE",
        "opnsense_credential_variables": [
            "OPNSENSE_READONLY_API_KEY",
            "OPNSENSE_READONLY_API_SECRET",
        ],
    }


def _run(runner: Runner, argv: list[str], timeout: float) -> dict[str, Any]:
    try:
        result = runner(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=max(0.01, timeout),
        )
    except (subprocess.TimeoutExpired, TimeoutError):
        return {"status": "failed", "command": argv, "detail": "timeout"}
    except OSError as error:
        return {"status": "unavailable", "command": argv, "detail": str(error)}
    return {
        "status": "pass" if result.returncode == 0 else "failed",
        "command": argv,
        "exit_code": result.returncode,
        "detail": (result.stdout + result.stderr)[-8192:],
    }


def status_payload(
    root: Path,
    target: str,
    *,
    total_timeout: float = 30.0,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    started = time.monotonic()
    targets = configured_targets(root)
    probes = []
    if target == "cluster":
        if runner is subprocess.run and not shutil.which("kubectl"):
            probes.append({"status": "unavailable", "detail": "kubectl is missing"})
        else:
            for name, argv in (
                (
                    "nodes",
                    ["kubectl", "--request-timeout=8s", "get", "nodes", "-o", "wide"],
                ),
                (
                    "flux",
                    [
                        "kubectl",
                        "--request-timeout=8s",
                        "get",
                        "kustomizations,helmreleases",
                        "-A",
                    ],
                ),
                (
                    "workloads",
                    [
                        "kubectl",
                        "--request-timeout=8s",
                        "get",
                        "pods",
                        "-A",
                        "--field-selector=status.phase!=Running,status.phase!=Succeeded",
                    ],
                ),
            ):
                remaining = total_timeout - (time.monotonic() - started)
                if remaining <= 0:
                    probes.append({"status": "failed", "detail": "total timeout"})
                    break
                probe = _run(runner, argv, min(8.0, remaining))
                probe["name"] = name
                probes.append(probe)
    else:
        for name, address in targets["cluster_nodes"].items():
            remaining = total_timeout - (time.monotonic() - started)
            if remaining <= 0:
                probes.append(
                    {"name": name, "status": "failed", "detail": "total timeout"}
                )
                break
            probe = _run(
                runner, ["ping", "-c", "1", "-W", "2", address], min(3.0, remaining)
            )
            probe["name"] = name
            probes.append(probe)
        remaining = total_timeout - (time.monotonic() - started)
        if remaining <= 0:
            probes.append(
                {"name": "bgp", "status": "failed", "detail": "total timeout"}
            )
        elif not targets["opnsense"]:
            probes.append(
                {
                    "name": "bgp",
                    "status": "unavailable",
                    "detail": "OPNsense target is not configured",
                }
            )
        else:
            probe = _run(
                runner,
                [
                    sys.executable,
                    "-m",
                    "scripts.agent.opnsense_status",
                    "--root",
                    str(root),
                    "--url",
                    targets["opnsense"],
                ],
                remaining,
            )
            try:
                observation = json.loads(probe["detail"])
                if not isinstance(observation, dict) or observation.get(
                    "status"
                ) not in {"pass", "failed", "unavailable"}:
                    raise ValueError("invalid diagnostic result")
                probe.update(observation)
            except (ValueError, TypeError):
                if probe["status"] == "pass":
                    probe.update(
                        status="failed", detail="invalid OPNsense diagnostic result"
                    )
            probe["name"] = "bgp"
            probes.append(probe)
    complete = probes and all(item["status"] == "pass" for item in probes)
    return {
        "schema_version": 1,
        "command": "status",
        "target": target,
        "timestamp": datetime.now(UTC).isoformat(),
        "selected_targets": targets,
        "status": "pass" if complete else "incomplete",
        "probes": probes,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
