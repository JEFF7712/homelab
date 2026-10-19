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
from pathlib import Path
from typing import Any

REGISTRY = "registry.rupan.dev"
REPOSITORY = "apps/spotify-matrix"
IDENTITY = "publisher-spotify-matrix"
SERVICE = "services/led-matrix"
MANIFEST_TYPES = (
    "application/vnd.oci.image.manifest.v1+json, "
    "application/vnd.docker.distribution.manifest.v2+json"
)


def publisher_auth(auth: dict[str, Any]) -> str:
    try:
        if set(auth["auths"]) != {REGISTRY}:
            raise ValueError
        token = auth["auths"][REGISTRY]["auth"]
        user, password = base64.b64decode(token, validate=True).decode().split(":", 1)
        if user != IDENTITY or not password or any(c in password for c in "\r\n"):
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(
            f"Supply the protected {IDENTITY} OCI auth file; global Docker login is not used"
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
            f"Matrix registry read failed with HTTP {error.code}; check its protected publisher credential and repository policy"
        ) from None


def preflight(auth: dict[str, Any]) -> str:
    token = publisher_auth(auth)
    registry_get(token, "")
    return token


def check_request(request: dict[str, Any]) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", request.get("sha", "")):
        raise ValueError("Matrix release requires an exact source SHA")
    for name in ("number", "parent"):
        if type(request.get(name)) is not int or request[name] <= 0:
            raise ValueError("Matrix release requires positive pipeline identities")
    publisher_auth(request["publisher_auth"])
    password = request.get("registry_read_password", "")
    if not password or any(c in password for c in "\r\n"):
        raise ValueError("Matrix release requires the separate registry reader")


def verify_publication(token: str, tag: str, image_id: str, sha: str) -> dict[str, Any]:
    raw = registry_get(token, f"{REPOSITORY}/manifests/{tag}")
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    manifest = json.loads(raw)
    config_digest = manifest["config"]["digest"]
    if config_digest != image_id:
        raise ValueError("Published image differs from the tested local image")
    config_raw = registry_get(token, f"{REPOSITORY}/blobs/{config_digest}")
    if "sha256:" + hashlib.sha256(config_raw).hexdigest() != config_digest:
        raise ValueError("Published image configuration checksum differs")
    config = json.loads(config_raw)
    if (
        config["config"]["Labels"]["org.opencontainers.image.revision"] != sha
        or config["config"]["User"] != "1000:1000"
        or config["config"]["Entrypoint"] != ["python", "/app/ha_matrix_streamer.py"]
        or config["architecture"] != "amd64"
        or config["os"] != "linux"
    ):
        raise ValueError("Published matrix runtime contract differs")
    # Verify the immutable reference too, rather than relying on a mutable tag.
    if registry_get(token, f"{REPOSITORY}/manifests/{digest}") != raw:
        raise ValueError("Published matrix digest lookup differs")
    return {
        "source_revision": sha,
        "repository": REPOSITORY,
        "tag": tag,
        "digest": digest,
        "config_digest": config_digest,
        "media_type": manifest["mediaType"],
        "image": f"{REGISTRY}/{REPOSITORY}@{digest}",
    }


def unpack_source(encoded: str, destination: Path) -> None:
    with tarfile.open(
        fileobj=io.BytesIO(base64.b64decode(encoded, validate=True))
    ) as archive:
        for member in archive.getmembers():
            name = Path(member.name)
            if (
                name.is_absolute()
                or ".." in name.parts
                or not (member.isfile() or member.isdir())
                or not (name.as_posix() == "services" or name.is_relative_to(SERVICE))
            ):
                raise ValueError("Matrix source archive contains an unexpected path")
        archive.extractall(destination, filter="data")


