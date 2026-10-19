"""One-shot read-only diagnosis of a Flux-managed app.

`just diag <name>` takes a Flux Kustomization or a namespace and reports, in
one pass, what agents otherwise assemble from dozens of kubectl and ssh
calls: Flux state, workload readiness, unhealthy pods with log tails, recent
warning events, running image digests compared with the registry lock, and
the runbooks that cover it. It never mutates the cluster.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
Kubectl = Callable[[Sequence[str]], tuple[int, str]]


def kubectl(arguments: Sequence[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["kubectl", "--request-timeout=15s", *arguments],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return 124, str(error)
    return result.returncode, result.stdout if result.returncode == 0 else result.stderr


def condition(item: dict[str, Any], kind: str) -> dict[str, Any]:
    return next(
        (
            c
            for c in item.get("status", {}).get("conditions", [])
            if c.get("type") == kind
        ),
        {},
    )


def kustomization_summary(item: dict[str, Any]) -> dict[str, Any]:
    ready = condition(item, "Ready")
    namespaces = sorted(
        {
            entry["id"].split("_")[0]
            for entry in item.get("status", {}).get("inventory", {}).get("entries", [])
            if entry.get("id", "").split("_")[0]
        }
    )
    return {
        "name": item["metadata"]["name"],
        "ready": ready.get("status") == "True",
        "message": ready.get("message", ""),
        "revision": item.get("status", {}).get("lastAppliedRevision", ""),
        "path": item.get("spec", {}).get("path", ""),
        "namespaces": namespaces,
    }


def workload_summary(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        kind = item["kind"]
        status = item.get("status", {})
        if kind == "DaemonSet":
            desired = status.get("desiredNumberScheduled", 0)
            ready = status.get("numberReady", 0)
        else:
            desired = item.get("spec", {}).get("replicas", 1)
            ready = status.get("readyReplicas", 0)
        rows.append(
            {
                "workload": f"{kind}/{item['metadata']['name']}",
                "namespace": item["metadata"]["namespace"],
                "ready": f"{ready}/{desired}",
                "healthy": ready >= desired,
            }
        )
    return rows


def pod_problems(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    problems = []
    for pod in items:
        phase = pod.get("status", {}).get("phase", "")
        if phase == "Succeeded":
            continue
        for container in pod.get("status", {}).get("containerStatuses", []) or []:
            state = container.get("state", {})
            waiting = state.get("waiting", {})
            last = container.get("lastState", {}).get("terminated", {})
            if container.get("ready") and container.get("restartCount", 0) == 0:
                continue
            problems.append(
                {
                    "pod": pod["metadata"]["name"],
                    "namespace": pod["metadata"]["namespace"],
                    "container": container["name"],
                    "phase": phase,
                    "ready": bool(container.get("ready")),
                    "restarts": container.get("restartCount", 0),
                    "reason": waiting.get("reason")
                    or state.get("terminated", {}).get("reason", ""),
                    "last_exit": last.get("reason", ""),
                }
            )
    return problems


def running_images(items: list[dict[str, Any]]) -> dict[str, set[str]]:
    images: dict[str, set[str]] = {}
    for pod in items:
        for container in pod.get("status", {}).get("containerStatuses", []) or []:
            reference = container.get("image", "")
            image_id = container.get("imageID", "")
            digest = image_id.rsplit("@", 1)[-1] if "@" in image_id else ""
            if reference.startswith("registry.rupan.dev/") and digest:
                repository = reference.removeprefix("registry.rupan.dev/")
                repository = repository.split("@")[0].rsplit(":", 1)[0]
                images.setdefault(repository, set()).add(digest)
    return images


def digest_mismatches(
    images: dict[str, set[str]], lock: dict[str, Any]
) -> list[dict[str, str]]:
    locked: dict[str, set[str]] = {}
    for record in lock.get("images", []):
        locked.setdefault(record.get("destination_repository", ""), set()).add(
            record.get("digest", "")
        )
    return [
        {
            "repository": repository,
            "running": digest,
            "locked": ", ".join(sorted(locked.get(repository, set()))) or "none",
        }
        for repository, digests in sorted(images.items())
        for digest in sorted(digests)
        if digest not in locked.get(repository, set())
    ]


def runbooks(root: Path, name: str) -> list[str]:
    matches = []
    for path in sorted((root / "docs/runbooks").glob("*.md")):
        text = path.read_text(encoding="utf-8").lower()
        if (
            name.lower() in path.stem
            or f"/{name.lower()}" in text
            or f" {name.lower()} " in text
        ):
            matches.append(path.relative_to(root).as_posix())
    return matches[:5]


def diagnose(name: str, run: Kubectl = kubectl, root: Path = ROOT) -> dict[str, Any]:
    report: dict[str, Any] = {"target": name, "errors": []}
    code, output = run(
        ["-n", "flux-system", "get", "kustomization", name, "-o", "json"]
    )
    if code == 0:
        flux = kustomization_summary(json.loads(output))
        report["flux"] = flux
        namespaces = flux["namespaces"] or [name]
    else:
        report["flux"] = None
        namespaces = [name]
    report["namespaces"] = namespaces
    workloads, problems, warnings = [], [], []
    images: dict[str, set[str]] = {}
    for namespace in namespaces:
        code, output = run(["-n", namespace, "get", "deploy,sts,ds", "-o", "json"])
        if code == 0:
            workloads.extend(workload_summary(json.loads(output)["items"]))
        else:
            report["errors"].append(output.strip()[-200:])
            continue
        code, output = run(["-n", namespace, "get", "pods", "-o", "json"])
        if code == 0:
            pods = json.loads(output)["items"]
            problems.extend(pod_problems(pods))
            for repository, digests in running_images(pods).items():
                images.setdefault(repository, set()).update(digests)
        code, output = run(
            [
                "-n",
                namespace,
                "get",
                "events",
                "--field-selector",
                "type=Warning",
                "-o",
                "json",
            ]
        )
        if code == 0:
            events = sorted(
                json.loads(output)["items"],
                key=lambda event: (
                    event.get("lastTimestamp") or event.get("eventTime") or ""
                ),
            )[-8:]
            warnings.extend(
                {
                    "namespace": namespace,
                    "object": f"{event['involvedObject'].get('kind')}/{event['involvedObject'].get('name')}",
                    "reason": event.get("reason", ""),
                    "message": event.get("message", "")[:240],
                    "count": event.get("count", 1),
                }
                for event in events
            )
    for problem in problems[:4]:
        arguments = [
            "-n",
            problem["namespace"],
            "logs",
            problem["pod"],
            "-c",
            problem["container"],
            "--tail=20",
        ]
        if problem["restarts"] and not problem["ready"]:
            arguments.append("--previous")
        code, output = run(arguments)
        problem["log_tail"] = output.strip().splitlines()[-20:] if output else []
    lock = json.loads((root / "registry/images.lock.json").read_text())
    report.update(
        workloads=workloads,
        problems=problems,
        warnings=warnings,
        digest_mismatches=digest_mismatches(images, lock),
        runbooks=runbooks(root, name),
    )
    report["healthy"] = (
        (report["flux"] is None or report["flux"]["ready"])
        and all(row["healthy"] for row in workloads)
        and not [p for p in problems if not p["ready"]]
        and not report["digest_mismatches"]
    )
    return report


def render(report: dict[str, Any]) -> str:
    lines = [
        f"diag {report['target']}: {'healthy' if report['healthy'] else 'UNHEALTHY'}"
    ]
    flux = report["flux"]
    if flux:
        state = "ready" if flux["ready"] else "NOT READY"
        lines.append(
            f"flux {flux['name']} ({flux['path']}): {state} at {flux['revision']}"
        )
        if not flux["ready"]:
            lines.append(f"  {flux['message']}")
    else:
        lines.append("flux: no Kustomization of that name; treated as a namespace")
    for row in report["workloads"]:
        mark = "" if row["healthy"] else "  <-"
        lines.append(f"{row['namespace']}/{row['workload']}: {row['ready']}{mark}")
    for problem in report["problems"]:
        lines.append(
            f"pod {problem['namespace']}/{problem['pod']} [{problem['container']}]: "
            f"ready={problem['ready']} restarts={problem['restarts']} "
            f"{problem['reason']} {problem['last_exit']}".rstrip()
        )
        lines.extend("    | " + line for line in problem.get("log_tail", []))
    for event in report["warnings"]:
        lines.append(
            f"warning {event['namespace']} {event['object']} {event['reason']} x{event['count']}: {event['message']}"
        )
    for mismatch in report["digest_mismatches"]:
        lines.append(
            f"image {mismatch['repository']} runs {mismatch['running'][:19]} but the lock has {mismatch['locked'][:80]}"
        )
    if report["runbooks"]:
        lines.append("runbooks: " + ", ".join(report["runbooks"]))
    lines.extend(f"error: {error}" for error in report["errors"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="just diag")
    parser.add_argument("name", help="Flux Kustomization or namespace")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = diagnose(args.name)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    sys.exit(main())
