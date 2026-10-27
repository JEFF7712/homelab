"""Decide whether a website change stays inside delegated authority.

The verifier compares the committed website manifests at the merge target
and at the PR head. It reads files from git objects and parses YAML; it never
runs the PR's renderer or kustomize, so a PR cannot change what is checked.
What it inspects is exactly what Flux applies.

A site's change is intended when its site.yaml or a handwritten file in its
directory changes, or the site is new. Generated files changing on their own
(a renderer change) must leave the site's objects identical. Every new or
changed object, intended or not, must be a vetted kind and satisfy that
kind's security and operational invariants, so editing site.yaml cannot buy
a privileged pod, a public Service or a wider network policy. The check is a
ratchet: an existing object may keep a problem it already had (old debt does
not block routine promotions) but may not gain one.

Findings are `repair` (the agent can fix the PR), `notice` (allowed and
recorded: constrained network exposure of a hardened site, an unprivileged
helper kind, removing a stateless site) or `authority` (an operator decides:
host-level access, deleting stored data, kinds that grant identity, secrets or
routing, or anything the verifier cannot reason about).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import yaml

WEBSITES = "gitops/websites"
SPEC = "site.yaml"
GENERATED = frozenset(
    {
        "namespace.yaml",
        "network-policy.yaml",
        "deployment.yaml",
        "service.yaml",
        "pdb.yaml",
        "kustomization.yaml",
    }
)
# Workloads whose pod template gets the same hardening checks as a Deployment.
POD_TEMPLATES = {
    "Deployment": ("spec", "template", "spec"),
    "StatefulSet": ("spec", "template", "spec"),
    "Job": ("spec", "template", "spec"),
    "CronJob": ("spec", "jobTemplate", "spec", "template", "spec"),
}
# Namespaced kinds that neither run code nor grant identity, secrets or
# routing: allowed and recorded.
NOTICE_KINDS = frozenset(
    {"CiliumNetworkPolicy", "HorizontalPodAutoscaler", "ServiceMonitor", "PodMonitor"}
)
VETTED_KINDS = frozenset(
    {
        "Namespace",
        "NetworkPolicy",
        "Deployment",
        "Service",
        "PodDisruptionBudget",
        "PersistentVolumeClaim",
        "ConfigMap",
        "StatefulSet",
        "Job",
        "CronJob",
    }
)
KUSTOMIZATION_KEYS = frozenset({"apiVersion", "kind", "resources"})
FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.ya?ml")
SITE_NAME = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?")
IMAGE = re.compile(
    r"registry\.rupan\.dev/(apps|upstream)/[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}"
)
INGRESS_NAMESPACES = frozenset({"cloudflare", "observability"})
# Network exposure of a hardened site is constrained: allowed and recorded.
NETWORK_EXPOSURE = (
    "ingress may only come from",
    "services must be ClusterIP",
)
# Access to the node itself is not constrained by the site: an operator decides.
HOST_ACCESS = (
    "may not bind a hostPort",
    "may not set hostNetwork",
    "may not set hostPID",
    "may not set hostIPC",
    "may not be privileged",
    "unvetted source",
)
STORAGE_CLASSES = frozenset({"nfs-cluster"})
PRUNE = "kustomize.toolkit.fluxcd.io/prune"
MAX_FILE_BYTES = 256 * 1024
MAX_REPLICAS = 5
MAX_CPU_MILLICORES = 2000
MAX_MEMORY_MIB = 2048
MAX_STORAGE_GIB = 20
VOLUME_SOURCES = frozenset({"emptyDir", "persistentVolumeClaim", "configMap", "secret"})

Reader = Callable[[str], "str | None"]
Site = dict[str, dict[str, Any]]


@dataclass(frozen=True)
class Finding:
    severity: str  # "repair" or "authority"
    site: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.site}: {self.message}"


class Unreadable(ValueError):
    pass


def _load(read: Reader, path: str) -> list[Any]:
    text = read(path)
    if text is None:
        raise Unreadable(f"{path} is listed but missing")
    if len(text.encode()) > MAX_FILE_BYTES:
        raise Unreadable(f"{path} exceeds {MAX_FILE_BYTES} bytes")
    try:
        return [doc for doc in yaml.safe_load_all(text) if doc is not None]
    except yaml.YAMLError as error:
        raise Unreadable(f"{path} is not valid YAML: {error}") from None


def _kustomization(read: Reader, directory: str) -> list[str]:
    path = f"{directory}/kustomization.yaml"
    docs = _load(read, path)
    if len(docs) != 1 or not isinstance(docs[0], dict):
        raise Unreadable(f"{path} must hold one mapping")
    extra = set(docs[0]) - KUSTOMIZATION_KEYS
    if extra:
        raise Unreadable(f"{path} uses unvetted kustomize fields {sorted(extra)}")
    resources = docs[0].get("resources") or []
    if not isinstance(resources, list) or not all(
        isinstance(r, str) for r in resources
    ):
        raise Unreadable(f"{path} resources must be a list of names")
    return resources


def key(doc: dict[str, Any]) -> str:
    metadata = doc.get("metadata") or {}
    return f"{doc.get('kind')}/{metadata.get('namespace', '')}/{metadata.get('name')}"


def load_tree(read: Reader) -> dict[str, Site]:
    """Every listed site's objects, keyed like canonical.resource_key."""
    sites: dict[str, Site] = {}
    for name in _kustomization(read, WEBSITES):
        if not SITE_NAME.fullmatch(name):
            raise Unreadable(
                f"{WEBSITES} lists {name!r}, which is not a site directory"
            )
        site: Site = {}
        for resource in _kustomization(read, f"{WEBSITES}/{name}"):
            if not FILE_NAME.fullmatch(resource):
                raise Unreadable(
                    f"{name} lists {resource!r}, which is not a plain sibling file"
                )
            for doc in _load(read, f"{WEBSITES}/{name}/{resource}"):
                if not isinstance(doc, dict) or "kind" not in doc:
                    raise Unreadable(f"{name}/{resource} holds a non-object document")
                if key(doc) in site:
                    raise Unreadable(f"{name} defines {key(doc)} twice")
                site[key(doc)] = doc
        sites[name] = site
    return sites