def retain_publication(token: str, receipt: dict[str, Any]) -> str:
    digest = receipt["digest"]
    raw = registry_get(token, f"{REPOSITORY}/manifests/{digest}")
    if "sha256:" + hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("Matrix retention input checksum differs")
    tag = "retention-candidate-" + digest.removeprefix("sha256:")[:16]
    request = urllib.request.Request(
        f"https://{REGISTRY}/v2/{REPOSITORY}/manifests/{tag}",
        data=raw,
        method="PUT",
        headers={
            "Authorization": "Basic " + token,
            "Content-Type": receipt["media_type"],
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 201:
            raise ValueError("Matrix retention tag creation failed")
    if registry_get(token, f"{REPOSITORY}/manifests/{tag}") != raw:
        raise ValueError("Matrix retention tag checksum differs")
    return tag


def require_new_tag(token: str, tag: str) -> None:
    request = urllib.request.Request(
        f"https://{REGISTRY}/v2/{REPOSITORY}/manifests/{tag}",
        method="HEAD",
        headers={"Authorization": "Basic " + token, "Accept": MANIFEST_TYPES},
    )
    try:
        with urllib.request.urlopen(request, timeout=30):
            pass
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return
        raise RuntimeError(
            f"Matrix tag preflight failed with HTTP {error.code}"
        ) from None
    raise ValueError(
        "Matrix pipeline tag already exists; inspect the prior receipt before retrying"
    )


def execute(command: list[str], log: Path, **kwargs: Any) -> None:
    with log.open("ab") as output:
        result = subprocess.run(
            command, stdout=output, stderr=subprocess.STDOUT, **kwargs
        )
    if result.returncode:
        raise RuntimeError(f"Matrix release failed; inspect {log}")


def worker(request: dict[str, Any]) -> dict[str, Any]:
    check_request(request)
    token = preflight(request["publisher_auth"])
    root = Path("/persist/matrix-ci")
    root.mkdir(mode=0o700, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"{request['number']}-", dir=root))
    log = work / "release.log"
    source = work / "source"
    unpack_source(request["source_archive"], source)
    service = source / SERVICE
    base_match = re.search(
        r"^ARG PYTHON_BASE=(registry\.rupan\.dev/upstream/[^\s]+@sha256:[0-9a-f]{64})$",
        (service / "Dockerfile").read_text(),
        re.MULTILINE,
    )
    if not base_match:
        raise ValueError("Matrix Dockerfile must pin its local Python base by digest")
    base = base_match[1]
    tag = f"ci-{request['number']}-{request['sha'][:12]}"
    require_new_tag(token, tag)
    image = f"{REGISTRY}/{REPOSITORY}:{tag}"
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
        execute([*reader, "pull", base], log)
        wheels = service / "wheelhouse"
        wheels.mkdir()
        execute(
            [
                *reader,
                "run",
                "--rm",
                "--cap-drop=ALL",
                "-v",
                f"{service / 'requirements.txt'}:/requirements.txt:ro",
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
                "--network=none",
                "--platform=linux/amd64",
                "--label",
                "org.opencontainers.image.revision=" + request["sha"],
                "--tag",
                image,
                str(service),
            ],
            log,
        )
        tests = work / "tests"
        tests.mkdir(mode=0o755)
        for name in (
            "test_ha_source.py",
            "test_arcade_polish.py",
            "test_streaming.py",
            "test_spotify_limits.py",
        ):
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
        receipt = verify_publication(token, tag, image_id, request["sha"])
        receipt["retention_tag"] = retain_publication(token, receipt)
    receipt.update(pipeline=request["number"], parent_validation=request["parent"])
    (work / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def promotion_patch(root: Path, receipt: dict[str, Any]) -> str:
    digest = receipt["digest"]
    tag = receipt["tag"]
    retention_tag = "retention-candidate-" + digest.removeprefix("sha256:")[:16]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) or not re.fullmatch(
        r"ci-[1-9][0-9]*-[0-9a-f]{12}", tag
    ):
        raise ValueError("Invalid matrix publication receipt")
    if receipt["repository"] != REPOSITORY:
        raise ValueError("Publication receipt names another repository")
    if receipt.get("retention_tag") != retention_tag:
        raise ValueError("Publication receipt lacks the verified matrix retention tag")
    patch = []
    for name in (
        "gitops/led-matrix/deployment.yaml",
        "registry/images.lock.json",
        "registry/images.inventory.json",
    ):
        old = (root / name).read_text()
        if name.endswith(".yaml"):
            new, count = re.subn(
                rf"{re.escape(REGISTRY + '/' + REPOSITORY)}@sha256:[0-9a-f]{{64}}",
                f"{REGISTRY}/{REPOSITORY}@{digest}",
                old,
            )
            if count != 1:
                raise ValueError("Expected exactly one pinned matrix Deployment image")
        else:
            document = json.loads(old)
            records = [
                r
                for r in document["images"]
                if r["id"] == "first-party-apps-spotify-matrix"
            ]
            if len(records) != 1:
                raise ValueError("Expected exactly one matrix registry record")
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
                reference=f"{REGISTRY}/{REPOSITORY}:{tag}@{digest}",
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


def coordinate() -> None:
    from scripts.ci.native_release import BUILDER, ssh_command

    sha = os.environ["CI_COMMIT_SHA"]
    if subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() != sha:
        raise ValueError("Matrix release checkout differs from validated source")
    request = {
        "sha": sha,
        "number": int(os.environ["CI_PIPELINE_NUMBER"]),
        "parent": int(os.environ["CI_OPERATION_PARENT"]),
        "publisher_auth": json.loads(
            Path(os.environ["REGISTRY_MATRIX_PUBLISHER_AUTH_FILE"]).read_text()
        ),
        "registry_read_password": os.environ["REGISTRY_READ_PASSWORD"],
    }
    check_request(request)
    preflight(request["publisher_auth"])
    request["source_archive"] = base64.b64encode(
        subprocess.check_output(["git", "archive", sha, SERVICE])
    ).decode()
    program = base64.b64encode(Path(__file__).read_bytes()).decode()
    code = f"import base64,sys;sys.argv=['matrix-release','worker'];exec(compile(base64.b64decode({program!r}),'<protected-matrix-release>','exec'))"
    bootstrap = (
        "set -eu; matrix_python=''; "
        "for matrix_path in $(nix-store -q --requisites /run/current-system); do "
        'if test -x "$matrix_path/bin/python3"; then matrix_python=$matrix_path/bin/python3; break; fi; done; '
        'test -n "$matrix_python"; exec sudo -n "$matrix_python" -c '
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
            "Matrix builder failed; inspect /persist/matrix-ci/*/release.log on homelab-04"
        )
    receipt = json.loads(result.stdout.splitlines()[-1])
    if receipt["source_revision"] != sha or receipt["pipeline"] != request["number"]:
        raise ValueError("Matrix builder returned another source or pipeline")
    output = Path("artifacts/matrix-release")
    output.mkdir(parents=True, exist_ok=True)
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    patch = promotion_patch(Path.cwd(), receipt)
    (output / "promotion.patch").write_text(patch)
    print("Matrix image publication verified: " + receipt["image"])
    # The patch holds only public digests and tags. `just ship` reads it from
    # this log and ships it as the Flux promotion PR.
    print("----- BEGIN PROMOTION PATCH -----")
    print(patch, end="")
    print("----- END PROMOTION PATCH -----")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("doctor", "coordinate", "worker"))
    parser.add_argument("--auth-file", type=Path)
    args = parser.parse_args()
    if args.mode == "worker":
        print(json.dumps(worker(json.load(sys.stdin))))
    elif args.mode == "coordinate":
        coordinate()
    else:
        if args.auth_file is None:
            parser.error(
                "doctor requires --auth-file for the protected matrix publisher"
            )
        preflight(json.loads(args.auth_file.read_text()))
        print(
            "Matrix publisher authentication succeeded; write access has not been exercised"
        )


if __name__ == "__main__":
    main()
