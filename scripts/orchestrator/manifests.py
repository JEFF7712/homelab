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
  and the Kubernetes API; workers reach DNS, the worker API and, by FQDN,
  only the model providers' API hosts (PROVIDER_HOSTS);
- an admission policy (config/orchestrator/worker-admission.yaml) admits
  only worker-shaped Jobs from the controller, so its Job permission cannot
  mount the namespace's other Secrets;
- intake evidence is read only in the namespaces enrolled in
  config/orchestrator/evidence-namespaces.json.

The rendered files are applied only after the deployment gates pass; this
module does not bind them into Flux.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NAMESPACE = "persistent-agent"
CONTROLLER = "orchestrator-controller"
WORKER = "orchestrator-worker"
WORKER_API_PORT = 8090
CLIENT_API_PORT = 8091
INTAKE_PORT = 8092
# Read-only access for intake evidence. Secrets and ConfigMaps are absent on
# purpose and a test keeps them out; this list mirrors intake.KINDS.
# Read-only access for intake evidence: pods, their logs and events, and
# nothing else (no Secrets, ConfigMaps, specs of other kinds or nodes). It is
# granted by RoleBinding only in the enrolled namespaces.
EVIDENCE_RULES = (
    {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list"]},
    {"apiGroups": [""], "resources": ["pods/log"], "verbs": ["get"]},
    {"apiGroups": [""], "resources": ["events"], "verbs": ["list"]},
)
EVIDENCE_NAMESPACES_FILE = (
    Path(__file__).resolve().parents[2] / "config/orchestrator/evidence-namespaces.json"
)


def evidence_namespaces() -> tuple[str, ...]:
    """The enrolled namespaces, from the one committed list."""
    document = json.loads(EVIDENCE_NAMESPACES_FILE.read_text())
    allowed = tuple(sorted(document["allowed"]))
    overlap = set(allowed) & set(document["excluded"])
    if overlap:
        raise ValueError(f"namespaces both allowed and excluded: {sorted(overlap)}")
    if NAMESPACE in allowed:
        raise ValueError("the orchestrator never reads its own namespace as evidence")
    return allowed


DATA_PATH = "/var/lib/orchestrator"
# The architecture decision: the controller runs on homelab-02 or homelab-03,
# whichever fresh resource measurements favour; never homelab-05.
CONTROLLER_NODES = frozenset({"homelab-02", "homelab-03"})
# The worker backend configuration is a closed schema. Every key is known,
# every value has a shape that cannot carry an inline credential, and the
# settings that name a credential may only point at an approved secret source.
EXECUTABLE = re.compile(r"^(/[A-Za-z0-9._+/-]+|[A-Za-z0-9._+-]+)$")
NAME = re.compile(r"^[A-Za-z0-9._:/+-]{1,100}$")
PATH = re.compile(r"^/[A-Za-z0-9._/-]{1,200}$")
TOKEN_SHAPE = re.compile(
    r"(?i)(sk-|ghp_|gho_|ghs_|github_pat_|glpat-|xox[abprs]-|eyJ[A-Za-z0-9_-]{8,})"
)
CLAUDE_TOKEN = "/run/worker/claude-oauth-token"
CODEX_HOME = "/var/lib/codex-auth"
APPROVED_CREDENTIALS = {"token_file": {CLAUDE_TOKEN}, "codex_home": {CODEX_HOME}}
BACKEND_SCHEMA: dict[str, dict[str, Any]] = {
    "pi": {"pi": EXECUTABLE, "provider": NAME, "model": NAME, "experimental": bool},
    "codex": {
        "codex": EXECUTABLE,
        "model": NAME,
        "codex_home": "credential",
        "sandbox": ("external", "host"),
    },
    "claude": {
        "claude": EXECUTABLE,
        "model": NAME,
        "config_dir": PATH,
        "token_file": "credential",
        "sandbox": ("external", "host"),
    },
}
BACKEND_REQUIRED = {
    "pi": {"pi", "provider", "model"},
    "codex": {"codex", "model", "codex_home"},
    "claude": {"claude", "model", "config_dir", "token_file"},
}
PINNED = re.compile(r"^[a-z0-9.\-/:]+@sha256:[0-9a-f]{64}$")
# Fixed by config/orchestrator/worker-admission.yaml.
WORKER_IMAGE = re.compile(
    r"^registry\.rupan\.dev/apps/orchestrator-worker@sha256:[0-9a-f]{64}$"
)
WORKER_SECRET = "orchestrator-worker-secrets"
# Workers run in the emptyDir at /work; /job is the read-only job file.
WORKER_ROOT = "/work"
# The only hosts workers reach outside the cluster: the Anthropic and OpenAI
# APIs and OpenAI's sign-in and ChatGPT endpoints the Codex login refreshes
# and runs through. Enforced by FQDN in the worker's CiliumNetworkPolicy.
PROVIDER_HOSTS = (
    "api.anthropic.com",
    "api.openai.com",
    "auth.openai.com",
    "chatgpt.com",
)
NO_PRUNE = {"kustomize.toolkit.fluxcd.io/prune": "disabled"}
LABELS = {"app.kubernetes.io/part-of": "persistent-agent"}


@dataclass(frozen=True)
class IntakeParams:
    """Alert intake: who may post signals, and where failed pipelines are read."""

    woodpecker_url: str
    woodpecker_ip: str
    woodpecker_port: int
    woodpecker_repo_id: int
    open_limit: int = 3


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
    worker_secret_name: str = "orchestrator-worker-secrets"  # = WORKER_SECRET
    extra_worker_mounts: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    intake: IntakeParams | None = None
    # Nightly store snapshots on their own NFS volume, which dumps-to-r2
    # mounts read-only and ships off site; the group the shipper reads as.
    backup: bool = False
    backup_group: int = 1000

    def validate(self) -> None:
        if self.node not in CONTROLLER_NODES:
            raise ValueError(f"node {self.node!r} is not an allowed controller node")
        validate_backend(self.backend, [*self.extra_worker_mounts, *codex_mounts(self)])
        if self.backend.get("kind") == "codex" and self.max_running != 1:
            # The Codex login refreshes its token on use; one Job at a time
            # keeps a single writer of the shared store.
            raise ValueError("a Codex backend needs max_running 1")
        for image in (self.controller_image, self.worker_image):
            if not PINNED.match(image):
                raise ValueError(f"image {image!r} must be pinned by sha256 digest")
        if not self.host_path.startswith("/persist/"):
            raise ValueError("host_path must live under /persist")
        # The admission policy is static and names these; a deployment that
        # differed would have every worker Job refused.
        if not WORKER_IMAGE.match(self.worker_image):
            raise ValueError("worker_image must be apps/orchestrator-worker by digest")
        if self.worker_secret_name != WORKER_SECRET:
            raise ValueError(f"worker_secret_name must be {WORKER_SECRET}")


def validate_backend(
    backend: Mapping[str, Any], mounts: Sequence[Mapping[str, Any]] = ()
) -> None:
    """Check the worker backend configuration against the closed schema.

    Unknown keys (including `env`) are refused, so no setting can smuggle a
    credential under an innocuous name; values must match their shape; and
    `token_file` and `codex_home` must be the approved secret locations,
    which the manifests mount from a Secret or a dedicated volume.
    """
    kind = backend.get("kind")
    if kind not in BACKEND_SCHEMA:
        raise ValueError(f"unknown backend kind {kind!r}")
    schema = BACKEND_SCHEMA[kind]
    unknown = set(backend) - set(schema) - {"kind"}
    if unknown:
        raise ValueError(f"backend keys {sorted(unknown)} are not allowed for {kind}")
    missing = BACKEND_REQUIRED[kind] - set(backend)
    if missing:
        raise ValueError(f"backend keys {sorted(missing)} are required for {kind}")
    mounted = {str(m.get("mountPath")) for m in mounts}
    for key, value in backend.items():
        if key == "kind":
            continue
        rule = schema[key]
        if rule == "credential":
            if value not in APPROVED_CREDENTIALS[key]:
                raise ValueError(
                    f"{key} must be an approved secret source: "
                    f"{sorted(APPROVED_CREDENTIALS[key])}"
                )
            if key == "codex_home" and value not in mounted:
                raise ValueError(f"{key} {value} is not mounted into workers")
        elif rule is bool:
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be a boolean")
        elif isinstance(rule, tuple):
            if value not in rule:
                raise ValueError(f"{key} must be one of {list(rule)}")
        elif (
            not isinstance(value, str)
            or not rule.match(value)
            or TOKEN_SHAPE.search(value)
        ):
            raise ValueError(f"{key} has an invalid value")


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


# The committed admission policy for controller-created Jobs; an enforcement
# path, so a change to it needs the operator's approval of that change.
ADMISSION_POLICY = (
    Path(__file__).resolve().parents[2] / "config/orchestrator/worker-admission.yaml"
)


def admission_documents(params: DeployParams) -> list[dict[str, Any]]:
    """The worker Job admission policy and binding, exactly as committed.

    Nothing is substituted, so no deployment parameter can change a rule;
    validate() instead requires the parameters the policy fixes.
    """
    import yaml

    return [
        document
        for document in yaml.safe_load_all(ADMISSION_POLICY.read_text())
        if document
    ]


CODEX_VOLUME = "codex-auth"


def codex_mounts(params: DeployParams) -> list[dict[str, Any]]:
    if params.backend.get("kind") != "codex":
        return []
    return [{"name": CODEX_VOLUME, "mountPath": CODEX_HOME}]


def codex_documents(params: DeployParams) -> list[dict[str, Any]]:
    """The Codex login store: a node-local volume beside, not inside, the
    controller's, mounted only into Codex worker Jobs."""
    if params.backend.get("kind") != "codex":
        return []
    name = "orchestrator-codex-auth"
    return [
        {
            "apiVersion": "v1",
            "kind": "PersistentVolume",
            "metadata": {"name": name, "labels": dict(LABELS), "annotations": NO_PRUNE},
            "spec": {
                "capacity": {"storage": "64Mi"},
                "accessModes": ["ReadWriteOnce"],
                "persistentVolumeReclaimPolicy": "Retain",
                "storageClassName": "local-nvme",
                "local": {"path": f"{params.host_path}-codex-auth"},
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
                "claimRef": {"namespace": NAMESPACE, "name": name},
            },
        },
        {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": meta(name, annotations=NO_PRUNE),
            "spec": {
                "accessModes": ["ReadWriteOnce"],
                "storageClassName": "local-nvme",
                "volumeName": name,
                "resources": {"requests": {"storage": "64Mi"}},
            },
        },
    ]


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
    if params.backend.get("kind") == "codex":
        volumes.append(
            {
                "name": CODEX_VOLUME,
                "persistentVolumeClaim": {"claimName": "orchestrator-codex-auth"},
            }
        )
    return volumes, [*mounts, *codex_mounts(params), *params.extra_worker_mounts]


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
        **intake_config(params, secrets),
        "jobs": {
            "kind": "kubernetes",
            "namespace": NAMESPACE,
            "image": params.worker_image,
            "service_account": WORKER,
            "node_selector": {"kubernetes.io/hostname": params.node},
            "extra_volumes": volumes,
            "extra_mounts": mounts,
        },
        "work_root": WORKER_ROOT,
    }