def intended_sites(
    changed: Iterable[str],
    base: dict[str, Site],
    head: dict[str, Site],
    head_specs: set[str],
    base_specs: set[str],
) -> set[str]:
    """Sites the PR means to change, as opposed to re-rendered ones."""
    intended = set(head) - set(base)
    for path in changed:
        parts = path.split("/")
        if len(parts) != 4 or "/".join(parts[:2]) != WEBSITES:
            continue
        site, file = parts[2], parts[3]
        generated_site = site in head_specs or site in base_specs
        if file == SPEC or not generated_site or file not in GENERATED:
            intended.add(site)
    return intended


def _quantity_mib(value: Any) -> float | None:
    text = str(value)
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(Ki|Mi|Gi|Ti)?", text)
    if not match:
        return None
    scale = {
        "Ki": 1 / 1024,
        "Mi": 1,
        "Gi": 1024,
        "Ti": 1024 * 1024,
        None: 1 / (1024 * 1024),
    }
    return float(match.group(1)) * scale[match.group(2)]


def _millicores(value: Any) -> float | None:
    text = str(value)
    if re.fullmatch(r"[0-9]+m", text):
        return float(text[:-1])
    if re.fullmatch(r"[0-9]+(\.[0-9]+)?", text):
        return float(text) * 1000
    return None


