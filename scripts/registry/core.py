from __future__ import annotations

import base64
import binascii
import dataclasses
import datetime as dt
import fnmatch
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_REGISTRY = "registry.rupan.dev"
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REPOSITORY_RE = re.compile(
    r"[a-z0-9]+(?:[._]|__|[-]*[a-z0-9]+)*(?:/[a-z0-9]+(?:[._]|__|[-]*[a-z0-9]+)*)*\Z"
)
_TAG_RE = re.compile(r"[\w][\w.-]{0,127}\Z", re.ASCII)
_IMAGE_FIELD_RE = re.compile(
    r"^\s*(?:-\s*)?image:\s*[\"']?([^\s\"'#{}]+)", re.MULTILINE
)
_KIND_RE = re.compile(r"^kind:\s*HelmRelease\s*$", re.MULTILINE)
_INDEX_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }
)
_HASH_RE = re.compile(r"\$(?:2[abxy]\$\d{2}\$|argon2(?:id|i)?\$)\S{8,}\Z")
_CHART_RE = re.compile(r"^\s+chart:\s*[\"']?([^\s\"'#{}]+)", re.MULTILINE)


class RegistryError(RuntimeError):
    pass


class TransientRegistryError(RegistryError):
    """A failure that says nothing about the registry's actual contents.

    Cloudflare Access intermittently answers the runner instead of zot, which
    makes an authenticated read look like a bare 401 and a tag lookup look like
    a missing tag. Reporting that as drift pages on a phantom, so the probe
    distinguishes it and the record is marked inconclusive.
    """


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


@dataclasses.dataclass(frozen=True)
class ImageReference:
    registry: str
    repository: str
    tag: str | None = None
    digest: str | None = None

    @classmethod
    def parse(cls, value: str) -> ImageReference:
        raw = value.strip()
        if not raw or any(char.isspace() for char in raw):
            raise RegistryError(f"invalid image reference: {value!r}")
        if "://" in raw:
            raise RegistryError("image references must not contain a URL scheme")

        name, separator, digest = raw.partition("@")
        if separator and (not _DIGEST_RE.fullmatch(digest) or "@" in digest):
            raise RegistryError(f"invalid sha256 digest in image reference: {raw}")

        slash = name.rfind("/")
        colon = name.rfind(":")
        tag: str | None = None
        if colon > slash:
            tag = name[colon + 1 :]
            name = name[:colon]
            if not _TAG_RE.fullmatch(tag):
                raise RegistryError(f"invalid image tag in reference: {raw}")

        if name != name.lower():
            raise RegistryError(f"repository names must be lowercase: {raw}")

        parts = name.split("/")
        if not all(parts):
            raise RegistryError(f"invalid repository in image reference: {raw}")
        first = parts[0].lower()
        if "." in first or ":" in first or first == "localhost":
            registry = first
            repository = "/".join(parts[1:])
        else:
            registry = "docker.io"
            repository = "/".join(parts)
        if registry == "docker.io" and "/" not in repository:
            repository = f"library/{repository}"
        if not repository or not _REPOSITORY_RE.fullmatch(repository):
            raise RegistryError(f"invalid repository in image reference: {raw}")
        _validate_registry(registry)
        return cls(registry, repository, tag, digest or None)

    @property
    def canonical(self) -> str:
        suffix = f":{self.tag}" if self.tag else ""
        if self.digest:
            suffix += f"@{self.digest}"
        return f"{self.registry}/{self.repository}{suffix}"

    @property
    def immutable(self) -> str:
        if not self.digest:
            raise RegistryError(f"reference is not digest-pinned: {self.canonical}")
        return f"{self.registry}/{self.repository}@{self.digest}"


def _validate_registry(registry: str) -> None:
    if registry != registry.lower() or "/" in registry or not registry:
        raise RegistryError(f"invalid registry hostname: {registry}")
    host, separator, port = registry.rpartition(":")
    hostname = host if separator else registry
    if separator and (not port.isdigit() or not 1 <= int(port) <= 65535):
        raise RegistryError(f"invalid registry port: {registry}")
    if not re.fullmatch(r"(?:localhost|[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)", hostname):
        raise RegistryError(f"invalid registry hostname: {registry}")


def image_kind(reference: ImageReference) -> str:
    if reference.registry == DEFAULT_REGISTRY and reference.repository.startswith(
        "apps/"
    ):
        return "first-party"
    if reference.registry in {
        "docker.io",
        "ghcr.io",
    } and reference.repository.startswith("jeff7712/"):
        return "first-party"
    return "upstream"


def destination_repository(reference: ImageReference, kind: str | None = None) -> str:
    selected_kind = kind or image_kind(reference)
    if selected_kind == "first-party":
        if reference.registry == DEFAULT_REGISTRY and reference.repository.startswith(
            "apps/"
        ):
            return reference.repository
        if not reference.repository.startswith("jeff7712/"):
            raise RegistryError(
                f"first-party repository lacks the expected owner: {reference.repository}"
            )
        destination = f"apps/{reference.repository.removeprefix('jeff7712/')}"
    elif selected_kind == "upstream":
        if ":" in reference.registry:
            host, port = reference.registry.rsplit(":", 1)
            destination = f"upstream/{host}/port-{port}/{reference.repository}"
        else:
            destination = f"upstream/{reference.registry}/{reference.repository}"
    else:
        raise RegistryError(f"unknown image kind: {selected_kind}")
    if not _REPOSITORY_RE.fullmatch(destination):
        raise RegistryError(f"derived invalid destination repository: {destination}")
    return destination


def _stable_id(reference: ImageReference, kind: str) -> str:
    identity = reference.canonical.encode()
    slug = re.sub(r"[^a-z0-9]+", "-", reference.repository).strip("-")[:48]
    return f"{kind}-{slug}-{hashlib.sha256(identity).hexdigest()[:12]}"


def _decode_local_upstream_mirror(
    reference: ImageReference, destination_registry: str
) -> ImageReference | None:
    prefix = "upstream/"
    if (
        reference.registry != destination_registry
        or not reference.repository.startswith(prefix)
    ):
        return None
    try:
        return ImageReference.parse(
            reference.repository.removeprefix(prefix)
            + (f":{reference.tag}" if reference.tag else "")
            + (f"@{reference.digest}" if reference.digest else "")
        )
    except RegistryError:
        return None


def _source_revision(root: pathlib.Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def _timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def build_live_snapshot(
    payload: Mapping[str, Any], *, observed_at: str | None = None
) -> dict[str, Any]:
    if payload.get("kind") != "List" or not isinstance(payload.get("items"), list):
        raise RegistryError("kubectl workload response must be a Kubernetes List")

    images: dict[str, set[str]] = {}
    for item in payload["items"]:
        if not isinstance(item, dict):
            raise RegistryError("kubectl workload response contains an invalid item")
        kind = item.get("kind")
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            continue
        namespace = metadata.get("namespace", "default")
        name = metadata.get("name")
        if not isinstance(name, str):
            continue

        if kind == "Pod":
            pod_status = item.get("status")
            if isinstance(pod_status, dict) and pod_status.get("phase") in {
                "Succeeded",
                "Failed",
            }:
                continue
            spec = item.get("spec") or {}
            status_fields = (
                "initContainerStatuses",
                "containerStatuses",
                "ephemeralContainerStatuses",
            )
            consumer_prefix = f"{namespace}/{name}"
        elif kind == "Job":
            job_status = item.get("status") or {}
            conditions = job_status.get("conditions", [])
            if job_status.get("completionTime") or any(
                isinstance(condition, dict)
                and condition.get("status") == "True"
                and condition.get("type") in {"Complete", "Failed"}
                for condition in conditions
            ):
                continue
            spec = (item.get("spec") or {}).get("template", {}).get("spec", {})
            status_fields = ()
            consumer_prefix = f"{namespace}/job-{name}"
        elif kind == "CronJob":
            spec = (
                (item.get("spec") or {})
                .get("jobTemplate", {})
                .get("spec", {})
                .get("template", {})
                .get("spec", {})
            )
            status_fields = ()
            consumer_prefix = f"{namespace}/cronjob-{name}"
        else:
            continue

        if not isinstance(spec, dict):
            continue
        statuses: dict[str, str] = {}
        status = item.get("status", {})
        if isinstance(status, dict):
            for field in status_fields:
                entries = status.get(field, [])
                if isinstance(entries, list):
                    statuses.update(
                        {
                            entry["name"]: entry["imageID"]
                            for entry in entries
                            if isinstance(entry, dict)
                            and isinstance(entry.get("name"), str)
                            and isinstance(entry.get("imageID"), str)
                        }
                    )

        containers = []
        for field in ("initContainers", "containers", "ephemeralContainers"):
            values = spec.get(field, [])
            if isinstance(values, list):
                containers.extend(values)
        for container in containers:
            if not isinstance(container, dict):
                continue
            container_name = container.get("name")
            spec_image = container.get("image")
            if not isinstance(container_name, str) or not isinstance(spec_image, str):
                continue
            reference = ImageReference.parse(spec_image)
            image_id = statuses.get(container_name, "").removeprefix(
                "docker-pullable://"
            )
            if reference.digest is None and "@sha256:" in image_id:
                observed = ImageReference.parse(image_id)
                if (
                    observed.registry == reference.registry
                    and observed.repository == reference.repository
                    and observed.digest is not None
                ):
                    reference = dataclasses.replace(reference, digest=observed.digest)
            consumer = f"{consumer_prefix}/{container_name}"
            images.setdefault(reference.canonical, set()).add(consumer)

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "registry-observed-images",
        "observed_at": observed_at or _timestamp(),
        "evidence": "sanitized read-only Kubernetes Pod, Job, and CronJob image snapshot",
        "coverage": ["helm-generated", "k3s-bootstrap", "live-workloads"],
        "images": [
            {"reference": reference, "consumers": sorted(consumers)}
            for reference, consumers in sorted(images.items())
        ],
    }


