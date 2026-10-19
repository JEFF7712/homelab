"""Protected image releases for first-party services built from this repo.

The coordinator runs in a protected CI operation, archives the validated
service source, and ships itself to the builder over SSH. The builder pulls
the digest-pinned base with the registry reader, builds, optionally tests
inside the image, pushes with the service's scoped publisher, and verifies
the published runtime contract. The coordinator then prints a promotion
patch that `just ship` turns into the Flux PR. This file must stay
self-contained: the builder executes it without the repository.
"""

from __future__ import annotations

import argparse
import base64
import difflib
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REGISTRY = "registry.rupan.dev"
MANIFEST_TYPES = (
    "application/vnd.oci.image.manifest.v1+json, "
    "application/vnd.docker.distribution.manifest.v2+json"
)


@dataclass(frozen=True)
class Release:
    name: str
    service: str
    repository: str
    identity: str
    record: str
    consumers: tuple[str, ...]
    auth_variable: str
    # Expected runtime contract; None skips that field for vendor bases.
    entrypoint: tuple[str, ...] | None = None
    user: str | None = None
    # Hashed requirements downloaded into wheelhouse/ inside the base image.
    requirements: str | None = None
    # Wheel supply fetched by the coordinator before archiving.
    wheel_fetch: str | None = None
    tests: tuple[str, ...] = ()
    # A vendor base that installs packages at build time needs the network.
    build_network: bool = False


RELEASES = {
    release.name: release
    for release in (
        Release(
            name="spotify-matrix",
            service="services/led-matrix",
            repository="apps/spotify-matrix",
            identity="publisher-spotify-matrix",
            record="first-party-apps-spotify-matrix",
            consumers=("gitops/led-matrix/deployment.yaml",),
            auth_variable="REGISTRY_MATRIX_PUBLISHER_AUTH_FILE",
            entrypoint=("python", "/app/ha_matrix_streamer.py"),
            user="1000:1000",
            requirements="requirements.txt",
            tests=(
                "test_ha_source.py",
                "test_arcade_polish.py",
                "test_streaming.py",
                "test_spotify_limits.py",
            ),
        ),
        Release(
            name="bus-display",
            service="services/bus-display",
            repository="apps/bus-display",
            identity="publisher-bus-display",
            record="first-party-apps-bus-display-244cb03cb790",
            consumers=("gitops/websites/bus-display/deployment.yaml",),
            auth_variable="REGISTRY_BUS_DISPLAY_PUBLISHER_AUTH_FILE",
            entrypoint=("python3", "/app/bus_service.py"),
            user="1000:1000",
            wheel_fetch="services/bus-display/scripts/fetch-wheels.sh",
        ),
        Release(
            name="lidarr-extended",
            service="services/lidarr-extended",
            repository="apps/lidarr-extended",
            identity="publisher-lidarr-extended",
            record="first-party-apps-lidarr-extended-c59cbeca30f2",
            consumers=("gitops/media/arr.yaml",),
            auth_variable="REGISTRY_LIDARR_EXTENDED_PUBLISHER_AUTH_FILE",
            build_network=True,
        ),
    )
}


