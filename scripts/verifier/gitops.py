"""Verify non-website GitOps changes for host-level access and data destruction.

Flux applies `gitops/` directly on merge without a CI deployment operation, so
the merge policy is the only gate between an agent PR and the cluster. Website
directories (`gitops/websites/*`) have their own stricter profile in
`websites.py`; this module enforces the constitutional holds across the rest of
`gitops/`:

- **Host-level pod access** (`privileged`, `hostNetwork`, `hostPID`, `hostIPC`,
  `hostPort`, `hostPath`, or widening a Namespace to `privileged` Pod Security):
  can reach the node and therefore the enforcement host (`homelab-04`). Checked
  as a ratchet against the merge base so existing reviewed workloads (such as
  Home Assistant, Alloy or node-exporter) can update images and settings without
  escalating, while any new workload or newly added host capability escalates.
- **Persistent-data destruction**: deleting a `PersistentVolumeClaim`,
  `PersistentVolume` or CloudNativePG `Cluster`, dropping its Flux
  `prune: disabled` protection, or changing `persistentVolumeReclaimPolicy` away
  from `Retain` escalates to the operator on the approvals dashboard.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

import yaml

from scripts.verifier.websites import PRUNE, WEBSITES, Finding, Unreadable

GITOPS = "gitops"
SKIP_PREFIXES = (
    WEBSITES + "/",
    "gitops/secrets/",
    "gitops/ingress/crds/",
)
MAX_FILE_BYTES = 512 * 1024
POD_TEMPLATES: dict[str, tuple[str, ...]] = {
    "Pod": ("spec",),
    "Deployment": ("spec", "template", "spec"),
    "StatefulSet": ("spec", "template", "spec"),
    "DaemonSet": ("spec", "template", "spec"),
    "ReplicaSet": ("spec", "template", "spec"),
    "Job": ("spec", "template", "spec"),
    "CronJob": ("spec", "jobTemplate", "spec", "template", "spec"),
}
STATEFUL_KINDS = frozenset({"PersistentVolumeClaim", "PersistentVolume", "Cluster"})
POD_SECURITY_ENFORCE = "pod-security.kubernetes.io/enforce"

Reader = Callable[[str], "str | None"]


def covered_yaml(path: str) -> bool:
    return (
        path.startswith(GITOPS + "/")
        and path.endswith((".yaml", ".yml"))
        and not path.endswith(".sops.yaml")
        and not path.endswith(".sops.yml")
        and not path.startswith(SKIP_PREFIXES)
    )


def _docs(read: Reader, path: str) -> list[dict[str, Any]]:
    text = read(path)
    if text is None:
        return []
    if len(text.encode()) > MAX_FILE_BYTES:
        raise Unreadable(f"{path} exceeds {MAX_FILE_BYTES} bytes")
    try:
        loaded = list(yaml.safe_load_all(text))
    except yaml.YAMLError as error:
        raise Unreadable(f"{path} is not valid YAML: {error}") from None
    result: list[dict[str, Any]] = []
    for doc in loaded:
        if not isinstance(doc, dict):
            continue
        if doc.get("kind") == "List":
            for item in doc.get("items") or []:
                if isinstance(item, dict) and "kind" in item:
                    result.append(item)
        elif "kind" in doc and doc.get("kind") != "CustomResourceDefinition":
            result.append(doc)
    return result


def _id(doc: dict[str, Any]) -> str:
    meta = doc.get("metadata") or {}
    return f"{doc.get('kind')}/{meta.get('namespace', '')}/{meta.get('name', '')}"


def _pod_spec(doc: dict[str, Any]) -> dict[str, Any] | None:
    chain = POD_TEMPLATES.get(str(doc.get("kind", "")))
    if chain is None:
        return None
    cursor: Any = doc
    for step in chain:
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(step)
    return cursor if isinstance(cursor, dict) else None


def host_capabilities(doc: dict[str, Any]) -> set[str]:
    """Host-reaching capabilities requested by a Kubernetes object."""
    found: set[str] = set()
    if doc.get("kind") == "Namespace":
        labels = (doc.get("metadata") or {}).get("labels") or {}
        if labels.get(POD_SECURITY_ENFORCE) == "privileged":
            found.add("pod-security enforce=privileged")
        return found
    spec = _pod_spec(doc)
    if spec is None:
        return found
    for flag in ("hostNetwork", "hostPID", "hostIPC"):
        if spec.get(flag) is True:
            found.add(flag)
    for group in ("containers", "initContainers", "ephemeralContainers"):
        for container in spec.get(group) or []:
            if not isinstance(container, dict):
                continue
            security = container.get("securityContext") or {}
            if isinstance(security, dict) and security.get("privileged") is True:
                found.add("privileged container")
            for port in container.get("ports") or []:
                if isinstance(port, dict) and port.get("hostPort") is not None:
                    found.add(f"hostPort:{port.get('hostPort')}")
    for volume in spec.get("volumes") or []:
        if isinstance(volume, dict) and "hostPath" in volume:
            hp = volume.get("hostPath")
            target = hp.get("path", "") if isinstance(hp, dict) else ""
            found.add(f"hostPath:{target}")
    return found


def _is_stateful(doc: dict[str, Any]) -> bool:
    kind = doc.get("kind")
    if kind in {"PersistentVolumeClaim", "PersistentVolume"}:
        return True
    if kind == "Cluster" and str(doc.get("apiVersion", "")).startswith(
        "postgresql.cnpg.io/"
    ):
        return True
    return False


def _prune_disabled(doc: dict[str, Any]) -> bool:
    annotations = (doc.get("metadata") or {}).get("annotations") or {}
    return str(annotations.get(PRUNE, "")).lower() == "disabled"


def verify_gitops(
    changed: Iterable[str],
    read_base: Reader | None,
    read_head: Reader | None,
) -> list[Finding]:
    """Authority and repair findings for changed non-website GitOps manifests."""
    if read_base is None or read_head is None:
        return []
    paths = sorted(p for p in changed if covered_yaml(p))
    if not paths:
        return []
    base_objects: dict[str, tuple[str, dict[str, Any]]] = {}
    head_objects: dict[str, tuple[str, dict[str, Any]]] = {}
    for path in paths:
        for doc in _docs(read_base, path):
            base_objects[_id(doc)] = (path, doc)
        for doc in _docs(read_head, path):
            head_objects[_id(doc)] = (path, doc)

    findings: list[Finding] = []
    for obj_id, (path, doc) in sorted(head_objects.items()):
        before = base_objects.get(obj_id)
        added_caps = host_capabilities(doc) - (
            host_capabilities(before[1]) if before else set()
        )
        for cap in sorted(added_caps):
            findings.append(
                Finding(
                    "authority",
                    path,
                    f"{obj_id} grants host-level access ({cap})",
                )
            )

    for obj_id, (path, before_doc) in sorted(base_objects.items()):
        after = head_objects.get(obj_id)
        if _is_stateful(before_doc):
            if after is None:
                findings.append(
                    Finding(
                        "authority",
                        path,
                        f"deletes stateful resource {obj_id}",
                    )
                )
                continue
            after_doc = after[1]
            if _prune_disabled(before_doc) and not _prune_disabled(after_doc):
                findings.append(
                    Finding(
                        "authority",
                        after[0],
                        f"{obj_id} removes {PRUNE}=disabled protection",
                    )
                )
            if (
                before_doc.get("kind") == "PersistentVolume"
                and (before_doc.get("spec") or {}).get("persistentVolumeReclaimPolicy")
                == "Retain"
                and (after_doc.get("spec") or {}).get("persistentVolumeReclaimPolicy")
                != "Retain"
            ):
                findings.append(
                    Finding(
                        "authority",
                        after[0],
                        f"{obj_id} weakens persistentVolumeReclaimPolicy from Retain",
                    )
                )
        if (
            before_doc.get("kind") == "Kustomization"
            and str(before_doc.get("apiVersion", "")).startswith(
                "kustomize.toolkit.fluxcd.io/"
            )
            and after is None
            and (before_doc.get("spec") or {}).get("prune") is True
        ):
            findings.append(
                Finding(
                    "authority",
                    path,
                    f"deletes pruning Flux Kustomization {obj_id}",
                )
            )
    return findings