def refresh_live_snapshot(root: pathlib.Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["kubectl", "get", "pods,jobs,cronjobs", "-A", "-o", "json"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RegistryError("cannot read live Kubernetes image inventory") from error
    if result.returncode != 0:
        raise RegistryError("kubectl could not read live Kubernetes image inventory")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RegistryError(
            "kubectl returned invalid JSON for live image inventory"
        ) from error
    snapshot = build_live_snapshot(payload)
    atomic_write_json(root / "registry/observed-images.json", snapshot)
    return snapshot


def discover_inventory(root: pathlib.Path) -> dict[str, Any]:
    root = root.resolve()
    candidates = [root / "gitops", root / "flake"]
    top_level = [root / ".gitlab-ci.yml"]
    files: list[pathlib.Path] = []
    for candidate in candidates:
        if candidate.exists():
            files.extend(
                path
                for path in candidate.rglob("*")
                if path.is_file() and path.suffix in {".yaml", ".yml", ".nix", ".json"}
            )
    files.extend(path for path in top_level if path.is_file())

    found: dict[str, dict[str, Any]] = {}
    gaps: list[dict[str, Any]] = []
    for path in sorted(files):
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(root).as_posix()
        for match in _IMAGE_FIELD_RE.finditer(text):
            raw = match.group(1).rstrip(",")
            try:
                reference = ImageReference.parse(raw)
            except RegistryError:
                continue
            line = text.count("\n", 0, match.start()) + 1
            consumer = f"{relative}:{line}"
            key = reference.canonical
            entry = found.setdefault(
                key,
                _inventory_entry(reference),
            )
            entry["consumers"].append(consumer)
        if _KIND_RE.search(text):
            chart_match = _CHART_RE.search(text)
            gaps.append(
                {
                    "id": f"helm-generated:{relative}",
                    "class": "helm-generated",
                    "consumer": relative,
                    "input": chart_match.group(1) if chart_match else "unknown-chart",
                    "reason": "chart-generated workload images require a pinned render or live reconciliation",
                }
            )

    observed_coverage: set[str] = set()
    observed_path = root / "registry/observed-images.json"
    if observed_path.exists():
        observed = read_json(observed_path)
        if (
            observed.get("schema_version") != SCHEMA_VERSION
            or observed.get("kind") != "registry-observed-images"
        ):
            raise RegistryError("unsupported or invalid observed image schema")
        coverage = observed.get("coverage", [])
        if not isinstance(coverage, list) or not all(
            isinstance(item, str) for item in coverage
        ):
            raise RegistryError("observed-images coverage must be an array of strings")
        observed_coverage.update(coverage)
        for index, item in enumerate(observed.get("images", [])):
            if not isinstance(item, dict) or not isinstance(item.get("reference"), str):
                raise RegistryError(f"observed-images images[{index}] is invalid")
            reference = ImageReference.parse(item["reference"])
            consumers = item.get("consumers")
            if (
                not isinstance(consumers, list)
                or not consumers
                or not all(
                    isinstance(consumer, str) and consumer for consumer in consumers
                )
            ):
                raise RegistryError(
                    f"observed-images images[{index}].consumers is invalid"
                )
            matching_key = next(
                (
                    key
                    for key, candidate in found.items()
                    if candidate["source"]["registry"] == reference.registry
                    and candidate["source"]["repository"] == reference.repository
                    and (
                        candidate["source"]["digest"] == reference.digest
                        if reference.digest is not None
                        else candidate["source"]["tag"] == reference.tag
                    )
                ),
                None,
            )
            if matching_key is not None:
                entry = found.pop(matching_key)
                candidate_tag = entry["source"]["tag"]
                merged_reference = ImageReference(
                    reference.registry,
                    reference.repository,
                    reference.tag if reference.tag is not None else candidate_tag,
                    reference.digest,
                )
                entry["source"] = {
                    "registry": merged_reference.registry,
                    "repository": merged_reference.repository,
                    "tag": merged_reference.tag,
                    "digest": merged_reference.digest,
                    "reference": merged_reference.canonical,
                }
                entry["id"] = _stable_id(merged_reference, entry["kind"])
                found[merged_reference.canonical] = entry
            else:
                entry = found.setdefault(
                    reference.canonical, _inventory_entry(reference)
                )
            manifest = item.get("manifest")
            if manifest is not None:
                if (
                    not isinstance(manifest, dict)
                    or not isinstance(manifest.get("media_type"), str)
                    or not isinstance(manifest.get("platforms"), list)
                    or not all(
                        isinstance(platform, str) for platform in manifest["platforms"]
                    )
                ):
                    raise RegistryError(
                        f"observed-images images[{index}].manifest is invalid"
                    )
                entry["observed_manifest"] = manifest
            entry["consumers"].extend(f"observed:{consumer}" for consumer in consumers)

    gaps = [gap for gap in gaps if gap["class"] not in observed_coverage]

    if (
        root / "flake/modules/k3s-server.nix"
    ).exists() and "k3s-bootstrap" not in observed_coverage:
        gaps.append(
            {
                "id": "k3s-bootstrap:flake/modules/k3s-server.nix",
                "class": "k3s-bootstrap",
                "consumer": "flake/modules/k3s-server.nix",
                "input": "k3s runtime defaults",
                "reason": "sandbox and packaged bootstrap image references are not rendered in source",
            }
        )
    if "live-workloads" not in observed_coverage:
        gaps.append(
            {
                "id": "live-workloads:cluster",
                "class": "live-workloads",
                "consumer": "cluster",
                "input": "Pods, init containers, ephemeral containers, Jobs, and CronJobs",
                "reason": "offline discovery does not contact the cluster; reconcile a fresh sanitized live snapshot",
            }
        )
    for entry in found.values():
        if entry["kind"] == "first-party":
            gaps.append(
                {
                    "id": f"producer:{entry['id']}",
                    "class": "producer-pipeline",
                    "consumer": ",".join(entry["consumers"]),
                    "input": entry["source"]["reference"],
                    "reason": "producer pipeline location and local-registry publication are not proven by this repository",
                    "blocking": False,
                }
            )

    entries = sorted(found.values(), key=lambda item: item["id"])
    for entry in entries:
        entry["consumers"] = sorted(set(entry["consumers"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "registry-inventory",
        "generated_at": _timestamp(),
        "source_revision": _source_revision(root),
        "scope": {
            "root": ".",
            "sources": [
                "gitops",
                "flake",
                ".gitlab-ci.yml",
                "registry/observed-images.json",
            ],
            "exhaustive": False,
        },
        "images": entries,
        "unresolved_inputs": sorted(gaps, key=lambda item: item["id"]),
    }


def _inventory_entry(reference: ImageReference) -> dict[str, Any]:
    kind = image_kind(reference)
    return {
        "id": _stable_id(reference, kind),
        "kind": kind,
        "source": {
            "registry": reference.registry,
            "repository": reference.repository,
            "tag": reference.tag,
            "digest": reference.digest,
            "reference": reference.canonical,
        },
        "destination_repository": destination_repository(reference, kind),
        "consumers": [],
        "producer": (
            {
                "owner": "jeff7712",
                "location": f"external-image-repository:{reference.registry}/{reference.repository}",
                "pipeline_status": "unverified",
            }
            if kind == "first-party"
            else None
        ),
        "retention_class": "deployed",
    }


def atomic_write_json(path: pathlib.Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_private(path: pathlib.Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _fsync_directory(path: pathlib.Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RegistryError(f"cannot read valid JSON from {path}: {error}") from error
    if not isinstance(value, dict):
        raise RegistryError(f"expected a JSON object in {path}")
    return value


def load_inventory(path: pathlib.Path) -> dict[str, Any]:
    value = read_json(path)
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("kind") != "registry-inventory"
    ):
        raise RegistryError("unsupported or invalid registry inventory schema")
    images = value.get("images")
    if not isinstance(images, list):
        raise RegistryError("inventory images must be an array")
    return value


def load_lock(path: pathlib.Path, *, allow_incomplete: bool = False) -> dict[str, Any]:
    value = read_json(path)
    errors = validate_lock(value, allow_incomplete=allow_incomplete)
    if errors:
        raise RegistryError("invalid image lock: " + "; ".join(errors))
    return value


def validate_lock(
    value: Mapping[str, Any], *, allow_incomplete: bool = False
) -> list[str]:
    errors: list[str] = []
    if value.get("schema_version") != SCHEMA_VERSION:
        errors.append("unknown schema_version")
    if value.get("kind") != "registry-image-lock":
        errors.append("kind must be registry-image-lock")
    destination_registry = value.get("destination_registry")
    try:
        _validate_registry(str(destination_registry))
    except RegistryError as error:
        errors.append(str(error))
    records = value.get("images")
    if not isinstance(records, list):
        return errors + ["images must be an array"]
    seen_ids: set[str] = set()
    tag_owners: dict[tuple[str, str], tuple[str, str]] = {}
    repository_sources: dict[str, tuple[str, str]] = {}
    for index, record in enumerate(records):
        prefix = f"images[{index}]"
        if not isinstance(record, dict):
            errors.append(f"{prefix} must be an object")
            continue
        identifier = record.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9.-]{0,127}", identifier
        ):
            errors.append(f"{prefix}.id is invalid")
        elif identifier in seen_ids:
            errors.append(f"duplicate id {identifier}")
        else:
            seen_ids.add(identifier)
        kind = record.get("kind")
        if kind not in {"first-party", "upstream"}:
            errors.append(f"{prefix}.kind is invalid")
        source = record.get("source")
        source_identity: tuple[str, str] | None = None
        if not isinstance(source, dict):
            errors.append(f"{prefix}.source must be an object")
        else:
            try:
                parsed = ImageReference.parse(str(source.get("reference", "")))
                if parsed.registry != source.get(
                    "registry"
                ) or parsed.repository != source.get("repository"):
                    errors.append(f"{prefix}.source fields disagree with reference")
                if parsed.digest != source.get("digest"):
                    errors.append(f"{prefix}.source digest disagrees with reference")
                source_identity = (parsed.registry, parsed.repository)
            except RegistryError as error:
                errors.append(f"{prefix}.source: {error}")
        digest = record.get("digest")
        if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
            errors.append(f"{prefix}.digest must be a sha256 digest")
        media_type = record.get("media_type")
        if not isinstance(media_type, str) or "/" not in media_type:
            errors.append(f"{prefix}.media_type is invalid")
        platforms = record.get("platforms")
        if not isinstance(platforms, list):
            errors.append(f"{prefix}.platforms must be an array")
        else:
            for platform in platforms:
                if not isinstance(platform, str) or not re.fullmatch(
                    r"[a-z0-9_-]+/[a-z0-9_.-]+(?:/[a-z0-9_.-]+)?", platform
                ):
                    errors.append(f"{prefix}.platforms contains an invalid platform")
        consumers = record.get("consumers")
        if (
            not isinstance(consumers, list)
            or not consumers
            or not all(isinstance(item, str) and item for item in consumers)
        ):
            errors.append(f"{prefix}.consumers must classify at least one consumer")
        if kind == "first-party":
            producer = record.get("producer")
            if (
                not isinstance(producer, dict)
                or not producer.get("owner")
                or not producer.get("location")
            ):
                errors.append(f"{prefix}.producer is required for first-party images")
        if record.get("retention_class") not in {"deployed", "rollback", "candidate"}:
            errors.append(f"{prefix}.retention_class is invalid")
        destination = record.get("destination_repository")
        if not isinstance(destination, str) or not _REPOSITORY_RE.fullmatch(
            destination
        ):
            errors.append(f"{prefix}.destination_repository is invalid")
        elif source_identity:
            previous = repository_sources.setdefault(destination, source_identity)
            if previous != source_identity:
                errors.append(
                    f"destination {destination} maps conflicting source identities"
                )
        tags = record.get("destination_tags")
        if not isinstance(tags, list) or not tags:
            errors.append(f"{prefix}.destination_tags must be a nonempty array")
        elif isinstance(destination, str) and isinstance(digest, str):
            for tag in tags:
                if not isinstance(tag, str) or not _TAG_RE.fullmatch(tag):
                    errors.append(f"{prefix}.destination_tags contains an invalid tag")
                    continue
                key = (destination, tag)
                owner = (str(identifier), digest)
                previous = tag_owners.setdefault(key, owner)
                if previous[1] != digest:
                    errors.append(
                        f"destination tag {destination}:{tag} has conflicting digests"
                    )
        referrers = record.get("referrers")
        if not isinstance(referrers, dict) or not isinstance(
            referrers.get("required"), list
        ):
            errors.append(f"{prefix}.referrers.required must be an array")
        authenticity = record.get("authenticity")
        if not isinstance(authenticity, dict) or authenticity.get("status") not in {
            "verified",
            "copied-unverified",
            "absent",
            "unsupported",
            "not-required",
        }:
            errors.append(f"{prefix}.authenticity.status is invalid")
    unresolved = value.get("unresolved_inputs", [])
    if not isinstance(unresolved, list):
        errors.append("unresolved_inputs must be an array")
    elif not allow_incomplete:
        blocking = [
            item
            for item in unresolved
            if not isinstance(item, dict) or item.get("blocking", True)
        ]
        if blocking:
            errors.append(f"{len(blocking)} blocking unresolved inputs remain")
    return errors


def _platforms(manifest: Mapping[str, Any]) -> list[str]:
    values: set[str] = set()
    descriptors = manifest.get("manifests", [])
    if not isinstance(descriptors, list):
        return []
    for descriptor in descriptors:
        if not isinstance(descriptor, dict):
            continue
        platform = descriptor.get("platform")
        if not isinstance(platform, dict):
            continue
        os_name = platform.get("os")
        architecture = platform.get("architecture")
        variant = platform.get("variant")
        if isinstance(os_name, str) and isinstance(architecture, str):
            value = f"{os_name}/{architecture}"
            if isinstance(variant, str) and variant:
                value += f"/{variant}"
            values.add(value)
    return sorted(values)


def _dest_auth_header() -> str | None:
    """The `Authorization` value the destination auth file supplies, if any."""
    variable = os.environ.get("REGISTRY_DEST_AUTH_FILE")
    if not variable:
        return None
    try:
        payload = json.loads(pathlib.Path(variable).read_text(encoding="utf-8"))
        config = payload["auths"][DEFAULT_REGISTRY]
    except (OSError, KeyError, TypeError, ValueError):
        return None
    auth = config.get("auth") if isinstance(config, dict) else None
    return f"Basic {auth}" if isinstance(auth, str) else None


def _registry_from_args(args: Sequence[str]) -> str | None:
    """The registry an skopeo invocation targets, from its `docker://` argument."""
    for argument in args:
        if argument.startswith("docker://"):
            remainder = argument.removeprefix("docker://")
            return remainder.split("/", 1)[0] or None
    return None


def _interstitial_reason(registry: str) -> str | None:
    """Describe why the registry cannot be trusted right now, or None if it can.

    A 401 from the registry's own auth challenge is healthy, so it is not
    reported. What must be caught is Cloudflare Access answering in zot's
    place, which surfaces as a redirect into its login flow or as a challenge
    header, and plain unreachability.
    """
    request = urllib.request.Request(f"https://{registry}/v2/", method="GET")
    header = _dest_auth_header()
    if header:
        request.add_header("Authorization", header)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=10) as response:
            del response
            return None
    except urllib.error.HTTPError as error:
        location = error.headers.get("Location", "") if error.headers else ""
        if error.code in {301, 302, 303, 307, 308}:
            if "/cdn-cgi/access" in location or "access/login" in location:
                return f"Cloudflare Access intercepted the request (redirected to {location[:120]})"
            return None
        if error.code == 403 and (error.headers or {}).get("cf-mitigated"):
            return "Cloudflare Access returned a challenge instead of registry data"
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return f"registry unreachable: {error}"


def _reference_from_args(args: Sequence[str]) -> tuple[str, str, str] | None:
    """Split a skopeo `docker://` argument into registry, repository, reference."""
    targets = [a for a in args if a.startswith("docker://")]
    if not targets:
        return None
    remainder = targets[-1].removeprefix("docker://")
    registry, _, path = remainder.partition("/")
    if not registry or not path:
        return None
    for separator in ("@", ":"):
        repository, found, reference = path.rpartition(separator)
        if found and repository and reference:
            return registry, repository, reference
    return registry, path, ""


def _spurious_failure(args: Sequence[str]) -> str | None:
    """Why a failed operation does not reflect the registry, or None if it does.

    Probing only `/v2/` is not enough: Cloudflare Access intercepts individual
    requests, so the base endpoint often answers cleanly while the manifest read
    does not. Asking for the exact repository and reference the operation failed
    on, with the same credential, is self-consistent: if that succeeds the
    registry is serving the content and the tool's failure was spurious.
    """
    parsed = _reference_from_args(args)
    if parsed is None:
        return None
    registry, repository, reference = parsed
    if not reference:
        return None
    request = urllib.request.Request(
        f"https://{registry}/v2/{repository}/manifests/{reference}", method="GET"
    )
    header = _dest_auth_header()
    if header:
        request.add_header("Authorization", header)
    for media_type in _INDEX_MEDIA_TYPES:
        request.add_header("Accept", media_type)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=15) as response:
            if response.status == 200:
                return (
                    f"{repository}:{reference} is served by {registry} when read "
                    "directly, so the tool's failure was not the registry's answer"
                )
    except urllib.error.HTTPError:
        # The registry itself rejected or lacks it, which is a real result.
        return None
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    return None


class OciClient:
    def __init__(self, *, timeout: int = 60, retries: int = 2) -> None:
        if timeout < 1 or retries < 0 or retries > 5:
            raise RegistryError("timeout and retry bounds are invalid")
        self.timeout = timeout
        self.retries = retries
        self.inspect_tool = (
            "skopeo"
            if shutil.which("skopeo")
            else "crane"
            if shutil.which("crane")
            else None
        )
        self.copy_tool = "skopeo" if shutil.which("skopeo") else None

    def raw_manifest(
        self,
        reference: str,
        *,
        destination: bool = False,
        timeout: float | None = None,
    ) -> bytes:
        if self.inspect_tool == "skopeo":
            args = ["skopeo", "--registries-conf", "/dev/null", "inspect", "--raw"]
            auth = os.environ.get(
                "REGISTRY_DEST_AUTH_FILE"
                if destination
                else "REGISTRY_SOURCE_AUTH_FILE"
            )
            if auth:
                args.extend(["--authfile", auth])
            args.append(f"docker://{reference}")
        elif self.inspect_tool == "crane":
            args = ["crane", "manifest", reference]
        else:
            raise RegistryError(
                "no supported OCI inspection tool found; install skopeo or crane"
            )
        return self._run(args, operation="manifest inspection", timeout=timeout)

    def list_tags(self, repository: str, *, destination: bool = False) -> list[str]:
        if self.inspect_tool != "skopeo":
            raise RegistryError("tag listing requires skopeo")
        args = ["skopeo", "--registries-conf", "/dev/null", "list-tags"]
        auth = os.environ.get(
            "REGISTRY_DEST_AUTH_FILE" if destination else "REGISTRY_SOURCE_AUTH_FILE"
        )
        if auth:
            args.extend(["--authfile", auth])
        args.append(f"docker://{repository}")
        raw = self._run(args, operation="tag listing")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise RegistryError(
                f"registry returned invalid tag list JSON for {repository}"
            ) from error
        tags = payload.get("Tags") if isinstance(payload, dict) else None
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise RegistryError(f"registry returned invalid tags for {repository}")
        return sorted(tags)

    def copy(
        self, source: str, destination: str, *, timeout: float | None = None
    ) -> None:
        if self.copy_tool != "skopeo":
            raise RegistryError("digest-preserving copy requires skopeo")
        args = [
            "skopeo",
            "--registries-conf",
            "/dev/null",
            "copy",
            "--all",
            "--insecure-policy",
            "--preserve-digests",
        ]
        source_auth = os.environ.get("REGISTRY_SOURCE_AUTH_FILE")
        destination_auth = os.environ.get("REGISTRY_DEST_AUTH_FILE")
        if source_auth:
            args.extend(["--src-authfile", source_auth])
        if destination_auth:
            args.extend(["--dest-authfile", destination_auth])
        args.extend([f"docker://{source}", f"docker://{destination}"])
        self._run(args, operation="digest-preserving copy", retries=0, timeout=timeout)

    def _run(
        self,
        args: Sequence[str],
        *,
        operation: str,
        retries: int | None = None,
        timeout: float | None = None,
    ) -> bytes:
        attempts = self.retries if retries is None else retries
        operation_timeout = self.timeout if timeout is None else timeout
        if operation_timeout <= 0:
            raise RegistryError(f"{operation} timed out")
        for attempt in range(attempts + 1):
            try:
                result = subprocess.run(
                    list(args),
                    capture_output=True,
                    timeout=operation_timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                if attempt == attempts:
                    raise RegistryError(
                        f"{operation} timed out after {operation_timeout:g} seconds"
                    ) from error
            else:
                if result.returncode == 0:
                    return result.stdout
                if attempt == attempts:
                    detail = result.stderr.decode(errors="replace").strip()
                    suffix = f": {detail}" if detail else ""
                    spurious = _spurious_failure(args)
                    if spurious is not None:
                        raise TransientRegistryError(
                            f"{operation} could not be trusted: {spurious}"
                        )
                    registry = _registry_from_args(args)
                    if registry is not None:
                        reason = _interstitial_reason(registry)
                        if reason is not None:
                            raise TransientRegistryError(
                                f"{operation} could not reach {registry}: {reason}"
                            )
                    raise RegistryError(
                        f"{operation} failed with exit code {result.returncode}{suffix}"
                    )
            time.sleep(min(2**attempt, 8))
        raise AssertionError("unreachable")


def manifest_details(
    client: OciClient, reference: str, *, destination: bool = False
) -> tuple[str, str, list[str]]:
    raw = client.raw_manifest(reference, destination=destination)
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RegistryError(
            f"registry returned invalid manifest JSON for {reference}"
        ) from error
    if not isinstance(manifest, dict):
        raise RegistryError(
            f"registry returned invalid manifest document for {reference}"
        )
    media_type = manifest.get("mediaType")
    if not isinstance(media_type, str):
        raise RegistryError(f"manifest lacks mediaType for {reference}")
    return digest, media_type, _platforms(manifest)


def resolve_inventory(
    inventory: Mapping[str, Any],
    client: OciClient,
    *,
    destination_registry: str = DEFAULT_REGISTRY,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    unresolved: list[dict[str, str]] = [
        dict(item) for item in inventory.get("unresolved_inputs", [])
    ]
    records: list[dict[str, Any]] = []
    for image in inventory["images"]:
        source = image["source"]
        reference = ImageReference.parse(source["reference"])
        original_reference = _decode_local_upstream_mirror(
            reference, destination_registry
        )
        if original_reference is not None:
            reference = original_reference
            source = {
                **source,
                "registry": reference.registry,
                "repository": reference.repository,
                "tag": reference.tag,
                "digest": reference.digest,
                "reference": reference.canonical,
            }
        try:
            observed_digest, media_type, platforms = manifest_details(
                client,
                reference.immutable if reference.digest else reference.canonical,
                destination=reference.registry == destination_registry,
            )
            if reference.digest and observed_digest != reference.digest:
                raise RegistryError(
                    f"source manifest digest mismatch for {reference.canonical}: expected {reference.digest}, observed {observed_digest}"
                )
            resolution = {"status": "source-registry-verified"}
        except RegistryError as error:
            observed = image.get("observed_manifest")
            if reference.digest and isinstance(observed, dict):
                observed_digest = reference.digest
                media_type = observed["media_type"]
                platforms = observed["platforms"]
                resolution = {
                    "status": "runtime-cache-verified",
                    "reason": str(error),
                }
            else:
                unresolved.append(
                    {
                        "id": f"resolve:{image['id']}",
                        "class": "source-resolution",
                        "consumer": ",".join(image["consumers"]),
                        "input": reference.canonical,
                        "reason": str(error),
                    }
                )
                continue
        digest = reference.digest or observed_digest
        immutable = ImageReference(
            reference.registry, reference.repository, reference.tag, digest
        )
        tags = []
        if reference.tag:
            tags.append(reference.tag)
        tags.append(
            f"retention-{image['retention_class']}-{digest.removeprefix('sha256:')[:16]}"
        )
        record = dict(image)
        if original_reference is not None:
            record["id"] = _stable_id(reference, str(image["kind"]))
            record["destination_repository"] = destination_repository(
                reference, str(image["kind"])
            )
        record.pop("observed_manifest", None)
        records.append(
            {
                **record,
                "source": {
                    **source,
                    "digest": digest,
                    "reference": immutable.canonical,
                },
                "digest": digest,
                "media_type": media_type,
                "platforms": platforms,
                "resolution": resolution,
                "destination_tags": sorted(set(tags)),
                "referrers": {"required": [], "source_status": "not-enumerated"},
                "authenticity": {
                    "status": "unsupported",
                    "reason": "no source verification policy is declared for this inventory record",
                },
            }
        )
    lock = {
        "schema_version": SCHEMA_VERSION,
        "kind": "registry-image-lock",
        "generated_at": _timestamp(),
        "source_revision": inventory.get("source_revision", "unavailable"),
        "destination_registry": destination_registry,
        "images": sorted(records, key=lambda item: item["id"]),
        "mirror_exceptions": [],
        "unresolved_inputs": sorted(unresolved, key=lambda item: item["id"]),
    }
    validation_errors = validate_lock(lock, allow_incomplete=True)
    if validation_errors:
        raise RegistryError(
            "resolved lock candidate is invalid: " + "; ".join(validation_errors)
        )
    return lock, unresolved


_PROMOTION_TAG_RE = re.compile(r"0\.0\.(\d+)\Z")
_TRACKED_MUTABLE_TAGS = frozenset({"latest"})


def promotion_version(tag: str | None) -> int | None:
    match = _PROMOTION_TAG_RE.fullmatch(tag or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def select_promotion_candidate(
    tags: Sequence[str], current_tag: str | None
) -> str | None:
    if current_tag in _TRACKED_MUTABLE_TAGS:
        return current_tag
    best: str | None = None
    best_version = -1
    for tag in tags:
        version = promotion_version(tag)
        if version is not None and version > best_version:
            best_version = version
            best = tag
    return best


def _migrate_record_source_to_local(
    client: OciClient, record: dict[str, Any], destination_registry: str
) -> bool:
    """Repoint a first-party record at the local registry after verification.

    The local registry is authoritative, so migration selects its newest release
    tag and preserves the previous digest until the normal promotion path
    verifies and applies the candidate. Returns False when the local registry
    has no usable release tag.
    """
    source = record["source"]
    if source.get("registry") == destination_registry:
        return False
    destination_repository_name = str(record["destination_repository"])
    local_repository = f"{destination_registry}/{destination_repository_name}"
    try:
        tags = client.list_tags(local_repository, destination=True)
    except RegistryError:
        return False
    candidate = select_promotion_candidate(tags, source.get("tag"))
    if candidate is None:
        return False
    record["source"] = {
        **source,
        "registry": destination_registry,
        "repository": destination_repository_name,
        "tag": candidate,
        "reference": f"{local_repository}:{candidate}@{source.get('digest')}",
    }
    return True


def promote_first_party_lock(
    client: OciClient,
    lock: dict[str, Any],
    *,
    only: set[str] | None = None,
) -> dict[str, Any]:
    """Promote first-party lock records to newest producer tags.

    Promotion is verification-only: the producer pipeline must already have
    published the candidate tag to the destination registry (only
    publisher-<project> holds a write grant there). Candidates the producer
    has not published are reported as skipped, never mirrored.

    Records still pointing at an external producer registry migrate to the
    local registry once the pinned digest is verified there, so GHCR remains
    a backup instead of a promotion dependency.
    """
    destination_registry = lock.get("destination_registry")
    if not isinstance(destination_registry, str) or not destination_registry:
        raise RegistryError("lock has no destination registry")
    promoted: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    migrated: list[str] = []
    records: list[dict[str, Any]] = []
    for record in lock["images"]:
        if record.get("kind") != "first-party" or (
            only is not None and record.get("destination_repository") not in only
        ):
            records.append(record)
            continue
        destination_repository_name = str(record["destination_repository"])
        if _migrate_record_source_to_local(client, record, destination_registry):
            migrated.append(destination_repository_name)
        source = record["source"]
        source_repository = f"{source['registry']}/{source['repository']}"
        source_is_local = source["registry"] == destination_registry
        try:
            tags = client.list_tags(source_repository, destination=source_is_local)
        except RegistryError as error:
            skipped.append(
                {
                    "destination_repository": destination_repository_name,
                    "reason": f"tag listing failed: {error}",
                }
            )
            records.append(record)
            continue
        candidate = select_promotion_candidate(tags, source.get("tag"))
        if candidate is None:
            skipped.append(
                {
                    "destination_repository": destination_repository_name,
                    "reason": "no promotion tags found",
                }
            )
            records.append(record)
            continue
        candidate_reference = f"{source_repository}:{candidate}"
        new_digest, media_type, platforms = _inspect_digest(
            client, candidate_reference, destination=source_is_local
        )
        previous_digest = str(record["digest"])
        if candidate == source.get("tag") and new_digest == previous_digest:
            skipped.append(
                {
                    "destination_repository": destination_repository_name,
                    "reason": f"already at {candidate_reference}",
                }
            )
            records.append(record)
            continue
        destination_tagged = (
            f"{destination_registry}/{destination_repository_name}:{candidate}"
        )
        try:
            observed_destination, _, _ = _inspect_digest(
                client, destination_tagged, destination=True
            )
        except RegistryError:
            observed_destination = None
        if observed_destination is None:
            skipped.append(
                {
                    "destination_repository": destination_repository_name,
                    "reason": (
                        f"producer has not published {candidate_reference} "
                        f"to {destination_tagged}"
                    ),
                }
            )
            records.append(record)
            continue
        if observed_destination != new_digest:
            skipped.append(
                {
                    "destination_repository": destination_repository_name,
                    "reason": (
                        f"refusing to overwrite {destination_tagged}: "
                        f"expected {new_digest}, observed {observed_destination}"
                    ),
                }
            )
            records.append(record)
            continue
        retention_class = str(record.get("retention_class", "deployed"))
        updated = dict(record)
        updated["source"] = {
            "registry": destination_registry,
            "repository": destination_repository_name,
            "tag": candidate,
            "digest": new_digest,
            "reference": f"{destination_tagged}@{new_digest}",
        }
        updated["digest"] = new_digest
        updated["media_type"] = media_type
        updated["platforms"] = platforms
        updated["destination_tags"] = sorted(
            {
                candidate,
                f"retention-{retention_class}-{new_digest.removeprefix('sha256:')[:16]}",
            }
        )
        updated["resolution"] = {"status": "source-registry-verified"}
        records.append(updated)
        promoted.append(
            {
                "destination_repository": destination_repository_name,
                "consumers": list(record.get("consumers", [])),
                "previous_tag": source.get("tag"),
                "tag": candidate,
                "previous_digest": previous_digest,
                "digest": new_digest,
            }
        )
    lock["images"] = sorted(records, key=lambda item: item["id"])
    if promoted or migrated:
        lock["generated_at"] = _timestamp()
    errors = validate_lock(lock)
    if errors:
        raise RegistryError("promoted lock is invalid: " + "; ".join(errors))
    return {"promoted": promoted, "skipped": skipped, "migrated": migrated}


def filter_transient_observed_errors(
    errors: Sequence[Mapping[str, str]], previous_digests: set[str]
) -> list[Mapping[str, str]]:
    """Drop live-observation errors that a just-landed promotion explains.

    The cluster keeps running the previous digest until Flux reconciles, so
    observed: consumers may legitimately pin a promoted record's previous
    digest. Anything else (file consumers, unknown digests) still fails.
    """
    remaining: list[Mapping[str, str]] = []
    for item in errors:
        _, _, digest = item["reference"].partition("@")
        if item["consumer"].startswith("observed:") and digest in previous_digests:
            continue
        remaining.append(item)
    return remaining


def rewrite_observed_digests(
    root: pathlib.Path,
    *,
    destination_registry: str,
    destination_repository: str,
    previous_digest: str,
    digest: str,
) -> int:
    """Advance explained live observations to a promoted digest.

    The cluster converges on rollout; without this the pre-rollout snapshot
    fails policy checks on the just-landed lock. Unknown digests are never
    touched, so genuine drift still fails the gate.
    """
    if previous_digest == digest:
        return 0
    path = root / "registry/observed-images.json"
    if not path.exists():
        return 0
    old = f"{destination_registry}/{destination_repository}@{previous_digest}"
    new = f"{destination_registry}/{destination_repository}@{digest}"
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count:
        path.write_text(text.replace(old, new), encoding="utf-8")
    return count


def rewrite_consumer_digests(
    root: pathlib.Path,
    consumers: Sequence[str],
    *,
    destination_registry: str,
    destination_repository: str,
    previous_digest: str,
    digest: str,
) -> list[str]:
    if previous_digest == digest:
        return []
    old = f"{destination_registry}/{destination_repository}@{previous_digest}"
    new = f"{destination_registry}/{destination_repository}@{digest}"
    updated: list[str] = []
    consumers = list(consumers)
    for image in discover_inventory(root)["images"]:
        if image["source"]["reference"] == old:
            consumers.extend(image["consumers"])
    handled: set[str] = set()
    for consumer in dict.fromkeys(consumers):
        if consumer.startswith("observed:"):
            continue
        relative, _, _ = consumer.partition(":")
        if relative in handled:
            continue
        path = root / relative
        text = path.read_text(encoding="utf-8")
        if old not in text:
            raise RegistryError(f"consumer {consumer} no longer pins {old}")
        path.write_text(text.replace(old, new), encoding="utf-8")
        handled.add(relative)
        updated.append(relative)
    return updated


def copy_plan(lock: Mapping[str, Any]) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    registry = lock["destination_registry"]
    for record in sorted(lock["images"], key=lambda item: item["id"]):
        source = record["source"]
        destination = f"{registry}/{record['destination_repository']}"
        steps.append(
            {
                "id": record["id"],
                "source": f"{source['registry']}/{source['repository']}@{record['digest']}",
                "destination": f"{destination}@{record['digest']}",
                "destination_tags": record["destination_tags"],
                "expected_digest": record["digest"],
                "media_type": record["media_type"],
                "platforms": record["platforms"],
                "required_referrers": record["referrers"]["required"],
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "registry-copy-plan",
        "generated_at": _timestamp(),
        "source_revision": lock.get("source_revision", "unavailable"),
        "destination_registry": registry,
        "steps": steps,
    }


def _inspect_digest(
    client: OciClient,
    reference: str,
    *,
    destination: bool,
    timeout: float | None = None,
) -> tuple[str, str, list[str]]:
    if isinstance(client, OciClient):
        raw = client.raw_manifest(reference, destination=destination, timeout=timeout)
    else:
        raw = client.raw_manifest(reference, destination=destination)
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RegistryError(
            f"registry returned invalid manifest JSON for {reference}"
        ) from error
    media_type = manifest.get("mediaType")
    if not isinstance(media_type, str):
        raise RegistryError(f"manifest lacks mediaType for {reference}")
    return digest, media_type, _platforms(manifest)


def verify_record(
    client: OciClient,
    lock: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    timeout: float | None = None,
) -> dict[str, Any]:
    destination = f"{lock['destination_registry']}/{record['destination_repository']}@{record['digest']}"
    observed, media_type, platforms = _inspect_digest(
        client, destination, destination=True, timeout=timeout
    )
    errors: list[str] = []
    if observed != record["digest"]:
        errors.append(
            f"digest mismatch: expected {record['digest']}, observed {observed}"
        )
    if media_type != record["media_type"]:
        errors.append(
            f"media type mismatch: expected {record['media_type']}, observed {media_type}"
        )
    # Platforms are only derivable from an index. A single-arch manifest
    # carries no `manifests` list, so its observed platforms are always empty
    # while the lock may still record the platform it was built for.
    if media_type in _INDEX_MEDIA_TYPES and platforms != record["platforms"]:
        errors.append(
            f"platform mismatch: expected {record['platforms']}, observed {platforms}"
        )
    referrer_outcomes: list[dict[str, Any]] = []
    for descriptor in record["referrers"]["required"]:
        digest = descriptor.get("digest") if isinstance(descriptor, dict) else None
        if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
            errors.append("required referrer has an invalid digest")
            continue
        ref = f"{lock['destination_registry']}/{record['destination_repository']}@{digest}"
        try:
            observed_referrer, _, _ = _inspect_digest(
                client, ref, destination=True, timeout=timeout
            )
            matched = observed_referrer == digest
        except TransientRegistryError:
            # An unreachable registry says nothing about whether the referrer
            # is there, so it must not be recorded as a missing referrer.
            raise
        except RegistryError:
            observed_referrer = None
            matched = False
        referrer_outcomes.append(
            {
                "expected_digest": digest,
                "observed_digest": observed_referrer,
                "matched": matched,
            }
        )
        if not matched:
            errors.append(f"required referrer missing or mismatched: {digest}")

    tag_outcomes: list[dict[str, Any]] = []
    for tag in record["destination_tags"]:
        reference = (
            f"{lock['destination_registry']}/{record['destination_repository']}:{tag}"
        )
        try:
            observed_tag, _, _ = _inspect_digest(
                client, reference, destination=True, timeout=timeout
            )
        except TransientRegistryError:
            # Same reasoning as the referrer probe: a registry Cloudflare is
            # intercepting would otherwise be reported as a missing tag, which
            # is the exact false drift this check exists to catch.
            raise
        except RegistryError:
            observed_tag = None
        matched = observed_tag == record["digest"]
        tag_outcomes.append(
            {
                "tag": tag,
                "expected_digest": record["digest"],
                "observed_digest": observed_tag,
                "matched": matched,
            }
        )
        if not matched:
            # A retention tag pointing at the wrong manifest is how the ledfx
            # index became untagged and collectable, and the digest check above
            # cannot see it, so the tag target is compared directly.
            detail = "missing" if observed_tag is None else f"points at {observed_tag}"
            errors.append(
                f"destination tag {tag} {detail}, expected {record['digest']}"
            )

    return {
        "id": record["id"],
        "status": "verified" if not errors else "failed",
        "destination": destination,
        "expected_digest": record["digest"],
        "observed_digest": observed,
        "platforms": {"expected": record["platforms"], "observed": platforms},
        "referrers": referrer_outcomes,
        "tags": tag_outcomes,
        "errors": errors,
    }


def reconcile_retention(
    client: OciClient,
    lock: Mapping[str, Any],
    *,
    kind: str | None = None,
    image_timeout: float = 900,
    dry_run: bool = False,
    progress: Any = None,
) -> dict[str, Any]:
    """Ensure every lock-listed retention tag resolves to the locked digest.

    The lock declares the tags a deployed digest must stay reachable by. For
    upstream images the copy step creates them, but `apps/**` is writable only
    by the per-project publishers, so the importer cannot and the first-party
    tags were never created. This creates the missing ones from the manifest
    already present in the destination, so no blob is re-uploaded.

    It is deliberately additive. A tag that already matches is left alone, and
    a tag that exists but resolves to a different manifest is reported as a
    conflict and never overwritten: that is the ledfx failure, and clobbering
    it would destroy the only evidence of what the tag used to point at.
    """
    if progress is None:
        progress = _ignore_progress
    registry = lock["destination_registry"]
    outcomes: list[dict[str, Any]] = []
    errors: list[str] = []
    for record in sorted(lock["images"], key=lambda item: item["id"]):
        if kind is not None and record["kind"] != kind:
            continue
        tags = [
            tag for tag in record["destination_tags"] if tag.startswith("retention-")
        ]
        if not tags:
            continue
        base = f"{registry}/{record['destination_repository']}"
        for tag in tags:
            tagged = f"{base}:{tag}"
            started = time.monotonic()
            try:
                observed, _, _ = _inspect_digest(client, tagged, destination=True)
            except RegistryError:
                observed = None
            if observed == record["digest"]:
                status = "present"
            elif observed is None:
                status = "would-create" if dry_run else "created"
                if not dry_run:
                    remaining = max(1.0, image_timeout - (time.monotonic() - started))
                    try:
                        _copy_with_timeout(
                            client, f"{base}@{record['digest']}", tagged, remaining
                        )
                    except RegistryError as error:
                        status = "failed"
                        errors.append(f"{record['id']} {tag}: {error}")
                progress(f"{status} {record['id']} tag={tag}")
            else:
                status = "conflict"
                errors.append(
                    f"{record['id']} {tag} resolves to {observed}, expected "
                    f"{record['digest']}; left untouched"
                )
                progress(f"conflict {record['id']} tag={tag}")
            outcomes.append(
                {
                    "id": record["id"],
                    "kind": record["kind"],
                    "tag": tag,
                    "status": status,
                    "expected_digest": record["digest"],
                    "observed_digest": observed,
                }
            )
    counts: dict[str, int] = {}
    for outcome in outcomes:
        counts[outcome["status"]] = counts.get(outcome["status"], 0) + 1
    return operation_report(
        "reconcile-retention",
        lock,
        outcomes,
        status_override="failed" if errors else "ok",
        summary={"counts": counts, "errors": sorted(errors)},
    )


def verify_lock(
    client: OciClient, lock: Mapping[str, Any], *, kind: str | None = None
) -> dict[str, Any]:
    outcomes: list[dict[str, Any]] = []
    for record in sorted(lock["images"], key=lambda item: item["id"]):
        if kind is not None and record["kind"] != kind:
            continue
        try:
            outcome = verify_record(client, lock, record)
        except TransientRegistryError as error:
            outcome = {
                "id": record["id"],
                "status": "indeterminate",
                "destination": f"{lock['destination_registry']}/{record['destination_repository']}@{record['digest']}",
                "expected_digest": record["digest"],
                "observed_digest": None,
                "platforms": {"expected": record["platforms"], "observed": []},
                "referrers": [],
                "tags": [],
                "errors": [str(error)],
            }
        except RegistryError as error:
            outcome = {
                "id": record["id"],
                "status": "failed",
                "destination": f"{lock['destination_registry']}/{record['destination_repository']}@{record['digest']}",
                "expected_digest": record["digest"],
                "observed_digest": None,
                "platforms": {"expected": record["platforms"], "observed": []},
                "referrers": [],
                "tags": [],
                "errors": [str(error)],
            }
        outcomes.append(outcome)
    return operation_report("verify", lock, outcomes)


def _copy_with_timeout(
    client: OciClient, source: str, destination: str, timeout: float
) -> None:
    if isinstance(client, OciClient):
        client.copy(source, destination, timeout=timeout)
    else:
        client.copy(source, destination)


def _copy_one(
    client: OciClient,
    lock: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    image_timeout: float,
    progress: Any,
) -> dict[str, Any]:
    started = time.monotonic()
    destination_base = (
        f"{lock['destination_registry']}/{record['destination_repository']}"
    )
    source = f"{record['source']['registry']}/{record['source']['repository']}@{record['digest']}"
    status = "reused"
    errors: list[str] = []
    observed_digest: str | None = None

    def remaining() -> float:
        value = image_timeout - (time.monotonic() - started)
        if value <= 0:
            raise RegistryError(f"image copy timed out after {image_timeout:g} seconds")
        return value

    try:
        progress(f"started {record['id']}")
        for tag in record["destination_tags"]:
            tagged_destination = f"{destination_base}:{tag}"
            try:
                observed, _, _ = _inspect_digest(
                    client,
                    tagged_destination,
                    destination=True,
                    timeout=remaining(),
                )
            except RegistryError:
                _copy_with_timeout(client, source, tagged_destination, remaining())
                status = "copied"
                progress(f"copied {record['id']} tag={tag}")
            else:
                if observed != record["digest"]:
                    observed_digest = observed
                    raise RegistryError(
                        f"refusing to overwrite conflicting destination tag "
                        f"{record['destination_repository']}:{tag}: expected "
                        f"{record['digest']}, observed {observed}"
                    )
        for descriptor in record["referrers"]["required"]:
            digest = descriptor["digest"]
            referrer_tag = f"referrer-{digest.removeprefix('sha256:')[:16]}"
            referrer_destination = f"{destination_base}:{referrer_tag}"
            try:
                observed, _, _ = _inspect_digest(
                    client,
                    referrer_destination,
                    destination=True,
                    timeout=remaining(),
                )
            except RegistryError:
                _copy_with_timeout(
                    client,
                    f"{record['source']['registry']}/{record['source']['repository']}@{digest}",
                    referrer_destination,
                    remaining(),
                )
                status = "copied"
                progress(f"copied {record['id']} referrer={digest}")
            else:
                if observed != digest:
                    observed_digest = observed
                    raise RegistryError(
                        f"refusing to overwrite conflicting referrer "
                        f"{record['destination_repository']}:{referrer_tag}: expected "
                        f"{digest}, observed {observed}"
                    )
        verification = verify_record(client, lock, record, timeout=remaining())
        if verification["status"] != "verified":
            errors.extend(verification["errors"])
    except (RegistryError, KeyError, TypeError) as error:
        errors.append(str(error))
    outcome = {
        "id": record["id"],
        "status": "failed" if errors else status,
        "source": source,
        "destination": f"{destination_base}@{record['digest']}",
        "expected_digest": record["digest"],
        "observed_digest": (
            observed_digest
            if observed_digest is not None
            else (record["digest"] if not errors else None)
        ),
        "platforms": {
            "expected": record["platforms"],
            "observed": record["platforms"] if not errors else [],
        },
        "referrers": [],
        "errors": errors,
    }
    progress(f"{'failed' if errors else 'finished'} {record['id']}")
    return outcome


def _ignore_progress(message: str) -> None:
    del message


def copy_lock(
    client: OciClient,
    lock: Mapping[str, Any],
    *,
    kind: str | None = None,
    concurrency: int = 3,
    image_timeout: float = 900,
    progress: Any = None,
) -> dict[str, Any]:
    if not 1 <= concurrency <= 8:
        raise RegistryError("copy concurrency must be between 1 and 8")
    if image_timeout <= 0:
        raise RegistryError("image timeout must be positive")
    if progress is None:
        progress = _ignore_progress

    records = [
        record
        for record in sorted(lock["images"], key=lambda item: item["id"])
        if kind is None or record["kind"] == kind
    ]
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(
                _copy_one,
                client,
                lock,
                record,
                image_timeout=image_timeout,
                progress=progress,
            ): record
            for record in records
        }
        outcomes = [future.result() for future in as_completed(futures)]
    outcomes.sort(key=lambda item: item["id"])
    return operation_report("copy", lock, outcomes)


def operation_report(
    command: str,
    lock: Mapping[str, Any],
    outcomes: list[dict[str, Any]],
    *,
    status_override: str | None = None,
    summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    failures = sum(item["status"] == "failed" for item in outcomes)
    indeterminate = sum(item["status"] == "indeterminate" for item in outcomes)
    base = {
        "total": len(outcomes),
        "failed": failures,
        "indeterminate": indeterminate,
    }
    if summary:
        base.update(summary)
    if status_override is None:
        if failures:
            status = "failed"
        elif indeterminate:
            status = "indeterminate"
        else:
            status = "ok"
    else:
        status = status_override
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "registry-operation-report",
        "command": command,
        "generated_at": _timestamp(),
        "source_revision": lock.get("source_revision", "unavailable"),
        "status": status,
        "summary": base,
        "images": outcomes,
    }


def check_consumers(root: pathlib.Path, lock: Mapping[str, Any]) -> dict[str, Any]:
    inventory = discover_inventory(root)
    allowed = {
        (registry, repository, digest)
        for record in lock["images"]
        for registry, repository in (
            (record["source"]["registry"], record["source"]["repository"]),
            (lock["destination_registry"], record["destination_repository"]),
        )
        for digest in (record["digest"],)
    }
    exceptions = lock.get("mirror_exceptions", [])
    errors: list[dict[str, str]] = []
    for image in inventory["images"]:
        reference = image["source"]["reference"]
        parsed = ImageReference.parse(reference)
        if (
            parsed.digest is not None
            and (parsed.registry, parsed.repository, parsed.digest) in allowed
        ):
            continue
        for consumer in image["consumers"]:
            if _matches_exception(reference, consumer, exceptions):
                continue
            errors.append(
                {
                    "consumer": consumer,
                    "reference": reference,
                    "error": "consumer is not an exact locked local digest and has no tested mirror exception",
                }
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "registry-policy-report",
        "generated_at": _timestamp(),
        "source_revision": inventory["source_revision"],
        "status": "ok" if not errors else "failed",
        "checked_consumers": sum(
            len(item["consumers"]) for item in inventory["images"]
        ),
        "errors": sorted(
            errors, key=lambda item: (item["consumer"], item["reference"])
        ),
        "discovery_gaps": inventory["unresolved_inputs"],
    }


def _matches_exception(reference: str, consumer: str, exceptions: Any) -> bool:
    if not isinstance(exceptions, list):
        return False
    for exception in exceptions:
        if not isinstance(exception, dict):
            continue
        if (
            exception.get("source_reference") == reference
            and isinstance(exception.get("consumer"), str)
            and fnmatch.fnmatchcase(consumer, exception["consumer"])
            and exception.get("tested") is True
            and isinstance(exception.get("evidence"), str)
            and exception["evidence"]
            and isinstance(exception.get("reason"), str)
            and exception["reason"]
        ):
            return True
    return False


def render_node_config(lock: Mapping[str, Any], username: str, password: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._@+-]{1,128}", username):
        raise RegistryError("registry username contains unsupported characters")
    if not password or "\x00" in password or "\n" in password or "\r" in password:
        raise RegistryError("registry password file must contain one nonempty line")
    destination_registry = str(lock["destination_registry"])
    mappings: dict[str, dict[str, str]] = {}
    for record in lock["images"]:
        source = record["source"]
        registry = source["registry"]
        repository = source["repository"]
        destination = record["destination_repository"]
        if registry == destination_registry and repository == destination:
            continue
        previous = mappings.setdefault(registry, {}).setdefault(repository, destination)
        if previous != destination:
            raise RegistryError(
                f"source repository {registry}/{repository} has conflicting rewrite destinations"
            )

    lines = ["mirrors:"]
    for registry in sorted(mappings):
        lines.extend(
            [
                f"  {json.dumps(registry)}:",
                "    endpoint:",
                f"      - {json.dumps('https://' + destination_registry)}",
                "    rewrite:",
            ]
        )
        for repository, destination in sorted(mappings[registry].items()):
            pattern = "^" + re.escape(repository) + "$"
            lines.append(f"      {json.dumps(pattern)}: {json.dumps(destination)}")
    lines.extend(
        [
            "configs:",
            f"  {json.dumps(destination_registry)}:",
            "    auth:",
            f"      username: {json.dumps(username)}",
            f"      password: {json.dumps(password)}",
            "    tls:",
            "      insecure_skip_verify: false",
            "",
        ]
    )
    return "\n".join(lines)


def render_access_control(lock: Mapping[str, Any]) -> dict[str, Any]:
    read = ["read"]
    write = ["read", "create", "update"]
    administer = ["read", "create", "update", "delete"]
    repositories: dict[str, Any] = {
        "**": {"defaultPolicy": []},
        "apps/**": {
            "policies": [
                {"users": ["node"], "actions": read},
            ],
            "defaultPolicy": [],
        },
        "upstream/**": {
            "policies": [
                {"users": ["node"], "actions": read},
                {"users": ["importer"], "actions": write},
            ],
            "defaultPolicy": [],
        },
    }
    for record in sorted(
        lock["images"], key=lambda item: item["destination_repository"]
    ):
        if record["kind"] != "first-party":
            continue
        repository = record["destination_repository"]
        project = repository.removeprefix("apps/")
        repositories[repository] = {
            "policies": [
                {"users": ["node"], "actions": read},
                {"users": [f"publisher-{project}"], "actions": write},
            ],
            "defaultPolicy": [],
        }
    return {
        "repositories": repositories,
        "adminPolicy": {"users": ["maintenance"], "actions": administer},
    }


def parse_htpasswd(content: str) -> dict[str, str]:
    """Map user to hash from an htpasswd file, ignoring blanks and comments."""
    entries: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        user, separator, value = stripped.partition(":")
        if not separator or not user:
            continue
        entries[user] = value
    return entries


def _auth_file_user(auth: str) -> str:
    decoded = base64.b64decode(auth).decode()
    user, separator, _ = decoded.partition(":")
    if not separator:
        raise RegistryError("registry auth entry is not base64 of user:password")
    return user


def _verify_htpasswd(path: pathlib.Path, user: str, password: str) -> bool:
    """Ask `htpasswd` whether the password matches, keeping it off argv.

    Exit 0 matches, 3 is a mismatch and 6 an unknown user; anything else means
    the file or the hash is not usable and the caller reports it rather than
    treating it as a pass.
    """
    result = subprocess.run(
        ["htpasswd", "-v", str(path), user],
        input=password + "\n",
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return True
    if result.returncode in (3, 6):
        return False
    raise RegistryError(
        f"htpasswd could not verify {user}: {result.stderr.strip() or result.returncode}"
    )


def _verify_one(
    errors: list[dict[str, str]],
    checked: list[dict[str, str]],
    orphans: list[dict[str, str]],
    path: pathlib.Path,
    entries: Mapping[str, str],
    variable: str,
    name: str,
    registry: str,
    user: str,
    password: str,
    expected_users: set[str] | None,
) -> None:
    """Record one auth entry's outcome against the htpasswd."""
    field = {"variable": variable, "registry": registry, "user": user}
    if user not in entries:
        # A variable naming an account the lock never grants a role to is
        # leftover from a retired identity, which is cleanup rather than an
        # outage. One the policy does grant a role to would break whichever job
        # needs it, so that stays fatal.
        if expected_users is not None and user not in expected_users:
            orphans.append(
                {
                    **field,
                    "reason": (
                        f"{name} auth file names {user}, which the access-control "
                        f"policy never grants a role to; delete the variable"
                    ),
                }
            )
            return
        errors.append(
            {
                **field,
                "error": f"{name} auth file has no htpasswd entry for {user}",
            }
        )
        return
    if not _HASH_RE.match(entries[user]):
        errors.append(
            {
                **field,
                "error": (
                    f"htpasswd entry for {user} is not a usable hash; repair it "
                    f"before trusting the comparison"
                ),
            }
        )
        return
    try:
        matches = _verify_htpasswd(path, user, password)
    except RegistryError as error:
        errors.append({**field, "error": str(error)})
        return
    if not matches:
        errors.append(
            {
                **field,
                "error": (
                    f"{name} auth file password does not match the htpasswd entry "
                    f"for {user}; the two are rotated together or not at all"
                ),
            }
        )
        return
    checked.append(field)


def access_control_users(lock: Mapping[str, Any]) -> set[str]:
    """Every account the rendered policy grants a role to."""
    policy = render_access_control(lock)
    users: set[str] = set(policy.get("adminPolicy", {}).get("users", []))
    for repository in policy.get("repositories", {}).values():
        for rule in repository.get("policies", []):
            users.update(rule.get("users", []))
    return users


def check_auth_consistency(
    htpasswd_content: str,
    auth_files: Mapping[str, str],
    expected_users: set[str] | None = None,
) -> dict[str, Any]:
    """Verify each registry auth file's password still matches the htpasswd.

    zot authenticates against the htpasswd, but CI authenticates with the
    separate `REGISTRY_*_AUTH_FILE` variables that a password rotation has to
    update in lockstep. When they drift the only symptom is a job failing with
    a bare 401, which is indistinguishable from a network fault, so verify the
    pairs offline instead.

    Auth files whose user has no htpasswd entry are errors. The reverse, an
    htpasswd user with no auth file, is not: some accounts are only ever used
    by hand.
    """
    entries = parse_htpasswd(htpasswd_content)
    errors: list[dict[str, str]] = []
    checked: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    orphans: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory() as directory:
        path = pathlib.Path(directory) / "htpasswd"
        path.write_text(htpasswd_content, encoding="utf-8")
        path.chmod(0o600)
        for variable in sorted(auth_files):
            name = variable.removeprefix("REGISTRY_").removesuffix("_AUTH_FILE").lower()
            try:
                payload = json.loads(auth_files[variable])
                auths = payload["auths"]
                if not isinstance(auths, dict) or not auths:
                    raise RegistryError("auth file has no auths entries")
            except (
                KeyError,
                TypeError,
                ValueError,
                RegistryError,
                json.JSONDecodeError,
            ) as error:
                errors.append(
                    {"variable": variable, "error": f"unreadable auth file: {error}"}
                )
                continue
            for registry, config in sorted(auths.items()):
                # The htpasswd authenticates zot, so only destination-registry
                # entries can be compared against it. A source auth file's
                # upstream credentials are somebody else's accounts and are
                # deliberately not here to be checked.
                if registry != DEFAULT_REGISTRY:
                    skipped.append(
                        {
                            "variable": variable,
                            "registry": registry,
                            "reason": "not the destination registry",
                        }
                    )
                    continue
                try:
                    user = _auth_file_user(config["auth"])
                    password = (
                        base64.b64decode(config["auth"]).decode().partition(":")[2]
                    )
                except (
                    KeyError,
                    TypeError,
                    ValueError,
                    RegistryError,
                    binascii.Error,
                ) as error:
                    errors.append(
                        {
                            "variable": variable,
                            "registry": registry,
                            "error": f"unreadable auth entry: {error}",
                        }
                    )
                    continue
                _verify_one(
                    errors,
                    checked,
                    orphans,
                    path,
                    entries,
                    variable,
                    name,
                    registry,
                    user,
                    password,
                    expected_users,
                )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "registry-auth-consistency-report",
        "generated_at": _timestamp(),
        "status": "ok" if not errors else "failed",
        "checked_accounts": checked,
        "skipped_entries": sorted(
            skipped, key=lambda item: (item["variable"], item["registry"])
        ),
        "orphaned_accounts": orphans,
        "htpasswd_users": sorted(entries),
        "errors": errors,
    }
