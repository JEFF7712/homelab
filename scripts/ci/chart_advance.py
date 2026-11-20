"""Advance Renovate's Helm chart bumps on the registry-advance schedule.

A Renovate chart PR moves only `spec.chart.spec.version`, but every
HelmRelease here pins its app images through post-renderer digests, so the
chart alone would run new templates on old images (docs/runbooks/
local-registry.md, "Upgrading a Helm chart"). The schedule therefore never
merges Renovate's branch. It renders the release at both versions with its own
values and takes the bump only when nothing but post-renderer-pinned image
tags, labels and annotations, and CRD schemas that only grow, differ. Anything
else (pod spec, RBAC, config, monitoring rules, added or removed resources,
values from other objects) stays with a person.

An eligible bump lands in two steps, like an upstream image:

- chart mirror: declare the new images in the nearest registry-sources.yaml
  and lock only their records (scripts/registry/advance.py `mirror`), merged
  as its own PR so registry-lock-import copies them;
- chart bump: once the registry serves every new digest, move the chart
  version and those post-renderer digests together in one PR.

A bump settles on pod evidence (the schedule's read-only identity cannot read
HelmReleases): no pod may still run a moved image's old digest, and a Ready
pod must run each new one. A bump that does not get there within two hours,
or whose HelmRelease reports a failed upgrade, is reverted. A bump that moves
no pinned image cannot be verified that way and stays with a person.
The Renovate engine and CloudNativePG (its operator rolls every database) are
held by config/registry/advance-policy.json `hold_charts`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

MIRROR_BRANCH = "registry-advance/chart-mirror-"
BUMP_BRANCH = "registry-advance/chart-bump-"
REVERT_BRANCH = "registry-advance/chart-revert-"
REGISTRY_PREFIX = "registry.rupan.dev/"
LINE_LENGTH = "  # yamllint disable-line rule:line-length"
VERSION_LINE = re.compile(r"^([-+])\s+version:\s*['\"]?([\w.+-]+)['\"]?\s*$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
# Regenerated on every render (webhook CAs, TLS secrets) or volatile labels.
RANDOM_KEYS = {"caBundle", "ca.crt", "ca.key", "tls.crt", "tls.key"}


class _RenderLoader(yaml.SafeLoader):
    """Safe loading that reads YAML 1.1's `=` value tag as a string, as charts
    emit it (kube-prometheus-stack's CRD enums)."""


def _value(loader: yaml.SafeLoader, node: yaml.Node) -> str:
    assert isinstance(node, yaml.ScalarNode)
    return str(loader.construct_scalar(node))


_RenderLoader.add_constructor("tag:yaml.org,2002:value", _value)


@dataclass(frozen=True)
class ChartBump:
    number: int
    path: str
    old: str
    new: str


def chart_bump(number: int, files: list[str], diff: str) -> ChartBump | None:
    """The chart version move of a Renovate PR that changes one HelmRelease."""
    if len(files) != 1 or not re.fullmatch(r"gitops/.+\.ya?ml", files[0]):
        return None
    removed, added = [], []
    for line in diff.splitlines():
        if line.startswith(("---", "+++")):
            continue
        match = VERSION_LINE.match(line)
        if match:
            (removed if match[1] == "-" else added).append(match[2])
        elif line.startswith(("-", "+")) and line[1:].strip():
            return None
    if len(removed) != 1 or len(added) != 1:
        return None
    return ChartBump(number, files[0], removed[0], added[0])


def helm_release(text: str) -> dict[str, Any] | None:
    for document in yaml.safe_load_all(text):
        if isinstance(document, dict) and document.get("kind") == "HelmRelease":
            return document
    return None


def release_id(release: Mapping[str, Any]) -> str:
    metadata = release.get("metadata") or {}
    return f"{metadata.get('namespace', 'default')}/{metadata.get('name')}"


def workload_namespace(release: Mapping[str, Any]) -> str:
    spec = release.get("spec") or {}
    return spec.get("targetNamespace") or (release.get("metadata") or {}).get(
        "namespace", "default"
    )


def pinned_images(release: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """{upstream image name: {newName, digest}} from the post-renderers."""
    pinned = {}
    for renderer in (release.get("spec") or {}).get("postRenderers") or []:
        for image in ((renderer.get("kustomize") or {}).get("images")) or []:
            if image.get("name") and DIGEST.match(str(image.get("digest", ""))):
                pinned[image["name"]] = {
                    "newName": str(image.get("newName", image["name"])),
                    "digest": str(image["digest"]),
                }
    return pinned


def policy_hold(release: Mapping[str, Any], policy: Mapping[str, Any]) -> str | None:
    """Why the policy keeps a release's chart with a person, or None."""
    if release_id(release) in policy.get("hold_charts", []):
        return f"{release_id(release)} is held for a person"
    namespace = workload_namespace(release)
    if namespace in policy.get("deny_namespaces", []):
        return f"namespace {namespace} is denied"
    held = policy.get("held_namespaces", {})
    if namespace in held:
        return f"namespace {namespace} {held[namespace]}"
    if (release.get("spec") or {}).get("valuesFrom"):
        return "values come from other objects, so a render cannot judge it"
    return None


def _split_image(reference: str) -> tuple[str, str]:
    name, _, tag = reference.split("@")[0].rpartition(":")
    if not name or "/" in tag:
        return reference.split("@")[0], ""
    return name, tag


def _strip(node: Any) -> Any:
    """Drop what differs between renders without changing behaviour."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key in RANDOM_KEYS or key == "description":
                continue
            if key == "metadata" and isinstance(value, dict):
                value = {
                    k: v for k, v in value.items() if k not in {"labels", "annotations"}
                }
            out[key] = _strip(value)
        return out
    if isinstance(node, list):
        return [_strip(item) for item in node]
    return node


def _documents(rendered: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    found = {}
    for document in rendered:
        if not isinstance(document, dict) or "kind" not in document:
            continue
        metadata = document.get("metadata") or {}
        key = (
            f"{document['kind']}/{metadata.get('namespace', '')}/{metadata.get('name')}"
        )
        found[key] = dict(document)
    return found


def _superset(old: Any, new: Any) -> bool:
    """Whether a CRD schema only grew: nothing removed, nothing newly required."""
    if isinstance(old, dict) and isinstance(new, dict):
        for key, value in old.items():
            if key == "required":
                if not set(new.get(key, [])) <= set(value):
                    return False
                continue
            if key not in new or not _superset(value, new[key]):
                return False
        return "required" in old or not new.get("required")
    if isinstance(old, list) and isinstance(new, list):
        if all(not isinstance(item, (dict, list)) for item in old + new):
            return set(map(str, old)) <= set(map(str, new))
        return len(old) == len(new) and all(map(_superset, old, new))
    return old == new


def _first_difference(old: Any, new: Any, path: str = "") -> str | None:
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new), key=str):
            if key not in old or key not in new:
                return f"{path}.{key}"
            found = _first_difference(old[key], new[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            return f"{path}[]"
        for index, (a, b) in enumerate(zip(old, new, strict=True)):
            found = _first_difference(a, b, f"{path}[{index}]")
            if found:
                return found
        return None
    return None if old == new else path or "."


def _take_images(
    old: Any, new: Any, pinned: set[str], moves: dict[str, tuple[str, str]]
) -> Any:
    """`new` with each pinned image tag move recorded and put back to `old`."""
    if isinstance(old, dict) and isinstance(new, dict):
        out = {}
        for key, value in new.items():
            if (
                key == "image"
                and isinstance(value, str)
                and isinstance(old.get(key), str)
                and value != old[key]
            ):
                old_name, old_tag = _split_image(old[key])
                new_name, new_tag = _split_image(value)
                if old_name == new_name and old_name in pinned and old_tag and new_tag:
                    moves[old_name] = (old_tag, new_tag)
                    out[key] = old[key]
                    continue
            out[key] = _take_images(old.get(key), value, pinned, moves)
        return out
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        return [
            _take_images(a, b, pinned, moves) for a, b in zip(old, new, strict=True)
        ]
    return new


def classify(
    old_render: Iterable[Mapping[str, Any]],
    new_render: Iterable[Mapping[str, Any]],
    pinned: set[str],
) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """(pinned image tag moves, reasons a person must look) for two renders."""
    old_docs, new_docs = _documents(old_render), _documents(new_render)
    reasons = [f"{key} is new" for key in sorted(set(new_docs) - set(old_docs))]
    reasons += [f"{key} is removed" for key in sorted(set(old_docs) - set(new_docs))]
    moves: dict[str, tuple[str, str]] = {}
    for key in sorted(set(old_docs) & set(new_docs)):
        old, new = _strip(old_docs[key]), _strip(new_docs[key])
        if key.startswith("CustomResourceDefinition/"):
            if not _superset(old, new):
                reasons.append(f"{key} schema loses or newly requires a field")
            continue
        if key.startswith("Secret/"):
            old.pop("data", None)
            new.pop("data", None)
        new = _take_images(old, new, pinned, moves)
        difference = _first_difference(old, new)
        if difference:
            reasons.append(f"{key} changes {difference}")
    return moves, reasons


def render(
    root: Path, release: Mapping[str, Any], version: str, helm: str = "helm"
) -> list[dict[str, Any]]:
    spec = release["spec"]
    chart = spec["chart"]["spec"]
    source = chart["sourceRef"]["name"]
    url = None
    for path in sorted((root / "gitops").rglob("*.y*ml")):
        for document in yaml.safe_load_all(path.read_text()):
            if (
                isinstance(document, dict)
                and document.get("kind") == "HelmRepository"
                and (document.get("metadata") or {}).get("name") == source
            ):
                url = document["spec"]["url"]
    if url is None:
        raise RuntimeError(f"no HelmRepository {source}")
    with tempfile.TemporaryDirectory(prefix="chart-advance-") as scratch:
        values = Path(scratch) / "values.yaml"
        values.write_text(yaml.safe_dump(spec.get("values") or {}))
        name = (release.get("metadata") or {}).get("name")
        command = [helm, "template", name]
        if url.startswith("oci://"):
            command.append(f"{url.rstrip('/')}/{chart['chart']}")
        else:
            command += [chart["chart"], "--repo", url]
        command += [
            "--version",
            version,
            "--namespace",
            workload_namespace(release),
            "--values",
            str(values),
            "--include-crds",
        ]
        # CI jobs may have no writable home; keep helm's repository cache here.
        env = dict(
            os.environ,
            HELM_CACHE_HOME=f"{scratch}/cache",
            HELM_CONFIG_HOME=f"{scratch}/config",
            HELM_DATA_HOME=f"{scratch}/data",
        )
        rendered = subprocess.run(
            command, check=True, capture_output=True, text=True, cwd=scratch, env=env
        ).stdout
    return [
        d for d in yaml.load_all(rendered, Loader=_RenderLoader) if isinstance(d, dict)
    ]


def declaration_file(root: Path, release_path: str) -> Path | None:
    """The nearest inventory-only registry-sources.yaml above a release."""
    directory = (root / release_path).parent
    while directory != root and directory.name:
        for candidate in sorted(directory.glob("registry-sources.y*ml")):
            return candidate
        directory = directory.parent
    return None


def declare(text: str, images: Mapping[str, str]) -> str:
    """Declare `name:tag@digest` references, replacing a line for the same name."""
    lines = text.rstrip("\n").split("\n")
    for reference in images.values():
        name = _split_image(reference)[0]
        line = f"  - image: {reference}{LINE_LENGTH}"
        for index, existing in enumerate(lines):
            match = re.match(r"\s*-\s*image:\s*(\S+)", existing)
            if match and _split_image(match[1])[0] == name:
                lines[index] = line
                break
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def bump_release(
    text: str,
    bump: ChartBump,
    pinned: Mapping[str, Mapping[str, str]],
    digests: Mapping[str, str],
) -> str:
    """The release with its chart version and moved post-renderer digests."""
    version = re.compile(
        rf"^(\s+version:\s*['\"]?){re.escape(bump.old)}(['\"]?\s*)$", re.MULTILINE
    )
    if len(version.findall(text)) != 1:
        raise ValueError(f"{bump.path}: chart version {bump.old} is not unique")
    text = version.sub(rf"\g<1>{bump.new}\g<2>", text)
    for name, digest in digests.items():
        old = pinned[name]["digest"]
        if text.count(old) != 1:
            raise ValueError(f"{bump.path}: digest {old} of {name} is not unique")
        text = text.replace(old, digest)
    return text


def manifest_digest(reference: str, skopeo: str = "skopeo") -> str:
    """The index (or manifest) digest an upstream tag points at now."""
    command = [skopeo, "inspect", "--raw"]
    auth = os.environ.get("REGISTRY_SOURCE_AUTH_FILE")
    if auth:
        command += ["--authfile", auth]
    raw = subprocess.run(
        [*command, f"docker://{reference}"], check=True, capture_output=True
    ).stdout
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def locked(lock: Mapping[str, Any], reference: str) -> bool:
    return any(
        (entry.get("source") or {}).get("reference") == reference
        for entry in lock["images"]
    )


def settle_verdict(
    release: Mapping[str, Any] | None,
    version: str,
    pods: Iterable[tuple[str, bool]],
    digests: Mapping[str, str],
) -> tuple[str, str]:
    """('verified'|'waiting'|'failed', why) for a merged chart bump.

    Pods decide: every moved image's old digest must be gone and a Ready pod
    must run each new one. The schedule's read-only identity cannot read
    HelmReleases; when `release` is readable anyway, a failed upgrade fails
    the bump at once.
    """
    if release is not None:
        status = release.get("status") or {}
        ready = next(
            (c for c in status.get("conditions") or [] if c.get("type") == "Ready"),
            {},
        )
        if str(status.get("lastAttemptedRevision", "")) == version and ready.get(
            "reason"
        ) in {"InstallFailed", "UpgradeFailed", "RollbackSucceeded", "RetriesExceeded"}:
            return "failed", f"{ready.get('reason')}: {ready.get('message', '')[:200]}"
    pods = list(pods)
    for old, new in digests.items():
        if any(old in image for image, _ in pods):
            return "waiting", f"a pod still runs {old[:19]}"
        if not any(new in image and ready for image, ready in pods):
            return "waiting", f"no Ready pod runs {new[:19]} yet"
    return "verified", f"every pod runs the new digests of chart {version}"


def bump_changes(diff: str) -> tuple[str, str, str, dict[str, str]] | None:
    """(path, old version, new version, {old digest: new digest}) of a chart bump PR."""
    path = ""
    versions: dict[str, list[str]] = {"-": [], "+": []}
    digests: dict[str, list[str]] = {"-": [], "+": []}
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith(("---", "+++")):
            continue
        elif line[:1] in versions:
            match = VERSION_LINE.match(line)
            if match:
                versions[match[1]].append(match[2])
            digests[line[0]] += re.findall(r"sha256:[0-9a-f]{64}", line)
    if not path or len(versions["-"]) != 1 or len(versions["+"]) != 1:
        return None
    if len(digests["-"]) != len(digests["+"]):
        return None
    return (
        path,
        versions["-"][0],
        versions["+"][0],
        dict(zip(digests["-"], digests["+"], strict=True)),
    )


def branch_slug(release: Mapping[str, Any], version: str) -> str:
    return hashlib.sha256(f"{release_id(release)}@{version}".encode()).hexdigest()[:12]


def kubectl_json(*args: str) -> Any:
    kubectl = os.environ.get("KUBECTL", "kubectl")
    result = subprocess.run(
        [kubectl, *args, "-o", "json"], capture_output=True, text=True, cwd="/tmp"
    )
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


def running_images(namespace: str) -> list[tuple[str, bool]]:
    """(imageID, ready or finished) for every container of every pod."""
    pods = kubectl_json("-n", namespace, "get", "pods") or {"items": []}
    return [
        (
            str(status.get("imageID", "")),
            bool(status.get("ready") or (status.get("state") or {}).get("terminated")),
        )
        for pod in pods["items"]
        for key in ("initContainerStatuses", "containerStatuses")
        for status in (pod.get("status") or {}).get(key) or []
    ]


# A bump that is not Ready at its version this long after its merge failed:
# Flux retries a failed upgrade within minutes.
SETTLE_TIMEOUT = 2 * 3600
SETTLE_WINDOW = 24 * 3600

Api = Callable[..., Any]
Publish = Callable[[str, str, str, str, list[str]], None]


def settle_bumps(
    root: Path,
    token: str,
    closed: Iterable[Mapping[str, Any]],
    now: float,
    api: Api,
    publish: Publish,
) -> tuple[str | None, set[str]]:
    """Settle merged chart bumps; ('reverted' if one was, releases still waiting)."""
    from scripts.ci.registry_advance import REVERTED, VERIFIED, parse_time

    busy: set[str] = set()
    bumps = [
        p
        for p in closed
        if str((p.get("head") or {}).get("ref", "")).startswith(BUMP_BRANCH)
    ]
    for pull in sorted(bumps, key=lambda p: str(p.get("merged_at") or "")):
        merged = parse_time(pull.get("merged_at"))
        if merged is None or now - merged > SETTLE_WINDOW:
            continue
        number = pull["number"]
        bodies = " ".join(
            c.get("body", "") for c in api(token, f"/issues/{number}/comments")
        )
        if VERIFIED in bodies or REVERTED in bodies:
            continue
        changes = bump_changes(api(token, f"/pulls/{number}.diff", raw=True))
        release = helm_release((root / changes[0]).read_text()) if changes else None
        if changes is None or release is None:
            print(
                f"chart settle #{number}: cannot read the bump; leaving it to a person"
            )
            continue
        path, old_version, new_version, digests = changes
        metadata = release.get("metadata") or {}
        live = kubectl_json(
            "-n",
            str(metadata.get("namespace")),
            "get",
            "helmrelease",
            str(metadata.get("name")),
        )
        verdict, why = settle_verdict(
            live,
            new_version,
            running_images(workload_namespace(release)),
            digests,
        )
        if verdict == "waiting" and now - merged > SETTLE_TIMEOUT:
            verdict, why = "failed", f"still {why} after {SETTLE_TIMEOUT // 3600}h"
        if verdict == "waiting":
            print(f"chart settle #{number}: waiting: {why}")
            busy.add(release_id(release))
            continue
        if verdict == "verified":
            api(
                token,
                f"/issues/{number}/comments",
                data={"body": f"{VERIFIED}\n{why}."},
            )
            print(f"chart settle #{number}: verified")
            continue
        text = (root / path).read_text()
        revert = ChartBump(number, path, new_version, old_version)
        swapped = {
            name: old
            for name, old in zip(digests.values(), digests.keys(), strict=True)
        }
        pinned = {new: {"digest": new} for new in swapped}
        (root / path).write_text(bump_release(text, revert, pinned, swapped))
        title = f"charts: revert #{number}, {release_id(release)} {new_version} failed"
        body = f"#{number} did not settle (scheduled chart-advance): {why}"
        publish(token, f"{REVERT_BRANCH}{number}", title, body, [path])
        api(
            token,
            f"/issues/{number}/comments",
            data={"body": f"{REVERTED}\n{why}; reverted by {REVERT_BRANCH}{number}."},
        )
        print(f"chart settle #{number}: reverted ({why})")
        return "reverted", busy
    return None, busy


def step(
    root: Path,
    token: str,
    lock: Mapping[str, Any],
    policy: Mapping[str, Any],
    busy: set[str],
    api: Api,
    publish: Publish,
    serves: Callable[[str, str], bool],
    renovate: str,
) -> bool:
    """Take one chart step (a mirror or a bump); True when something was published."""
    from scripts.ci.registry_advance import IDENTITY, RECORDS
    from scripts.registry.advance import mirror

    for pull in sorted(
        api(token, "/pulls?state=open&limit=100"), key=lambda p: p["number"]
    ):
        if (pull.get("user") or {}).get("login") != renovate:
            continue
        number = pull["number"]
        files = [f["filename"] for f in api(token, f"/pulls/{number}/files")]
        bump = chart_bump(number, files, api(token, f"/pulls/{number}.diff", raw=True))
        if bump is None or not (root / bump.path).is_file():
            continue
        release = helm_release((root / bump.path).read_text())
        if release is None:
            continue
        label = f"chart #{number} {release_id(release)} {bump.old} -> {bump.new}"
        current = str(release["spec"]["chart"]["spec"].get("version"))
        if current != bump.old:
            print(f"{label}: main is at {current}; Renovate will refresh it")
            continue
        hold = policy_hold(release, policy)
        if hold or release_id(release) in busy:
            print(
                f"{label}: not eligible: {hold or 'an earlier bump is still settling'}"
            )
            continue
        pinned = pinned_images(release)
        try:
            moves, reasons = classify(
                render(root, release, bump.old),
                render(root, release, bump.new),
                set(pinned),
            )
        except (
            subprocess.CalledProcessError,
            RuntimeError,
            KeyError,
            yaml.YAMLError,
        ) as error:
            print(f"{label}: cannot render: {error}")
            continue
        if not moves and not reasons:
            # Settling reads pods, so a bump that moves no image cannot be
            # verified by the schedule.
            reasons = ["no pinned image moves, so pods cannot verify it"]
        if reasons:
            print(f"{label}: needs a person: {'; '.join(reasons[:3])}")
            continue
        upstream = {
            name: pinned[name]["newName"]
            .removeprefix(REGISTRY_PREFIX)
            .removeprefix("upstream/")
            for name in moves
        }
        references = {
            name: f"{upstream[name]}:{new_tag}@{manifest_digest(f'{upstream[name]}:{new_tag}')}"
            for name, (_, new_tag) in moves.items()
        }
        missing = {n: r for n, r in references.items() if not locked(lock, r)}
        if missing:
            declarations = declaration_file(root, bump.path)
            if declarations is None:
                print(f"{label}: no registry-sources.yaml declares its images")
                continue
            declarations.write_text(declare(declarations.read_text(), missing))
            changed = declarations.relative_to(root).as_posix()
            subprocess.run(["git", "add", "--", changed], cwd=root, check=True)
            subprocess.run(
                ["git", *IDENTITY, "commit", "-q", "-m", "declaration"],
                cwd=root,
                check=True,
            )
            try:
                mirror(root, "HEAD~1")
            finally:
                subprocess.run(
                    ["git", "reset", "-q", "--soft", "HEAD~1"], cwd=root, check=True
                )
            paths = [changed, *sorted(RECORDS)]
            title = (
                f"registry: mirror images for {release_id(release)} chart {bump.new}"
            )
            body = (
                f"Renovate #{number} moves {release_id(release)} to chart {bump.new}; "
                "rendered with its values, nothing changes but these pinned images "
                "(scheduled chart-advance, scripts/ci/chart_advance.py):\n\n"
                + "\n".join(f"- {r}" for r in missing.values())
            )
            publish(
                token,
                f"{MIRROR_BRANCH}{branch_slug(release, bump.new)}",
                title,
                body,
                paths,
            )
            return True
        digests = {name: ref.split("@")[1] for name, ref in references.items()}
        waiting = [
            name
            for name in moves
            if not serves(
                pinned[name]["newName"].removeprefix(REGISTRY_PREFIX), digests[name]
            )
        ]
        if waiting:
            print(f"{label}: waiting for the import of {', '.join(waiting)}")
            continue
        text = (root / bump.path).read_text()
        (root / bump.path).write_text(bump_release(text, bump, pinned, digests))
        title = f"charts: upgrade {release_id(release)} to {bump.new}"
        body = (
            f"Renovate #{number}'s chart bump with its pinned images moved together "
            "(scheduled chart-advance, scripts/ci/chart_advance.py). Rendered with "
            "its values, nothing else differs:\n\n"
            + "\n".join(
                f"- {name}: {old} -> {new}" for name, (old, new) in moves.items()
            )
        )
        publish(
            token,
            f"{BUMP_BRANCH}{branch_slug(release, bump.new)}",
            title,
            body,
            [bump.path],
        )
        return True
    return False
