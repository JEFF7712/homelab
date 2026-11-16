"""Publish the orchestrator's Nix-built images from a protected CI operation.

`python -m scripts.ci.orchestrator_release coordinate` runs in the
`orchestrator-publish` operation on the validated main commit. For each image
it builds the flake package, pushes the result by a new pipeline tag with that
repository's scoped publisher (never a shared or administrator credential),
verifies the published runtime contract and digest lookup, and adds the
retention tag. It then prints a promotion patch, which `just ship` merges as
its own PR, pinning both digests in config/orchestrator/images.json and
inserting or updating their first-party registry records.

Nothing here deploys: the orchestrator's manifests are rendered from the
pinned digests only when a deployment is requested.
"""

from __future__ import annotations

import argparse
import base64
import copy
import difflib
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.ci.service_release import (
    REGISTRY,
    Release,
    preflight,
    registry_get,
    require_new_tag,
    retain_publication,
)

PINS = "config/orchestrator/images.json"
LOCK = "registry/images.lock.json"
INVENTORY = "registry/images.inventory.json"
PRODUCER = "flake/packages/orchestrator-images.nix"
OWNER = "JEFF7712/homelab"
TAG = re.compile(r"ci-[1-9][0-9]*-[0-9a-f]{12}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
# The runtime contract both images must publish; the rendered manifests and
# the worker admission policy rely on it.
USER = "10001:10001"
WORKDIR = "/app"


@dataclass(frozen=True)
class Image:
    role: str
    package: str
    repository: str
    auth_variable: str

    @property
    def identity(self) -> str:
        return "publisher-" + self.repository.removeprefix("apps/")

    @property
    def record(self) -> str:
        return "first-party-" + self.repository.replace("/", "-")

    def release(self) -> Release:
        return Release(
            name=self.role,
            service=PRODUCER,
            repository=self.repository,
            identity=self.identity,
            record=self.record,
            consumers=(PINS,),
            auth_variable=self.auth_variable,
        )


IMAGES = (
    Image(
        "controller",
        "orchestrator-controller-image",
        "apps/orchestrator",
        "REGISTRY_ORCHESTRATOR_PUBLISHER_AUTH_FILE",
    ),
    Image(
        "worker",
        "orchestrator-worker-image",
        "apps/orchestrator-worker",
        "REGISTRY_ORCHESTRATOR_WORKER_PUBLISHER_AUTH_FILE",
    ),
)


def verify(image: Image, token: str, tag: str) -> dict[str, Any]:
    """The published manifest by tag, checked against the runtime contract."""
    raw = registry_get(token, f"{image.repository}/manifests/{tag}")
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    manifest = json.loads(raw)
    if "config" not in manifest:
        raise ValueError(f"{image.role}: published reference is an index")
    config_digest = manifest["config"]["digest"]
    config_raw = registry_get(token, f"{image.repository}/blobs/{config_digest}")
    if "sha256:" + hashlib.sha256(config_raw).hexdigest() != config_digest:
        raise ValueError(f"{image.role}: configuration checksum differs")
    config = json.loads(config_raw)
    runtime = config.get("config") or {}
    if (
        runtime.get("User") != USER
        or runtime.get("WorkingDir") != WORKDIR
        or config.get("architecture") != "amd64"
        or config.get("os") != "linux"
    ):
        raise ValueError(f"{image.role}: published runtime contract differs")
    if registry_get(token, f"{image.repository}/manifests/{digest}") != raw:
        raise ValueError(f"{image.role}: digest lookup differs")
    return {
        "role": image.role,
        "repository": image.repository,
        "tag": tag,
        "digest": digest,
        "media_type": manifest.get(
            "mediaType", "application/vnd.oci.image.manifest.v1+json"
        ),
    }


def pins_text(receipts: dict[str, dict[str, Any]]) -> str:
    pins = {
        role: f"{REGISTRY}/{receipt['repository']}@{receipt['digest']}"
        for role, receipt in sorted(receipts.items())
    }
    return json.dumps(pins, indent=2) + "\n"


def lock_record(receipt: dict[str, Any], line: int) -> dict[str, Any]:
    repository = receipt["repository"]
    digest = receipt["digest"]
    tag = receipt["tag"]
    return {
        "authenticity": {
            "reason": "no source verification policy is declared for this inventory record",
            "status": "unsupported",
        },
        "consumers": [f"{PINS}:{line}"],
        "destination_repository": repository,
        "destination_tags": [tag, receipt["retention_tag"]],
        "digest": digest,
        "id": "first-party-" + repository.replace("/", "-"),
        "kind": "first-party",
        "media_type": receipt["media_type"],
        "platforms": ["linux/amd64"],
        "producer": {
            "location": PRODUCER,
            "owner": OWNER,
            "pipeline_status": "verified-ci-build-published",
        },
        "referrers": {"required": [], "source_status": "not-enumerated"},
        "resolution": {"status": "source-registry-verified"},
        "retention_class": "candidate",
        "source": {
            "digest": digest,
            "reference": f"{REGISTRY}/{repository}:{tag}@{digest}",
            "registry": REGISTRY,
            "repository": repository,
            "tag": tag,
        },
    }


INVENTORY_KEYS = (
    "consumers",
    "destination_repository",
    "id",
    "kind",
    "producer",
    "retention_class",
    "source",
    "digest",
    "destination_tags",
)


def upsert(document: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """Replace the record with this id, or append it; other records keep
    their order and content (the inventory must only change minimally)."""
    updated = copy.deepcopy(document)
    images = updated["images"]
    for index, existing in enumerate(images):
        if existing["id"] == record["id"]:
            images[index] = record
            return updated
    images.append(record)
    return updated


def diff(name: str, old: str, new: str) -> list[str]:
    return list(
        difflib.unified_diff(
            old.splitlines(True),
            new.splitlines(True),
            fromfile="/dev/null" if not old else "a/" + name,
            tofile="b/" + name,
        )
    )


def promotion_patch(root: Path, receipts: dict[str, dict[str, Any]]) -> str:
    """Pin the published digests and record them in the registry lock."""
    for receipt in receipts.values():
        if not DIGEST.fullmatch(receipt["digest"]) or not TAG.fullmatch(receipt["tag"]):
            raise ValueError("invalid publication receipt")
        expected = "retention-candidate-" + receipt["digest"][7:23]
        if receipt.get("retention_tag") != expected:
            raise ValueError("publication receipt lacks the verified retention tag")
    if set(receipts) != {image.role for image in IMAGES}:
        raise ValueError("both orchestrator images must be published together")
    pins = pins_text(receipts)
    lines = {
        role: index + 1
        for index, text in enumerate(pins.splitlines())
        for role in receipts
        if text.strip().startswith(f'"{role}"')
    }
    patch: list[str] = []
    pins_path = root / PINS
    patch += diff(PINS, pins_path.read_text() if pins_path.exists() else "", pins)
    for name in (LOCK, INVENTORY):
        old = (root / name).read_text()
        document = json.loads(old)
        for role, receipt in sorted(receipts.items()):
            record = lock_record(receipt, lines[role])
            if name == INVENTORY:
                record = {key: record[key] for key in INVENTORY_KEYS}
            document = upsert(document, record)
        new = json.dumps(document, indent=2) + "\n"
        patch += diff(name, old, new)
    return "".join(patch)


def build(image: Image, output: Path) -> Path:
    """Build the flake image and write its archive."""
    stream = subprocess.check_output(
        [
            "nix",
            "build",
            f"./flake#{image.package}",
            "--no-link",
            "--print-out-paths",
        ],
        text=True,
    ).strip()
    archive = output / f"{image.role}.tar"
    with archive.open("wb") as handle:
        subprocess.run([stream], stdout=handle, check=True)
    return archive


# The CI container has no system containers policy. The only source is the
# archive this job just built, so accept that transport and reject the rest.
PUSH_POLICY = {
    "default": [{"type": "reject"}],
    "transports": {"docker-archive": {"": [{"type": "insecureAcceptAnything"}]}},
}


def push(image: Image, archive: Path, tag: str) -> None:
    policy = archive.with_name("policy.json")
    policy.write_text(json.dumps(PUSH_POLICY))
    subprocess.run(
        [
            "skopeo",
            "--policy",
            str(policy),
            "copy",
            "--format",
            "oci",
            "--dest-authfile",
            os.environ[image.auth_variable],
            f"docker-archive:{archive}",
            f"docker://{REGISTRY}/{image.repository}:{tag}",
        ],
        check=True,
    )


def coordinate() -> None:
    sha = os.environ["CI_COMMIT_SHA"]
    if subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() != sha:
        raise ValueError("release checkout differs from validated source")
    tag = f"ci-{int(os.environ['CI_PIPELINE_NUMBER'])}-{sha[:12]}"
    output = Path("artifacts/orchestrator-release")
    output.mkdir(parents=True, exist_ok=True)
    tokens = {}
    # Check every credential and tag before publishing anything.
    for image in IMAGES:
        auth = json.loads(Path(os.environ[image.auth_variable]).read_text())
        tokens[image.role] = preflight(image.release(), auth)
        require_new_tag(image.release(), tokens[image.role], tag)
    receipts = {}
    for image in IMAGES:
        archive = build(image, output)
        push(image, archive, tag)
        archive.unlink()
        receipt = verify(image, tokens[image.role], tag)
        receipt["retention_tag"] = retain_publication(
            image.release(), tokens[image.role], receipt
        )
        receipts[image.role] = receipt
        print(
            f"{image.role} published: {REGISTRY}/{image.repository}@{receipt['digest']}"
        )
    (output / "receipts.json").write_text(json.dumps(receipts, indent=2) + "\n")
    patch = promotion_patch(Path.cwd(), receipts)
    (output / "promotion.patch").write_text(patch)
    # `just ship` reads the patch from this log and ships it as its own PR;
    # base64 because Woodpecker masks lines that match secret values.
    print("----- BEGIN PROMOTION PATCH (base64) -----")
    encoded = base64.b64encode(patch.encode()).decode()
    for start in range(0, len(encoded), 76):
        print(encoded[start : start + 76])
    print("----- END PROMOTION PATCH -----")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("coordinate",))
    parser.parse_args()
    coordinate()


if __name__ == "__main__":
    main()
