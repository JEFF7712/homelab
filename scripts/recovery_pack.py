from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any

REQUIREMENTS = frozenset(
    {
        "sources",
        "pilot-images",
        "bootstrap-images",
        "host-closures",
        "recovery-tools",
        "installer",
        "platform-state",
        "credential-escrow",
        "charts",
        "drill-configuration",
        "current-ci-inputs",
    }
)
CONDITIONS = frozenset(
    {
        "decryption-access",
        "installer-boot",
        "independent-copy",
        "independent-host",
        "dependency-review",
    }
)


def confined_file(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or parts.is_absolute()
        or any(p in {".", ".."} for p in relative.split("/"))
    ):
        raise ValueError("Invalid checkpoint-relative file path")
    path = root
    for part in parts.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("Checkpoint entries cannot traverse symlinks")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Checkpoint entry is not a regular file")
    return path


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_oci(root: Path, relative: str, expected: list[str]) -> int:
    layout = root / relative
    index = confined_file(root, relative + "/index.json")
    blobs = layout / "blobs/sha256"
    verified: set[str] = set()

    def visit(descriptor: dict[str, Any], manifest: bool) -> None:
        digest = descriptor["digest"]
        if not isinstance(digest, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", digest
        ):
            raise ValueError("OCI descriptor requires an exact SHA-256")
        size = descriptor["size"]
        if type(size) is not int or size < 0:
            raise ValueError("Invalid OCI descriptor size")
        path = confined_file(root, relative + "/blobs/sha256/" + digest[7:])
        if path.stat().st_size != size or file_digest(path) != digest[7:]:
            raise ValueError("OCI descriptor content does not match")
        if digest in verified:
            return
        verified.add(digest)
        if manifest:
            if size > 8 * 1024 * 1024:
                raise ValueError("OCI manifest is too large")
            data = json.loads(path.read_text())
            if (
                not isinstance(data, dict)
                or data.get("schemaVersion") != 2
                or not (
                    isinstance(data.get("manifests"), list)
                    and data["manifests"]
                    or isinstance(data.get("config"), dict)
                    and isinstance(data.get("layers"), list)
                )
            ):
                raise ValueError("Invalid OCI image or index structure")
            for child in data.get("manifests", []):
                visit(child, True)
            if "config" in data:
                visit(data["config"], False)
                for child in data["layers"]:
                    visit(child, False)

    descriptors = json.loads(index.read_text())["manifests"]
    if not descriptors or not expected:
        raise ValueError("OCI layout needs index and original registry roots")
    for descriptor in descriptors:
        visit(descriptor, True)
    for digest in expected:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("Invalid original OCI root")
        path = confined_file(root, relative + "/blobs/sha256/" + digest[7:])
        visit({"digest": digest, "size": path.stat().st_size}, True)
    for path in blobs.iterdir():
        if not re.fullmatch(r"[0-9a-f]{64}", path.name):
            raise ValueError("Invalid OCI blob filename")
        path = confined_file(root, relative + "/blobs/sha256/" + path.name)
        if file_digest(path) != path.name:
            raise ValueError("OCI blob filename does not match content")
    return len(verified)


def audit(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    if (
        type(manifest.get("schema_version")) is not int
        or manifest["schema_version"] != 1
    ):
        raise ValueError("Unsupported recovery checkpoint schema")
    errors: list[str] = []
    assets: dict[str, dict[str, Any]] = {}
    verified: list[str] = []
    for asset in manifest["assets"]:
        identity = asset["id"]
        if not isinstance(identity, str) or not identity or identity in assets:
            raise ValueError("Checkpoint asset IDs must be unique nonempty strings")
        assets[identity] = asset
        try:
            if not asset["files"]:
                raise ValueError("Asset has no files")
            seen = set()
            for entry in asset["files"]:
                if entry["path"] in seen:
                    raise ValueError("Duplicate asset file path")
                seen.add(entry["path"])
                path = confined_file(root, entry["path"])
                if (
                    type(entry["bytes"]) is not int
                    or path.stat().st_size != entry["bytes"]
                    or file_digest(path) != entry["sha256"]
                ):
                    raise ValueError("File size or SHA-256 mismatch")
            if "oci" in asset:
                verify_oci(root, asset["oci"]["path"], asset["oci"]["roots"])
            verified.append(identity)
        except (KeyError, OSError, ValueError, TypeError) as exc:
            errors.append(f"{identity}: {type(exc).__name__}: {exc}")

    blocked: dict[str, list[str]] = {}
    declared = manifest.get("requirements", {})
    for name in sorted(REQUIREMENTS | declared.keys()):
        requirement = declared.get(name, {})
        missing = list(requirement.get("missing", []))
        references = requirement.get("assets", [])
        if not references:
            missing.append("No required assets declared")
        for identity in references:
            if identity not in verified:
                missing.append(f"Required asset not verified: {identity}")
        if missing:
            blocked[name] = missing
    conditions = manifest.get("conditions", {})
    for name in sorted(CONDITIONS | conditions.keys()):
        condition = conditions.get(name, {})
        evidence = condition.get("evidence_asset")
        if condition.get("satisfied") is not True or evidence not in verified:
            blocked[name] = [condition.get("reason", "No verified acceptance evidence")]
    return {
        "schema_version": 1,
        "checked_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "integrity_valid": not errors,
        "declared_inputs_complete": not errors and not blocked,
        "verified_assets": verified,
        "errors": errors,
        "blocked_requirements": blocked,
        "limitation": "Integrity and declared coverage only; does not prove a restore, dependency closure or external custody",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a retained recovery checkpoint")
    parser.add_argument("root", type=Path)
    parser.add_argument("--manifest", default="manifest.json")
    args = parser.parse_args()
    try:
        path = confined_file(args.root.resolve(), args.manifest)
        report = audit(args.root.resolve(), json.loads(path.read_text()))
        report["manifest_sha256"] = file_digest(path)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        report = {"integrity_valid": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("declared_inputs_complete") else 2


if __name__ == "__main__":
    raise SystemExit(main())
