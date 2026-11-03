"""Render the controller's Kubernetes manifests for single-node operation.

Two inputs are deployment decisions, so they are required parameters rather
than committed defaults: the node (chosen from fresh resource measurements,
never homelab-05) and the images (pinned by digest after the registry lane
publishes them). Everything else is fixed here and covered by tests:

- one controller replica, `Recreate`, pinned to the node with a local PV,
  so the SQLite store can never move without a manual restore;
- workers run on the same node with no service-account token, no host
  paths and the restricted Pod Security profile;
- workers hold no forge credential: only the publisher in the controller
  pod writes to Git, with a token mounted into the controller alone;
- default-deny NetworkPolicies; the controller reaches DNS, the forge, ntfy
  and the Kubernetes API; workers reach DNS, the worker API and public HTTPS
  for model providers, and nothing private (not the forge).

The rendered files are applied only after the deployment gates pass; this
module does not bind them into Flux.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

NAMESPACE = "persistent-agent"
CONTROLLER = "orchestrator-controller"
WORKER = "orchestrator-worker"
WORKER_API_PORT = 8090
CLIENT_API_PORT = 8091
DATA_PATH = "/var/lib/orchestrator"
# The architecture decision: the controller runs on homelab-02 or homelab-03,
# whichever fresh resource measurements favour; never homelab-05.
CONTROLLER_NODES = frozenset({"homelab-02", "homelab-03"})
CREDENTIAL_KEY = re.compile(
    r"(?i)(token|secret|password|api_key|apikey|credential)(?!_file|_dir)"
)
PINNED = re.compile(r"^[a-z0-9.\-/:]+@sha256:[0-9a-f]{64}$")
PRIVATE = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "100.64.0.0/10",
    "169.254.0.0/16",
)
NO_PRUNE = {"kustomize.toolkit.fluxcd.io/prune": "disabled"}
LABELS = {"app.kubernetes.io/part-of": "persistent-agent"}


@dataclass(frozen=True)
class DeployParams:
    node: str
    controller_image: str
    worker_image: str
    host_path: str
    forge_url: str
    forge_ip: str
    forge_owner: str
    forge_repo: str
    project: str
    repository_url: str
    issue_url_prefix: str
    backend: Mapping[str, Any]
    principal: str
    forge_login: str
    required_contexts: tuple[str, ...]
    ci_login: str
    api_endpoints: tuple[tuple[str, int], ...]
    ntfy_topic: str
    base_branch: str = "main"
    max_running: int = 1
    storage: str = "5Gi"
    secret_name: str = "orchestrator-secrets"
    worker_secret_name: str = "orchestrator-worker-secrets"
    extra_worker_mounts: Sequence[Mapping[str, Any]] = field(default_factory=tuple)

    def validate(self) -> None:
        if self.node not in CONTROLLER_NODES:
            raise ValueError(f"node {self.node!r} is not an allowed controller node")
        for key in _keys(self.backend):
            if CREDENTIAL_KEY.search(key):
                raise ValueError(
                    f"backend config key {key!r} looks like an inline credential; "
                    "mount it from a Secret and pass a file path"
                )
        for image in (self.controller_image, self.worker_image):
            if not PINNED.match(image):
                raise ValueError(f"image {image!r} must be pinned by sha256 digest")
        if not self.host_path.startswith("/persist/"):
            raise ValueError("host_path must live under /persist")


def _keys(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        return [str(k) for k in value] + [k for v in value.values() for k in _keys(v)]
    if isinstance(value, list | tuple):
        return [k for item in value for k in _keys(item)]
    return []


def meta(
    name: str, extra: Mapping[str, Any] | None = None, **fields: Any
) -> dict[str, Any]:
    return {
        "name": name,
        "namespace": NAMESPACE,
        "labels": dict(LABELS),
        **(extra or {}),
        **fields,
    }


def restricted_container(name: str, image: str, args: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "image": image,
        "args": args,
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
            "readOnlyRootFilesystem": True,
            "runAsNonRoot": True,
            "runAsUser": 10001,
            "runAsGroup": 10001,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
    }


WORKER_SECRET_KEYS = {
    "pi": (),
    "codex": (),
    "claude": ("claude-oauth-token",),
}


def worker_secret_keys(backend: Mapping[str, Any]) -> tuple[str, ...]:
    """Worker Secret keys a backend may mount: its provider credential only."""
    kind = str(backend.get("kind"))
    if kind not in WORKER_SECRET_KEYS:
        raise ValueError(f"unknown backend kind {kind!r}")
    return WORKER_SECRET_KEYS[kind]


def worker_volumes(params: DeployParams) -> tuple[list[Any], list[Any]]:
    keys = worker_secret_keys(params.backend)
    volumes: list[Any] = []
    mounts: list[Any] = []
    if keys:
        volumes.append(
            {
                "name": "worker-secrets",
                "secret": {
                    "secretName": params.worker_secret_name,
                    "defaultMode": 0o400,
                    # Only the keys this backend needs, never the whole Secret.
                    "items": [{"key": key, "path": key} for key in keys],
                },
            }
        )
        mounts.append(
            {"name": "worker-secrets", "mountPath": "/run/worker", "readOnly": True}
        )
    return volumes, [*mounts, *params.extra_worker_mounts]


def controller_config(params: DeployParams) -> dict[str, Any]:
    secrets = "/run/orchestrator"
    volumes, mounts = worker_volumes(params)
    return {
        "db": f"{DATA_PATH}/orchestrator.sqlite",
        "policy_version": "persistent-agent-v1",
        "owner": CONTROLLER,
        "project": params.project,
        "max_running": params.max_running,
        "worker_api_url": f"http://{CONTROLLER}.{NAMESPACE}.svc:{WORKER_API_PORT}",
        "base_branch": params.base_branch,
        "publisher": {
            "repository_url": params.repository_url,
            "token_file": f"{secrets}/forge-publisher-token",
            "work_dir": f"{DATA_PATH}/publisher",
        },
        "issue_url_prefix": params.issue_url_prefix,
        "forge": {
            "url": params.forge_url,
            "owner": params.forge_owner,
            "repo": params.forge_repo,
            "token_file": f"{secrets}/forge-controller-token",
        },
        "ntfy": {
            "url": "http://ntfy-ntfy.observability.svc",
            "topic": params.ntfy_topic,
            "token_file": f"{secrets}/ntfy-token",
        },
        "backend": dict(params.backend),
        "verifier": {
            "required_contexts": list(params.required_contexts),
            "reviewers": {params.principal: params.forge_login},
            "ci_logins": [params.ci_login],
        },
        "forge_logins": {params.principal: params.forge_login},
        "clients": [
            {
                "name": "supervisor",
                "principal": params.principal,
                "token_file": f"{secrets}/client-token",
            }
        ],
        "listen": {
            "worker": f"0.0.0.0:{WORKER_API_PORT}",
            "client": f"127.0.0.1:{CLIENT_API_PORT}",
        },
        "jobs": {
            "kind": "kubernetes",
            "namespace": NAMESPACE,
            "image": params.worker_image,
            "service_account": WORKER,
            "node_selector": {"kubernetes.io/hostname": params.node},
            "extra_volumes": volumes,
            "extra_mounts": mounts,
        },
    }


def render(params: DeployParams) -> list[dict[str, Any]]:
    params.validate()
    selector = {"app.kubernetes.io/name": CONTROLLER}
    worker_selector = {"app.kubernetes.io/name": WORKER}
    dns = {
        "to": [
            {
                "namespaceSelector": {
                    "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                },
                "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
            }
        ],
        "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
    }
    forge = {
        "to": [{"ipBlock": {"cidr": f"{params.forge_ip}/32"}}],
        "ports": [{"protocol": "TCP", "port": 443}, {"protocol": "TCP", "port": 3000}],
    }
    return [
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": NAMESPACE,
                "labels": {
                    **LABELS,
                    "pod-security.kubernetes.io/enforce": "restricted",
                },
                "annotations": NO_PRUNE,
            },
        },
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": meta(CONTROLLER),
        },
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": meta(WORKER),
            "automountServiceAccountToken": False,
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "Role",
            "metadata": meta("orchestrator-worker-jobs"),
            "rules": [
                {
                    "apiGroups": ["batch"],
                    "resources": ["jobs"],
                    "verbs": ["create", "get", "delete"],
                },
                {
                    "apiGroups": [""],
                    "resources": ["secrets"],
                    "verbs": ["create", "delete"],
                },
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": meta("orchestrator-worker-jobs"),
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "Role",
                "name": "orchestrator-worker-jobs",
            },
            "subjects": [
                {"kind": "ServiceAccount", "name": CONTROLLER, "namespace": NAMESPACE}
            ],
        },
        {
            "apiVersion": "v1",
            "kind": "PersistentVolume",
            "metadata": {
                "name": "orchestrator-data",
                "labels": dict(LABELS),
                "annotations": NO_PRUNE,
            },
            "spec": {
                "capacity": {"storage": params.storage},
                "accessModes": ["ReadWriteOnce"],
                "persistentVolumeReclaimPolicy": "Retain",
                "storageClassName": "local-nvme",
                "local": {"path": params.host_path},
                "nodeAffinity": {
                    "required": {
                        "nodeSelectorTerms": [
                            {
                                "matchExpressions": [
                                    {
                                        "key": "kubernetes.io/hostname",
                                        "operator": "In",
                                        "values": [params.node],
                                    }
                                ]
                            }
                        ]
                    }
                },
                "claimRef": {"namespace": NAMESPACE, "name": "orchestrator-data"},
            },
        },
        {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": meta("orchestrator-data", annotations=NO_PRUNE),
            "spec": {
                "accessModes": ["ReadWriteOnce"],
                "storageClassName": "local-nvme",
                "volumeName": "orchestrator-data",
                "resources": {"requests": {"storage": params.storage}},
            },
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": meta("orchestrator-config"),
            "data": {"config.json": json.dumps(controller_config(params), indent=2)},
        },
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": meta(CONTROLLER),
            "spec": {
                "replicas": 1,
                "strategy": {"type": "Recreate"},
                "selector": {"matchLabels": selector},
                "template": {
                    "metadata": {"labels": {**LABELS, **selector}},
                    "spec": {
                        "serviceAccountName": CONTROLLER,
                        "nodeSelector": {"kubernetes.io/hostname": params.node},
                        "securityContext": {"fsGroup": 10001},
                        "containers": [
                            {
                                **restricted_container(
                                    "controller",
                                    params.controller_image,
                                    [
                                        "python",
                                        "-m",
                                        "scripts.orchestrator",
                                        "serve",
                                        "--config",
                                        "/etc/orchestrator/config.json",
                                    ],
                                ),
                                "ports": [
                                    {
                                        "name": "worker-api",
                                        "containerPort": WORKER_API_PORT,
                                    }
                                ],
                                "resources": {
                                    "requests": {"cpu": "100m", "memory": "128Mi"},
                                    "limits": {"cpu": "1", "memory": "512Mi"},
                                },
                                "volumeMounts": [
                                    {
                                        "name": "config",
                                        "mountPath": "/etc/orchestrator",
                                        "readOnly": True,
                                    },
                                    {
                                        "name": "secrets",
                                        "mountPath": "/run/orchestrator",
                                        "readOnly": True,
                                    },
                                    {"name": "data", "mountPath": DATA_PATH},
                                    {"name": "tmp", "mountPath": "/tmp"},
                                ],
                            }
                        ],
                        "volumes": [
                            {
                                "name": "config",
                                "configMap": {"name": "orchestrator-config"},
                            },
                            {
                                "name": "secrets",
                                "secret": {
                                    "secretName": params.secret_name,
                                    "defaultMode": 0o400,
                                },
                            },
                            {
                                "name": "data",
                                "persistentVolumeClaim": {
                                    "claimName": "orchestrator-data"
                                },
                            },
                            {"name": "tmp", "emptyDir": {"sizeLimit": "256Mi"}},
                        ],
                    },
                },
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": meta(CONTROLLER),
            "spec": {
                "selector": selector,
                "ports": [
                    {
                        "name": "worker-api",
                        "port": WORKER_API_PORT,
                        "targetPort": "worker-api",
                    }
                ],
            },
        },
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": meta("default-deny"),
            "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]},
        },
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": meta(CONTROLLER),
            "spec": {
                "podSelector": {"matchLabels": selector},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [
                    {
                        "from": [{"podSelector": {"matchLabels": worker_selector}}],
                        "ports": [{"protocol": "TCP", "port": WORKER_API_PORT}],
                    }
                ],
                "egress": [
                    dns,
                    forge,
                    {
                        "to": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {
                                        "kubernetes.io/metadata.name": "observability"
                                    }
                                },
                                "podSelector": {
                                    "matchLabels": {"app.kubernetes.io/name": "ntfy"}
                                },
                            }
                        ],
                        "ports": [{"protocol": "TCP", "port": "http"}],
                    },
                    *(
                        {
                            "to": [{"ipBlock": {"cidr": f"{ip}/32"}}],
                            "ports": [{"protocol": "TCP", "port": port}],
                        }
                        for ip, port in params.api_endpoints
                    ),
                ],
            },
        },
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": meta(WORKER),
            "spec": {
                "podSelector": {"matchLabels": worker_selector},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [],
                "egress": [
                    dns,
                    {
                        "to": [{"podSelector": {"matchLabels": selector}}],
                        "ports": [{"protocol": "TCP", "port": WORKER_API_PORT}],
                    },
                    {
                        "to": [
                            {"ipBlock": {"cidr": "0.0.0.0/0", "except": list(PRIVATE)}}
                        ],
                        "ports": [{"protocol": "TCP", "port": 443}],
                    },
                ],
            },
        },
    ]
