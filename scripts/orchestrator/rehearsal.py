"""Credential-free rehearsal and gate 4 inputs: `python -m scripts.orchestrator.rehearsal`.

Subcommands print Kubernetes YAML for the operator workflow in
docs/runbooks/orchestrator-rehearsal.md:

- `render`: the manifests for config/orchestrator/rehearsal.json with the
  digests pinned in config/orchestrator/images.json;
- `secrets`: the controller and worker Secrets with random placeholder
  values (no credential is real, so the controller takes no task that needs
  the forge, ntfy or a model);
- `probe`: a pod with a worker's identity that runs network_probe;
- `admission`: one worker Job that must be admitted and one variant per
  rule the admission policy enforces, each labelled with the rule it breaks.
"""

from __future__ import annotations

import argparse
import copy
import json
import secrets
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scripts.orchestrator.jobs import JobSpec, KubernetesJobConfig, render_job
from scripts.orchestrator.manifests import (
    NAMESPACE,
    WORKER,
    WORKER_SECRET,
    DeployParams,
    IntakeParams,
    render,
)

ROOT = Path(__file__).resolve().parents[2]
PARAMS = ROOT / "config/orchestrator/rehearsal.json"
IMAGES = ROOT / "config/orchestrator/images.json"
CONTROLLER_SECRET_KEYS = (
    "forge-publisher-token",
    "forge-controller-token",
    "ntfy-token",
    "client-token",
    "alertmanager-intake-token",
    "sentinel-intake-token",
    "woodpecker-token",
)
ATTEMPT = "att_0123456789abcdef"


def params() -> DeployParams:
    raw = json.loads(PARAMS.read_text())
    images = json.loads(IMAGES.read_text())
    raw["controller_image"] = images["controller"]
    raw["worker_image"] = images["worker"]
    raw["required_contexts"] = tuple(raw["required_contexts"])
    raw["api_endpoints"] = tuple(tuple(item) for item in raw["api_endpoints"])
    raw["intake"] = IntakeParams(**raw["intake"])
    return DeployParams(**raw)


def placeholder_secrets(deploy: DeployParams) -> list[dict[str, Any]]:
    def secret(name: str, keys: tuple[str, ...]) -> dict[str, Any]:
        return {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": name, "namespace": NAMESPACE},
            "type": "Opaque",
            "stringData": {key: "rehearsal-" + secrets.token_hex(16) for key in keys},
        }

    return [
        secret(deploy.secret_name, CONTROLLER_SECRET_KEYS),
        secret(WORKER_SECRET, ("claude-oauth-token",)),
    ]


def probe_pod(deploy: DeployParams) -> dict[str, Any]:
    """A pod shaped like a worker, which runs the network probe once."""
    _, job = worker_job(deploy)
    pod = copy.deepcopy(job["spec"]["template"]["spec"])
    container = pod["containers"][0]
    container["args"] = ["python", "-m", "scripts.orchestrator.network_probe"]
    container["volumeMounts"] = [
        m for m in container["volumeMounts"] if m["name"] != "job"
    ]
    pod["volumes"] = [v for v in pod["volumes"] if v["name"] != "job"]
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "gate4-network-probe",
            "namespace": NAMESPACE,
            "labels": {"app.kubernetes.io/name": WORKER},
        },
        "spec": pod,
    }


def worker_job(deploy: DeployParams) -> tuple[dict[str, Any], dict[str, Any]]:
    config = KubernetesJobConfig(
        namespace=NAMESPACE,
        image=deploy.worker_image,
        node_selector={"kubernetes.io/hostname": deploy.node},
    )
    spec = JobSpec(ATTEMPT, "task_rehearsal", "http://x", "t", "brief", {"kind": "pi"})
    return render_job(spec, config)


def mutate(change: Callable[[dict[str, Any]], object]) -> Callable[[dict], dict]:
    def apply(job: dict[str, Any]) -> dict[str, Any]:
        mutated = copy.deepcopy(job)
        change(mutated)
        return mutated

    return apply


def pod(job: dict[str, Any]) -> dict[str, Any]:
    return job["spec"]["template"]["spec"]


def container(job: dict[str, Any]) -> dict[str, Any]:
    return pod(job)["containers"][0]


def admission_cases(deploy: DeployParams) -> list[tuple[str, bool, dict[str, Any]]]:
    """(name, admitted?, Job) for the controller's identity."""
    _, job = worker_job(deploy)
    controller_image = deploy.controller_image
    cases: dict[str, Callable[[dict[str, Any]], object]] = {
        "other image": lambda j: container(j).update(image=controller_image),
        "init container image": lambda j: pod(j).update(
            initContainers=[{**container(j), "name": "init", "image": controller_image}]
        ),
        "credential secret named like the job": lambda j: (
            j["metadata"].update(name="orchestrator-secrets"),
            pod(j)["volumes"][0]["secret"].update(secretName="orchestrator-secrets"),
        ),
        "job secret beyond job.json": lambda j: pod(j)["volumes"][0]["secret"].update(
            items=[{"key": "token", "path": "token"}]
        ),
        "controller secret volume": lambda j: pod(j)["volumes"].append(
            {"name": "loot", "secret": {"secretName": "orchestrator-secrets"}}
        ),
        "env from a secret": lambda j: container(j).update(
            envFrom=[{"secretRef": {"name": "orchestrator-secrets"}}]
        ),
        "other service account": lambda j: pod(j).update(serviceAccountName="default"),
        "service account token": lambda j: pod(j).update(
            automountServiceAccountToken=True
        ),
        "data volume claim": lambda j: pod(j)["volumes"].append(
            {
                "name": "data",
                "persistentVolumeClaim": {"claimName": "orchestrator-data"},
            }
        ),
        "host network": lambda j: pod(j).update(hostNetwork=True),
        "privilege escalation": lambda j: container(j)["securityContext"].update(
            allowPrivilegeEscalation=True
        ),
        "no deadline": lambda j: j["spec"].pop("activeDeadlineSeconds"),
        "no limits": lambda j: container(j)["resources"].pop("limits"),
        "image pull secret": lambda j: pod(j).update(
            imagePullSecrets=[{"name": "orchestrator-secrets"}]
        ),
        "not named as an attempt": lambda j: j["metadata"].update(name="orc-other"),
        "missing worker label": lambda j: j["spec"]["template"]["metadata"].update(
            labels={}
        ),
    }
    built = [("worker job", True, job)]
    built += [(name, False, mutate(change)(job)) for name, change in cases.items()]
    return built


def dump(documents: list[dict[str, Any]]) -> str:
    import yaml

    return yaml.safe_dump_all(documents, sort_keys=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.orchestrator.rehearsal")
    parser.add_argument("command", choices=("render", "secrets", "probe", "admission"))
    args = parser.parse_args(argv)
    deploy = params()
    if args.command == "render":
        sys.stdout.write(dump(render(deploy)))
    elif args.command == "secrets":
        sys.stdout.write(dump(placeholder_secrets(deploy)))
    elif args.command == "probe":
        sys.stdout.write(dump([probe_pod(deploy)]))
    else:
        sys.stdout.write(
            json.dumps(
                [
                    {"case": name, "admitted": admitted, "job": job}
                    for name, admitted, job in admission_cases(deploy)
                ]
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