def intake_config(params: DeployParams, secrets: str) -> dict[str, Any]:
    if params.intake is None:
        return {}
    return {
        "intake": {
            "listen": f"0.0.0.0:{INTAKE_PORT}",
            "token_files": {
                "alertmanager": f"{secrets}/alertmanager-intake-token",
                "sentinel": f"{secrets}/sentinel-intake-token",
            },
            "open_limit": params.intake.open_limit,
            "kubernetes_evidence": True,
            "evidence_namespaces": list(evidence_namespaces()),
            "woodpecker": {
                "url": params.intake.woodpecker_url,
                "repo_id": params.intake.woodpecker_repo_id,
                "token_file": f"{secrets}/woodpecker-token",
            },
        }
    }


def intake_documents(params: DeployParams) -> list[dict[str, Any]]:
    """The evidence ClusterRole and one RoleBinding per enrolled namespace.

    A ClusterRole referenced from RoleBindings grants nothing cluster-wide:
    the controller can read pods, logs and events only where it is bound.
    """
    if params.intake is None:
        return []
    name = "orchestrator-evidence"
    return [
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRole",
            "metadata": {"name": name, "labels": dict(LABELS)},
            "rules": [dict(rule) for rule in EVIDENCE_RULES],
        },
        *(
            {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "RoleBinding",
                "metadata": {
                    "name": name,
                    "namespace": namespace,
                    "labels": dict(LABELS),
                },
                "roleRef": {
                    "apiGroup": "rbac.authorization.k8s.io",
                    "kind": "ClusterRole",
                    "name": name,
                },
                "subjects": [
                    {
                        "kind": "ServiceAccount",
                        "name": CONTROLLER,
                        "namespace": NAMESPACE,
                    }
                ],
            }
            for namespace in evidence_namespaces()
        ),
    ]