def _container(name: str, container: dict[str, Any], problems: list[str]) -> None:
    label = f"container {container.get('name', '?')}"
    image = container.get("image", "")
    if not isinstance(image, str) or not IMAGE.fullmatch(image):
        problems.append(
            f"{label} image must be a digest-pinned registry.rupan.dev reference"
        )
    context = container.get("securityContext") or {}
    if context.get("allowPrivilegeEscalation") is not False:
        problems.append(f"{label} must set allowPrivilegeEscalation: false")
    if context.get("readOnlyRootFilesystem") is not True:
        problems.append(f"{label} must set readOnlyRootFilesystem: true")
    if context.get("privileged"):
        problems.append(f"{label} may not be privileged")
    capabilities = context.get("capabilities") or {}
    if "ALL" not in (capabilities.get("drop") or []) or capabilities.get("add"):
        problems.append(f"{label} must drop ALL capabilities and add none")
    if context.get("runAsUser") == 0 or context.get("runAsGroup") == 0:
        problems.append(f"{label} may not run as root")
    for port in container.get("ports") or []:
        if port.get("hostPort"):
            problems.append(f"{label} may not bind a hostPort")
    limits = (container.get("resources") or {}).get("limits") or {}
    cpu, memory = _millicores(limits.get("cpu")), _quantity_mib(limits.get("memory"))
    if cpu is None or memory is None:
        problems.append(f"{label} needs cpu and memory limits")
    else:
        if cpu > MAX_CPU_MILLICORES:
            problems.append(f"{label} cpu limit exceeds {MAX_CPU_MILLICORES}m")
        if memory > MAX_MEMORY_MIB:
            problems.append(f"{label} memory limit exceeds {MAX_MEMORY_MIB}Mi")


def _workload(name: str, doc: dict[str, Any], problems: list[str]) -> None:
    spec = doc.get("spec") or {}
    replicas = spec.get("replicas", 1)
    if not isinstance(replicas, int) or replicas > MAX_REPLICAS:
        problems.append(f"replicas must be at most {MAX_REPLICAS}")
    pod: Any = doc
    for step in POD_TEMPLATES[doc["kind"]]:
        pod = (pod or {}).get(step) or {}
    if pod.get("automountServiceAccountToken") is not False:
        problems.append("pods must set automountServiceAccountToken: false")
    if pod.get("serviceAccountName", "default") != "default":
        problems.append("pods must use the default service account")
    for flag in ("hostNetwork", "hostPID", "hostIPC"):
        if pod.get(flag):
            problems.append(f"pods may not set {flag}")
    context = pod.get("securityContext") or {}
    if context.get("runAsNonRoot") is not True:
        problems.append("pods must set runAsNonRoot: true")
    if (context.get("seccompProfile") or {}).get("type") != "RuntimeDefault":
        problems.append("pods must use the RuntimeDefault seccomp profile")
    for volume in pod.get("volumes") or []:
        sources = set(volume) - {"name"}
        if not sources <= VOLUME_SOURCES:
            problems.append(
                f"volume {volume.get('name')} uses unvetted source {sorted(sources - VOLUME_SOURCES)}"
            )
    containers = list(pod.get("containers") or []) + list(
        pod.get("initContainers") or []
    )
    if not containers:
        problems.append("workload has no containers")
    for container in containers:
        _container(name, container, problems)


def _network_policy(doc: dict[str, Any], problems: list[str]) -> None:
    for rule in (doc.get("spec") or {}).get("ingress") or []:
        for peer in rule.get("from") or [{}]:
            if peer == {"podSelector": {}}:
                continue
            labels = ((peer.get("namespaceSelector") or {}).get("matchLabels")) or {}
            if (
                set(peer) == {"namespaceSelector"}
                and set(labels) == {"kubernetes.io/metadata.name"}
                and labels["kubernetes.io/metadata.name"] in INGRESS_NAMESPACES
            ):
                continue
            problems.append(
                "ingress may only come from the namespace itself, cloudflare or observability"
            )


