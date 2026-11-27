"""Render Flux-owned, scratch-only Jobs for an exact PostgreSQL client upgrade."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from .chart_advance import kubectl_json

REPOSITORY = "upstream/docker.io/library/postgres"
NAMESPACE = "backups"
SERIES = {
    "16": (("db-dump.yaml", "immich"),),
    "17": (
        ("home-assistant-db-dump.yaml", "home-assistant"),
        ("umami-db-dump.yaml", "umami"),
    ),
}
ROOT = Path("gitops/backups")
DRILL = ROOT / "restore-drill.yaml"


def allowed(path: str, document: dict) -> bool:
    names = {
        (ROOT / filename).as_posix(): f"{dataset}-db-dump"
        for sources in SERIES.values()
        for filename, dataset in sources
    }
    names[DRILL.as_posix()] = "restore-drill"
    return (
        document.get("kind") == "CronJob"
        and document["metadata"].get("namespace") == NAMESPACE
        and names.get(path) == document["metadata"].get("name")
    )


def load_template(root: Path, path: Path, name: str) -> dict:
    for cron in yaml.safe_load_all((root / path).read_text()):
        if (
            isinstance(cron, dict)
            and allowed(path.as_posix(), cron)
            and cron["metadata"]["name"] == name
        ):
            return cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    raise ValueError(f"Missing backup CronJob {name} at {path}")


def render_jobs(root: Path, series: str, image: str) -> list[dict]:
    drill = load_template(root, DRILL, "restore-drill")
    script = (root / ROOT / "restore-drill/verify-dump.sh").read_bytes()
    jobs = []
    for filename, dataset in SERIES[series]:
        dump_spec = load_template(root, ROOT / filename, f"{dataset}-db-dump")
        dump = copy.deepcopy(
            next(c for c in dump_spec["containers"] if c["name"] == "pg-dump")
        )
        dump["image"] = image
        dump["command"] = [
            "/bin/sh",
            "-ec",
            "trap 'rm -f /dumps/check.dump' EXIT\n"
            "pg_dump --format=custom --file=/dumps/check.dump\ngzip /dumps/check.dump",
        ]
        verifier = "verify-immich" if series == "16" else "verify-postgres"
        restore = copy.deepcopy(
            next(c for c in drill["initContainers"] if c["name"] == verifier)
        )
        if series == "17":
            restore["image"] = image
        restore["volumeMounts"].append({"name": "dumps", "mountPath": "/dumps"})
        restore["command"] = [
            "/bin/sh",
            "-ec",
            "trap 'rm -f /dumps/check.dump.gz' EXIT\n"
            "mkdir -p /work/results\n"
            f"sh /scripts/verify-dump.sh {dataset}-db /dumps/check.dump.gz /work/results\n"
            f'grep -q "^pass:" /work/results/{dataset}-db',
        ]
        pod = {
            "automountServiceAccountToken": False,
            "restartPolicy": "Never",
            "securityContext": {**dump_spec["securityContext"], "fsGroup": 70},
            "initContainers": [dump],
            "containers": [restore],
            "volumes": [
                {"name": "dumps", "emptyDir": {"sizeLimit": "4Gi"}},
                {"name": "work", "emptyDir": {"sizeLimit": "8Gi"}},
                {"name": "tmp", "emptyDir": {}},
                next(v for v in drill["volumes"] if v["name"] == "scripts"),
            ],
        }
        fingerprint = hashlib.sha256(
            json.dumps(pod, sort_keys=True).encode() + script
        ).hexdigest()[:16]
        name = f"pg-client-{dataset}-{fingerprint}"
        jobs.append(
            {
                "apiVersion": "batch/v1",
                "kind": "Job",
                "metadata": {"name": name, "namespace": NAMESPACE},
                "spec": {
                    "activeDeadlineSeconds": 1200,
                    "backoffLimit": 0,
                    "template": {"spec": pod},
                },
            }
        )
    return jobs


def prepare(root: Path, series: str, digest: str) -> tuple[str, list[str]]:
    """Return ready, waiting, failed, or the paths of a new canary commit."""
    image = f"registry.rupan.dev/{REPOSITORY}@{digest}"
    jobs = render_jobs(root, series, image)
    filename = f"postgres-client-{series}-canary.yaml"
    path = ROOT / filename
    text = "# yamllint disable rule:line-length\n" + "\n".join(
        json.dumps(job, indent=2) + "\n" + ("---" if i < len(jobs) - 1 else "")
        for i, job in enumerate(jobs)
    )
    if not (root / path).exists() or (root / path).read_text() != text:
        kustomization = ROOT / "kustomization.yaml"
        manifest = root / kustomization
        content = manifest.read_text()
        resource = f"  - {filename}\n"
        paths = [path.as_posix()]
        if resource not in content:
            manifest.write_text(
                content.replace("resources:\n", "resources:\n" + resource, 1)
            )
            paths.append(kustomization.as_posix())
        (root / path).write_text(text)
        return "stage", paths

    ready = True
    for job in jobs:
        result: Any = kubectl_json(
            "-n", NAMESPACE, "get", "job", job["metadata"]["name"]
        )
        conditions = ((result or {}).get("status") or {}).get("conditions", [])
        terminal = {c["type"] for c in conditions if c.get("status") == "True"}
        if "Failed" in terminal:
            return "failed", []
        ready = ready and "Complete" in terminal
    return ("ready" if ready else "waiting"), []


def clear(root: Path, series: str) -> list[str]:
    """Prune the scratch Jobs in the commit that adopts their proven image."""
    filename = f"postgres-client-{series}-canary.yaml"
    path = ROOT / filename
    kustomization = ROOT / "kustomization.yaml"
    manifest = root / kustomization
    manifest.write_text(manifest.read_text().replace(f"  - {filename}\n", ""))
    (root / path).unlink()
    return [path.as_posix(), kustomization.as_posix()]