def backup_documents(params: DeployParams) -> list[dict[str, Any]]:
    """A nightly snapshot of the store onto a volume of its own.

    The volume holds only these snapshots, so the job cannot read or alter
    other datasets' dumps. dumps-to-r2 mounts it read-only and ships it off
    site at 03:00, and the restore drill restores and checks the newest
    snapshot. The job runs on the controller's node beside the local volume
    and needs no network.
    """
    if not params.backup:
        return []
    name = "orchestrator-backup"
    return [
        {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": meta("orchestrator-dumps", annotations=NO_PRUNE),
            "spec": {
                "accessModes": ["ReadWriteMany"],
                "storageClassName": "nfs-cluster",
                "resources": {"requests": {"storage": "1Gi"}},
            },
        },
        {
            "apiVersion": "batch/v1",
            "kind": "CronJob",
            "metadata": meta(name),
            "spec": {
                "schedule": "30 2 * * *",
                "concurrencyPolicy": "Forbid",
                "successfulJobsHistoryLimit": 2,
                "failedJobsHistoryLimit": 3,
                "jobTemplate": {
                    "spec": {
                        "backoffLimit": 2,
                        "activeDeadlineSeconds": 900,
                        "template": {
                            "metadata": {
                                "labels": {**LABELS, "app.kubernetes.io/name": name}
                            },
                            "spec": {
                                "automountServiceAccountToken": False,
                                "restartPolicy": "OnFailure",
                                "nodeSelector": {"kubernetes.io/hostname": params.node},
                                "securityContext": {
                                    "fsGroup": 10001,
                                    "supplementalGroups": [params.backup_group],
                                },
                                "containers": [
                                    {
                                        **restricted_container(
                                            "backup",
                                            params.controller_image,
                                            [
                                                "python",
                                                "-m",
                                                "scripts.orchestrator",
                                                "backup-rotate",
                                                "--db",
                                                f"{DATA_PATH}/orchestrator.sqlite",
                                                "--dir",
                                                "/dumps",
                                                "--keep",
                                                "7",
                                                "--group",
                                                str(params.backup_group),
                                            ],
                                        ),
                                        "resources": {
                                            "requests": {
                                                "cpu": "50m",
                                                "memory": "64Mi",
                                            },
                                            "limits": {"memory": "256Mi"},
                                        },
                                        "volumeMounts": [
                                            {"name": "data", "mountPath": DATA_PATH},
                                            {"name": "dumps", "mountPath": "/dumps"},
                                            {"name": "tmp", "mountPath": "/tmp"},
                                        ],
                                    }
                                ],
                                "volumes": [
                                    {
                                        "name": "data",
                                        "persistentVolumeClaim": {
                                            "claimName": "orchestrator-data"
                                        },
                                    },
                                    {
                                        "name": "dumps",
                                        "persistentVolumeClaim": {
                                            "claimName": "orchestrator-dumps"
                                        },
                                    },
                                    {"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}},
                                ],
                            },
                        },
                    }
                },
            },
        },
    ]


def worker_egress(params: DeployParams) -> dict[str, Any]:
    """Provider-only public egress for worker pods.

    The worker NetworkPolicy allows DNS and the worker API only; this Cilium
    policy adds TCP 443 to the provider hostnames and nothing else, so a
    worker cannot upload to an arbitrary host. FQDN matching needs Cilium to
    see the worker's DNS answers, hence the DNS rule with an L7 pattern.
    """
    return {
        "apiVersion": "cilium.io/v2",
        "kind": "CiliumNetworkPolicy",
        "metadata": meta("orchestrator-worker-egress"),
        "spec": {
            "endpointSelector": {"matchLabels": {"app.kubernetes.io/name": WORKER}},
            "egress": [
                {
                    "toEndpoints": [
                        {
                            "matchLabels": {
                                "k8s:io.kubernetes.pod.namespace": "kube-system",
                                "k8s:k8s-app": "kube-dns",
                            }
                        }
                    ],
                    "toPorts": [
                        {
                            "ports": [
                                {"port": "53", "protocol": "UDP"},
                                {"port": "53", "protocol": "TCP"},
                            ],
                            # Only names a worker needs: the providers and
                            # cluster-internal names (CoreDNS answers those
                            # itself and forwards nothing), so a query cannot
                            # carry data to an outside nameserver.
                            "rules": {
                                "dns": [
                                    *({"matchName": host} for host in PROVIDER_HOSTS),
                                    {"matchPattern": "*.cluster.local"},
                                    {"matchPattern": "*.svc"},
                                ]
                            },
                        }
                    ],
                },
                {
                    "toFQDNs": [{"matchName": host} for host in PROVIDER_HOSTS],
                    # Provider IPs are shared (Cloudflare fronts OpenAI), so
                    # the TLS server name is pinned too: another site on an
                    # allowed address is refused.
                    "toPorts": [
                        {
                            "ports": [{"port": "443", "protocol": "TCP"}],
                            "serverNames": list(PROVIDER_HOSTS),
                        }
                    ],
                },
            ],
        },
    }


def intake_ingress() -> dict[str, Any]:
    """Only Alertmanager and cluster-sentinel may post signals."""
    return {
        "from": [
            {
                "namespaceSelector": {
                    "matchLabels": {"kubernetes.io/metadata.name": namespace}
                },
                "podSelector": {"matchLabels": {"app.kubernetes.io/name": name}},
            }
            for namespace, name in (
                ("observability", "alertmanager"),
                ("automation", "cluster-sentinel"),
            )
        ],
        "ports": [{"protocol": "TCP", "port": INTAKE_PORT}],
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
    documents = [
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
                {
                    "kind": "ServiceAccount",
                    "name": CONTROLLER,
                    "namespace": NAMESPACE,
                }
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
                                    },
                                    *(
                                        [
                                            {
                                                "name": "intake",
                                                "containerPort": INTAKE_PORT,
                                            }
                                        ]
                                        if params.intake
                                        else []
                                    ),
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
                    },
                    *(
                        [
                            {
                                "name": "intake",
                                "port": INTAKE_PORT,
                                "targetPort": "intake",
                            }
                        ]
                        if params.intake
                        else []
                    ),
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
                    },
                    *([intake_ingress()] if params.intake else []),
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
                    *(
                        [
                            {
                                "to": [
                                    {
                                        "ipBlock": {
                                            "cidr": f"{params.intake.woodpecker_ip}/32"
                                        }
                                    }
                                ],
                                "ports": [
                                    {
                                        "protocol": "TCP",
                                        "port": params.intake.woodpecker_port,
                                    }
                                ],
                            }
                        ]
                        if params.intake
                        else []
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
                # No DNS here: an L4 allow would bypass the L7 DNS filter in
                # the worker's CiliumNetworkPolicy, which grants DNS instead.
                "egress": [
                    {
                        "to": [{"podSelector": {"matchLabels": selector}}],
                        "ports": [{"protocol": "TCP", "port": WORKER_API_PORT}],
                    },
                ],
            },
        },
    ]
    return (
        documents
        + [worker_egress(params)]
        + admission_documents(params)
        + codex_documents(params)
        + intake_documents(params)
        + backup_documents(params)
    )