def publisher_auth(release: Release, auth: dict[str, Any]) -> str:
    try:
        if set(auth["auths"]) != {REGISTRY}:
            raise ValueError
        token = auth["auths"][REGISTRY]["auth"]
        user, password = base64.b64decode(token, validate=True).decode().split(":", 1)
        if (
            user != release.identity
            or not password
            or any(c in password for c in "\r\n")
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(
            f"Supply the protected {release.identity} OCI auth file; global Docker login is not used"
        ) from error
    return token


def registry_get(token: str, path: str) -> bytes:
    request = urllib.request.Request(
        f"https://{REGISTRY}/v2/{path}",
        headers={"Authorization": "Basic " + token, "Accept": MANIFEST_TYPES},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"Registry read failed with HTTP {error.code}; check the protected publisher credential and repository policy"
        ) from None


def preflight(release: Release, auth: dict[str, Any]) -> str:
    token = publisher_auth(release, auth)
    registry_get(token, "")
    return token


def check_request(request: dict[str, Any]) -> Release:
    release = RELEASES.get(request.get("release", ""))
    if release is None:
        raise ValueError("Unknown service release")
    if not re.fullmatch(r"[0-9a-f]{40}", request.get("sha", "")):
        raise ValueError("Service release requires an exact source SHA")
    for name in ("number", "parent"):
        if type(request.get(name)) is not int or request[name] <= 0:
            raise ValueError("Service release requires positive pipeline identities")
    publisher_auth(release, request["publisher_auth"])
    password = request.get("registry_read_password", "")
    if not password or any(c in password for c in "\r\n"):
        raise ValueError("Service release requires the separate registry reader")
    return release


def verify_publication(
    release: Release, token: str, tag: str, image_id: str, sha: str
) -> dict[str, Any]:
    repository = release.repository
    raw = registry_get(token, f"{repository}/manifests/{tag}")
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    manifest = json.loads(raw)
    config_digest = manifest["config"]["digest"]
    if config_digest != image_id:
        raise ValueError("Published image differs from the tested local image")
    config_raw = registry_get(token, f"{repository}/blobs/{config_digest}")
    if "sha256:" + hashlib.sha256(config_raw).hexdigest() != config_digest:
        raise ValueError("Published image configuration checksum differs")
    config = json.loads(config_raw)
    runtime = config["config"]
    if (
        runtime["Labels"]["org.opencontainers.image.revision"] != sha
        or (release.user is not None and runtime.get("User") != release.user)
        or (
            release.entrypoint is not None
            and runtime.get("Entrypoint") != list(release.entrypoint)
        )
        or config["architecture"] != "amd64"
        or config["os"] != "linux"
    ):
        raise ValueError(f"Published {release.name} runtime contract differs")
    # Verify the immutable reference too, rather than relying on a mutable tag.
    if registry_get(token, f"{repository}/manifests/{digest}") != raw:
        raise ValueError(f"Published {release.name} digest lookup differs")
    return {
        "source_revision": sha,
        "repository": repository,
        "tag": tag,
        "digest": digest,
        "config_digest": config_digest,
        "media_type": manifest["mediaType"],
        "image": f"{REGISTRY}/{repository}@{digest}",
    }


def unpack_source(release: Release, encoded: str, destination: Path) -> None:
    with tarfile.open(
        fileobj=io.BytesIO(base64.b64decode(encoded, validate=True))
    ) as archive:
        for member in archive.getmembers():
            name = Path(member.name)
            if (
                name.is_absolute()
                or ".." in name.parts
                or not (member.isfile() or member.isdir())
                or not (
                    name.as_posix() in {"services", release.service}
                    or name.is_relative_to(release.service)
                )
            ):
                raise ValueError("Service source archive contains an unexpected path")
        archive.extractall(destination, filter="data")


def retain_publication(release: Release, token: str, receipt: dict[str, Any]) -> str:
    repository = release.repository
    digest = receipt["digest"]
    raw = registry_get(token, f"{repository}/manifests/{digest}")
    if "sha256:" + hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("Retention input checksum differs")
    tag = "retention-candidate-" + digest.removeprefix("sha256:")[:16]
    request = urllib.request.Request(
        f"https://{REGISTRY}/v2/{repository}/manifests/{tag}",
        data=raw,
        method="PUT",
        headers={
            "Authorization": "Basic " + token,
            "Content-Type": receipt["media_type"],
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 201:
            raise ValueError("Retention tag creation failed")
    if registry_get(token, f"{repository}/manifests/{tag}") != raw:
        raise ValueError("Retention tag checksum differs")
    return tag


def require_new_tag(release: Release, token: str, tag: str) -> None:
    request = urllib.request.Request(
        f"https://{REGISTRY}/v2/{release.repository}/manifests/{tag}",
        method="HEAD",
        headers={"Authorization": "Basic " + token, "Accept": MANIFEST_TYPES},
    )
    try:
        with urllib.request.urlopen(request, timeout=30):
            pass
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return
        raise RuntimeError(f"Tag preflight failed with HTTP {error.code}") from None
    raise ValueError(
        "Pipeline tag already exists; inspect the prior receipt before retrying"
    )


def execute(command: list[str], log: Path, **kwargs: Any) -> None:
    with log.open("ab") as output:
        result = subprocess.run(
            command, stdout=output, stderr=subprocess.STDOUT, **kwargs
        )
    if result.returncode:
        raise RuntimeError(f"Service release failed; inspect {log}")


def pinned_base(dockerfile: str) -> str | None:
    match = re.search(
        r"(registry\.rupan\.dev/upstream/\S+@sha256:[0-9a-f]{64})", dockerfile
    )
    return match[1] if match else None


def worker(request: dict[str, Any]) -> dict[str, Any]:
    release = check_request(request)
    token = preflight(release, request["publisher_auth"])
    root = Path("/persist/service-release-ci")
    root.mkdir(mode=0o700, exist_ok=True)
    work = Path(
        tempfile.mkdtemp(prefix=f"{release.name}-{request['number']}-", dir=root)
    )
    log = work / "release.log"
    source = work / "source"
    unpack_source(release, request["source_archive"], source)
    service = source / release.service
    base = pinned_base((service / "Dockerfile").read_text())
    if base is None and not release.build_network:
        raise ValueError(f"{release.name} Dockerfile must pin its local base by digest")
    tag = f"ci-{request['number']}-{request['sha'][:12]}"
    require_new_tag(release, token, tag)
    image = f"{REGISTRY}/{release.repository}:{tag}"
    with tempfile.TemporaryDirectory(prefix="credentials-", dir=work) as temporary:
        protected = Path(temporary)
        for name, auth in (
            ("publisher", request["publisher_auth"]),
            (
                "reader",
                {
                    "auths": {
                        REGISTRY: {
                            "auth": base64.b64encode(
                                ("node:" + request["registry_read_password"]).encode()
                            ).decode()
                        }
                    }
                },
            ),
        ):
            directory = protected / name
            directory.mkdir(mode=0o700)
            path = directory / "config.json"
            path.write_text(json.dumps(auth))
            path.chmod(0o600)
        reader = ["docker", "--config", str(protected / "reader")]
        writer = ["docker", "--config", str(protected / "publisher")]
        if base is not None:
            execute([*reader, "pull", base], log)
        if release.requirements is not None:
            if base is None:
                raise ValueError("Wheel downloads require a pinned local base")
            wheels = service / "wheelhouse"
            wheels.mkdir()
            execute(
                [
                    *reader,
                    "run",
                    "--rm",
                    "--cap-drop=ALL",
                    "-v",
                    f"{service / release.requirements}:/requirements.txt:ro",
                    "-v",
                    f"{wheels}:/wheels",
                    base,
                    "python",
                    "-m",
                    "pip",
                    "download",
                    "--require-hashes",
                    "--only-binary=:all:",
                    "--dest",
                    "/wheels",
                    "-r",
                    "/requirements.txt",
                ],
                log,
            )
        execute(
            [
                *reader,
                "buildx",
                "build",
                "--load",
                "--provenance=false",
                "--network=" + ("default" if release.build_network else "none"),
                "--platform=linux/amd64",
                "--label",
                "org.opencontainers.image.revision=" + request["sha"],
                "--tag",
                image,
                str(service),
            ],
            log,
        )
        if release.tests:
            tests = work / "tests"
            tests.mkdir(mode=0o755)
            for name in release.tests:
                (tests / name).write_bytes((service / name).read_bytes())
            execute(
                [
                    *reader,
                    "run",
                    "--rm",
                    "--network=none",
                    "--read-only",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--tmpfs",
                    "/tmp:rw,size=64m",
                    "-v",
                    f"{tests}:/tests:ro",
                    "-e",
                    "PYTHONPATH=/app",
                    "--entrypoint",
                    "python",
                    image,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "/tests",
                    "-v",
                ],
                log,
            )
        image_id = subprocess.check_output(
            [*reader, "image", "inspect", "--format", "{{.Id}}", image], text=True
        ).strip()
        execute([*writer, "push", image], log)
        receipt = verify_publication(release, token, tag, image_id, request["sha"])
        receipt["retention_tag"] = retain_publication(release, token, receipt)
    receipt.update(
        release=release.name,
        pipeline=request["number"],
        parent_validation=request["parent"],
    )
    (work / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def promotion_patch(release: Release, root: Path, receipt: dict[str, Any]) -> str:
    digest = receipt["digest"]
    tag = receipt["tag"]
    retention_tag = "retention-candidate-" + digest.removeprefix("sha256:")[:16]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) or not re.fullmatch(
        r"ci-[1-9][0-9]*-[0-9a-f]{12}", tag
    ):
        raise ValueError("Invalid publication receipt")
    if receipt["repository"] != release.repository:
        raise ValueError("Publication receipt names another repository")
    if receipt.get("retention_tag") != retention_tag:
        raise ValueError("Publication receipt lacks the verified retention tag")
    patch = []
    for name in (
        *release.consumers,
        "registry/images.lock.json",
        "registry/images.inventory.json",
    ):
        old = (root / name).read_text()
        if name.endswith(".yaml"):
            new, count = re.subn(
                rf"{re.escape(REGISTRY + '/' + release.repository)}@sha256:[0-9a-f]{{64}}",
                f"{REGISTRY}/{release.repository}@{digest}",
                old,
            )
            if count != 1:
                raise ValueError(
                    f"Expected exactly one pinned {release.repository} image in {name}"
                )
        else:
            document = json.loads(old)
            records = [r for r in document["images"] if r["id"] == release.record]
            if len(records) != 1:
                raise ValueError(f"Expected exactly one {release.record} record")
            record = records[0]
            record.update(
                digest=digest,
                destination_tags=[tag, retention_tag],
                retention_class="candidate",
            )
            record["producer"]["pipeline_status"] = "verified-ci-build-published"
            record["source"].update(
                tag=tag,
                digest=digest,
                reference=f"{REGISTRY}/{release.repository}:{tag}@{digest}",
            )
            if "media_type" in record:
                record["media_type"] = receipt["media_type"]
            new = json.dumps(document, indent=2) + "\n"
        patch.extend(
            difflib.unified_diff(
                old.splitlines(True),
                new.splitlines(True),
                fromfile="a/" + name,
                tofile="b/" + name,
            )
        )
    return "".join(patch)


def source_archive(release: Release, sha: str) -> bytes:
    """The committed service tree, plus a verified wheelhouse when declared."""
    archive = subprocess.check_output(["git", "archive", sha, release.service])
    if release.wheel_fetch is None:
        return archive
    subprocess.run(["bash", release.wheel_fetch], check=True)
    wheelhouse = Path(release.service) / "wheelhouse"
    buffer = io.BytesIO()
    with (
        tarfile.open(fileobj=io.BytesIO(archive)) as committed,
        tarfile.open(fileobj=buffer, mode="w") as combined,
    ):
        for member in committed.getmembers():
            combined.addfile(
                member, committed.extractfile(member) if member.isfile() else None
            )
        for path in sorted(wheelhouse.iterdir()):
            combined.add(path, arcname=path.as_posix(), recursive=False)
    return buffer.getvalue()


def coordinate(name: str) -> None:
    from scripts.ci.native_release import BUILDER, ssh_command

    release = RELEASES[name]
    sha = os.environ["CI_COMMIT_SHA"]
    if subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() != sha:
        raise ValueError("Release checkout differs from validated source")
    request = {
        "release": release.name,
        "sha": sha,
        "number": int(os.environ["CI_PIPELINE_NUMBER"]),
        "parent": int(os.environ["CI_OPERATION_PARENT"]),
        "publisher_auth": json.loads(
            Path(os.environ[release.auth_variable]).read_text()
        ),
        "registry_read_password": os.environ["REGISTRY_READ_PASSWORD"],
    }
    check_request(request)
    preflight(release, request["publisher_auth"])
    request["source_archive"] = base64.b64encode(source_archive(release, sha)).decode()
    program = base64.b64encode(Path(__file__).read_bytes()).decode()
    code = f"import base64,sys;sys.argv=['service-release','worker'];exec(compile(base64.b64decode({program!r}),'<protected-service-release>','exec'))"
    bootstrap = (
        "set -eu; release_python=''; "
        "for release_path in $(nix-store -q --requisites /run/current-system); do "
        'if test -x "$release_path/bin/python3"; then release_python=$release_path/bin/python3; break; fi; done; '
        'test -n "$release_python"; exec sudo -n "$release_python" -c '
        + shlex.quote(code)
    )
    result = subprocess.run(
        [*ssh_command(BUILDER, relay=True), bootstrap],
        input=json.dumps(request),
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(
            f"{release.name} builder failed; inspect /persist/service-release-ci/{release.name}-*/release.log on homelab-04"
        )
    receipt = json.loads(result.stdout.splitlines()[-1])
    if receipt["source_revision"] != sha or receipt["pipeline"] != request["number"]:
        raise ValueError("Builder returned another source or pipeline")
    output = Path("artifacts/service-release") / release.name
    output.mkdir(parents=True, exist_ok=True)
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    patch = promotion_patch(release, Path.cwd(), receipt)
    (output / "promotion.patch").write_text(patch)
    print(f"{release.name} image publication verified: " + receipt["image"])
    # The patch holds only public digests and tags. `just ship` reads it from
    # this log and ships it as the Flux promotion PR.
    print("----- BEGIN PROMOTION PATCH -----")
    print(patch, end="")
    print("----- END PROMOTION PATCH -----")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("doctor", "coordinate", "worker"))
    parser.add_argument("release", nargs="?", choices=sorted(RELEASES))
    parser.add_argument("--auth-file", type=Path)
    args = parser.parse_args()
    if args.mode == "worker":
        print(json.dumps(worker(json.load(sys.stdin))))
        return
    if args.release is None:
        parser.error(f"{args.mode} requires a release name")
    release = RELEASES[args.release]
    if args.mode == "coordinate":
        coordinate(release.name)
        return
    if args.auth_file is None:
        parser.error("doctor requires --auth-file for the protected publisher")
    preflight(release, json.loads(args.auth_file.read_text()))
    print(
        f"{release.identity} authentication succeeded; write access has not been exercised"
    )


if __name__ == "__main__":
    main()