def _object(name: str, doc: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    kind = doc.get("kind")
    metadata = doc.get("metadata") or {}
    if kind == "Namespace":
        if metadata.get("name") != name:
            problems.append("the site may only define its own namespace")
        if (metadata.get("labels") or {}).get(
            "pod-security.kubernetes.io/enforce"
        ) != "restricted":
            problems.append(
                "the namespace must enforce the restricted pod security level"
            )
        if set(metadata.get("annotations") or {}) - {PRUNE}:
            problems.append(
                "namespace annotations other than prune protection are not vetted"
            )
        return problems
    if metadata.get("namespace") != name:
        problems.append(f"{kind} {metadata.get('name')} must live in namespace {name}")
    if kind in POD_TEMPLATES:
        _workload(name, doc, problems)
    elif kind == "NetworkPolicy":
        _network_policy(doc, problems)
    elif kind == "Service":
        spec = doc.get("spec") or {}
        if spec.get("type", "ClusterIP") != "ClusterIP" or spec.get("externalIPs"):
            problems.append("services must be ClusterIP without external IPs")
        if not spec.get("selector"):
            problems.append("services must select pods")
    elif kind == "PersistentVolumeClaim":
        spec = doc.get("spec") or {}
        if spec.get("storageClassName") not in STORAGE_CLASSES:
            problems.append(f"claims must use {sorted(STORAGE_CLASSES)}")
        size = _quantity_mib(
            ((spec.get("resources") or {}).get("requests") or {}).get("storage")
        )
        if size is None or size > MAX_STORAGE_GIB * 1024:
            problems.append(f"claims must request at most {MAX_STORAGE_GIB}Gi")
        if (metadata.get("annotations") or {}).get(PRUNE) != "disabled":
            problems.append("claims must carry prune: disabled")
    return problems


def _isolated(site: Site) -> bool:
    return any(
        doc.get("kind") == "NetworkPolicy"
        and (doc.get("spec") or {}).get("podSelector") == {}
        and "Ingress" in ((doc.get("spec") or {}).get("policyTypes") or [])
        for doc in site.values()
    )


def _unprotected_claims(name: str, site: Site) -> bool:
    claims = any(d.get("kind") == "PersistentVolumeClaim" for d in site.values())
    namespace = site.get(f"Namespace//{name}", {})
    annotations = (namespace.get("metadata") or {}).get("annotations") or {}
    return claims and annotations.get(PRUNE) != "disabled"


def verify(
    base: dict[str, Site],
    head: dict[str, Site],
    changed: Iterable[str],
    base_specs: set[str],
    head_specs: set[str],
) -> list[Finding]:
    findings: list[Finding] = []
    intended = intended_sites(changed, base, head, head_specs, base_specs)
    for name in sorted(set(base) - set(head)):
        stateful = any(
            d.get("kind") == "PersistentVolumeClaim" for d in base[name].values()
        )
        findings.append(
            Finding(
                "authority" if stateful else "notice",
                name,
                "removing a site deletes its namespace"
                + (" and its stored data" if stateful else "; git revert restores it"),
            )
        )
    for name in sorted(head):
        old, new = base.get(name, {}), head[name]
        if name not in intended and old != new:
            changed_keys = sorted(
                k for k in old.keys() | new.keys() if old.get(k) != new.get(k)
            )
            findings.append(
                Finding(
                    "repair",
                    name,
                    "generated manifests changed without a site.yaml change: "
                    + ", ".join(changed_keys[:6]),
                )
            )
            continue
        for gone in sorted(old.keys() - new.keys()):
            if gone.startswith("PersistentVolumeClaim/"):
                findings.append(
                    Finding("authority", name, f"deleting {gone} discards stored data")
                )
        touched = [k for k in new if old.get(k) != new[k]]
        if not touched:
            continue
        for object_key in sorted(touched):
            doc = new[object_key]
            if doc.get("kind") not in VETTED_KINDS:
                namespaced = (doc.get("metadata") or {}).get("namespace") == name
                allowed = doc.get("kind") in NOTICE_KINDS and namespaced
                findings.append(
                    Finding(
                        "notice" if allowed else "authority",
                        name,
                        f"{object_key} is not a kind the verifier vets",
                    )
                )
                continue
            # A ratchet: an object that already existed may keep problems it
            # already had, so routine edits are not blocked on old debt, but
            # it may not gain new ones.
            previous = old.get(object_key)
            known = set(_object(name, previous)) if previous else set()
            for problem in _object(name, doc):
                if problem not in known:
                    if any(marker in problem for marker in HOST_ACCESS):
                        severity = "authority"
                    elif any(marker in problem for marker in NETWORK_EXPOSURE):
                        severity = "notice"
                    else:
                        severity = "repair"
                    findings.append(Finding(severity, name, f"{object_key}: {problem}"))
        if not _isolated(new) and (not old or _isolated(old)):
            findings.append(
                Finding("repair", name, "no namespace-wide ingress NetworkPolicy")
            )
        if _unprotected_claims(name, new) and not (
            old and _unprotected_claims(name, old)
        ):
            findings.append(
                Finding(
                    "repair",
                    name,
                    "a site holding claims needs a prune-protected namespace",
                )
            )
    return findings
