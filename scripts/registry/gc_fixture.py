"""Prove zot's garbage-collection behaviour on a disposable instance.

Production runs zot 2.1.20 with `storage.gc` enabled and no `storage.retention`
policy, so a manifest that loses every tag becomes collectable. That single fact
is what makes a retention tag load-bearing: the digest a lock pins stops being
pullable the moment it loses its last tag, which is the failure the reconciler
and drift check exist to prevent.

That behaviour has been assumed, and one past incident was explained without
evidence for it. This fixture tests it directly. It serves zot from a temporary
root on a spare port, publishes crafted images with known digests, removes their
tags, lets zot's own collector run, and then asserts from the API which
manifests survived.

The scenarios cover the shapes that matter:

- a manifest that loses every tag is collected;
- a manifest kept alive by a retention tag is not;
- when a tag is repointed at another digest the manifest it used to name is
  collected, which is how a retention tag pointing at the wrong image silently
  costs you the pinned one.

The real registry is never contacted. Everything runs on loopback against a
throwaway root that is deleted afterwards.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import dataclasses
import gzip
import hashlib
import io
import json
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, Self

SCHEMA_VERSION = 1
# The release the registry serves. A mismatch means the fixture no longer
# describes production, so it is reported rather than quietly passing.
EXPECTED_ZOT_VERSION = "2.1.20"
STARTUP_TIMEOUT = 60
COLLECT_TIMEOUT = 180
MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
CONFIG_MEDIA_TYPE = "application/vnd.oci.image.config.v1+json"
LAYER_MEDIA_TYPE = "application/vnd.oci.image.layer.v1.tar+gzip"
ACCEPT = (
    f"{MANIFEST_MEDIA_TYPE}, "
    "application/vnd.docker.distribution.manifest.v2+json, "
    "application/vnd.oci.image.index.v1+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json"
)


class FixtureError(RuntimeError):
    pass


def zot_build(binary: str) -> str:
    """The build string zot reports, e.g. `2.1.20-0-g3b5796d`."""
    result = subprocess.run(
        [binary, "--version"], capture_output=True, text=True, timeout=60, check=False
    )
    if result.returncode != 0:
        raise FixtureError(f"zot --version failed: {result.stderr.strip()}")
    return str(json.loads(result.stdout)["commit"]).removeprefix("v")


def zot_version(binary: str) -> str:
    """The release version, with any git describe suffix dropped."""
    match = re.match(r"(\d+\.\d+\.\d+)", zot_build(binary))
    if match is None:
        raise FixtureError(f"zot reported an unparseable version: {zot_build(binary)}")
    return match.group(1)


def free_port() -> int:
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _layer(payload: bytes) -> tuple[bytes, str]:
    """A deterministic gzipped tar layer, so digests repeat across runs."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        info = tarfile.TarInfo("payload.txt")
        info.size = len(payload)
        info.mtime = 0
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(payload))
    compressed = io.BytesIO()
    # mtime=0 keeps the gzip header from varying between runs.
    with gzip.GzipFile(fileobj=compressed, mode="wb", mtime=0) as handle:
        handle.write(raw.getvalue())
    body = compressed.getvalue()
    return body, "sha256:" + hashlib.sha256(body).hexdigest()


def build_image(root: pathlib.Path, payload: bytes) -> str:
    """Write a single-layer OCI layout and return its manifest digest."""
    blobs = root / "blobs" / "sha256"
    blobs.mkdir(parents=True)

    layer, layer_digest = _layer(payload)
    diff_id = "sha256:" + hashlib.sha256(gzip.decompress(layer)).hexdigest()
    config = json.dumps(
        {
            "architecture": "amd64",
            "os": "linux",
            "config": {},
            "rootfs": {"type": "layers", "diff_ids": [diff_id]},
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    config_digest = "sha256:" + hashlib.sha256(config).hexdigest()
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": MANIFEST_MEDIA_TYPE,
            "config": {
                "mediaType": CONFIG_MEDIA_TYPE,
                "digest": config_digest,
                "size": len(config),
            },
            "layers": [
                {
                    "mediaType": LAYER_MEDIA_TYPE,
                    "digest": layer_digest,
                    "size": len(layer),
                }
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    manifest_digest = "sha256:" + hashlib.sha256(manifest).hexdigest()

    for body, digest in (
        (config, config_digest),
        (layer, layer_digest),
        (manifest, manifest_digest),
    ):
        (blobs / digest.removeprefix("sha256:")).write_bytes(body)

    (root / "oci-layout").write_text(
        json.dumps({"imageLayoutVersion": "1.0.0"}), encoding="utf-8"
    )
    (root / "index.json").write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "manifests": [
                    {
                        "mediaType": MANIFEST_MEDIA_TYPE,
                        "digest": manifest_digest,
                        "size": len(manifest),
                        "annotations": {"org.opencontainers.image.ref.name": "v1"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest_digest


def write_htpasswd(path: pathlib.Path, user: str, password: str) -> None:
    """Hash the way production does, keeping the password off argv."""
    result = subprocess.run(
        ["htpasswd", "-Bbn", user, password],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise FixtureError(f"htpasswd failed: {result.stderr.strip()}")
    path.write_text(result.stdout, encoding="utf-8")
    path.chmod(0o600)


def write_auth_file(
    path: pathlib.Path, registry: str, user: str, password: str
) -> None:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    path.write_text(
        json.dumps({"auths": {registry: {"auth": token}}}), encoding="utf-8"
    )
    path.chmod(0o600)


def build_config(
    storage_root: pathlib.Path,
    htpasswd: pathlib.Path,
    port: int,
    user: str,
    *,
    gc_delay: str = "1s",
    gc_interval: str = "2s",
) -> dict[str, Any]:
    """A config shaped like production's, with collection on a short leash.

    Production runs `gcInterval: 24h`; the fixture shortens it so the collector
    runs inside the test. What decides *what* gets collected is left identical,
    above all the absence of any `storage.retention` policy.
    """
    return {
        "distSpecVersion": "1.1.1",
        "storage": {
            "rootDirectory": str(storage_root),
            "commit": True,
            "dedupe": True,
            "gc": True,
            "gcDelay": gc_delay,
            "gcInterval": gc_interval,
        },
        "http": {
            "address": "127.0.0.1",
            "port": str(port),
            "realm": "127.0.0.1",
            "readTimeout": "30m",
            "writeTimeout": "30m",
            "auth": {"failDelay": 1, "htpasswd": {"path": str(htpasswd)}},
            "accessControl": {
                "repositories": {
                    # The same shape production uses per repository: explicit
                    # actions rather than a bare defaultPolicy.
                    "**": {
                        "defaultPolicy": [],
                        "policies": [
                            {
                                "users": [user],
                                "actions": [
                                    "read",
                                    "create",
                                    "update",
                                    "delete",
                                ],
                            }
                        ],
                    }
                }
            },
        },
        "log": {"level": "warn"},
    }


class DisposableRegistry:
    """A throwaway zot on loopback, torn down when the context exits."""

    def __init__(self, binary: str, root: pathlib.Path, port: int) -> None:
        self.binary = binary
        self.root = root
        self.port = port
        self.user = "fixture"
        # A throwaway credential for a loopback registry that is deleted on
        # exit; it never authenticates against anything real.
        self.password = "fixture-" + hashlib.sha256(str(root).encode()).hexdigest()[:16]
        self.htpasswd = root / "htpasswd"
        self.auth_file = root / "auth.json"
        self.config_path = root / "config.json"
        self.process: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> Self:
        self.root.mkdir(parents=True, exist_ok=True)
        write_htpasswd(self.htpasswd, self.user, self.password)
        write_auth_file(self.auth_file, self.host, self.user, self.password)
        self.config_path.write_text(
            json.dumps(
                build_config(self.root / "storage", self.htpasswd, self.port, self.user)
            ),
            encoding="utf-8",
        )
        self._log = (self.root / "zot.log").open("wb")
        self.process = subprocess.Popen(
            [self.binary, "serve", str(self.config_path)],
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )
        self._await_ready()
        return self

    def __exit__(self, *_: object) -> None:
        if self.process is not None:
            self.process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=30)
            if self.process.poll() is None:
                self.process.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self.process.wait(timeout=30)
        self._log.close()

    @property
    def host(self) -> str:
        return f"127.0.0.1:{self.port}"

    def log_tail(self) -> str:
        return (self.root / "zot.log").read_text(errors="replace")[-2000:]

    def _await_ready(self) -> None:
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise FixtureError(f"zot exited during startup:\n{self.log_tail()}")
            try:
                if self.get("/v2/")[0] in {200, 401}:
                    return
            except (urllib.error.URLError, TimeoutError, OSError):
                pass
            time.sleep(0.2)
        raise FixtureError(f"zot was not ready in time:\n{self.log_tail()}")

    def _send(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        request = urllib.request.Request(
            f"http://{self.host}{path}", data=body, method=method
        )
        token = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers or {}), error.read()

    def get(self, path: str, *, accept: str | None = None) -> tuple[int, bytes]:
        status, _, body = self._send(
            "GET", path, headers={"Accept": accept} if accept else None
        )
        return status, body

    def put(
        self, path: str, body: bytes, headers: dict[str, str]
    ) -> tuple[int, dict[str, str], bytes]:
        return self._send("PUT", path, body=body, headers=headers)

    def delete(self, path: str) -> int:
        request = urllib.request.Request(f"http://{self.host}{path}", method="DELETE")
        token = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code

    def manifest_exists(self, repository: str, reference: str) -> bool:
        status, _ = self.get(f"/v2/{repository}/manifests/{reference}", accept=ACCEPT)
        return status == 200

    def tags(self, repository: str) -> list[str]:
        status, body = self.get(f"/v2/{repository}/tags/list")
        if status != 200:
            return []
        payload = json.loads(body)
        return list(payload.get("tags") or [])

    def push(self, layout: pathlib.Path, repository: str, tag: str) -> str:
        """Publish a crafted OCI layout, returning the manifest digest stored."""
        digest = json.loads((layout / "index.json").read_text(encoding="utf-8"))[
            "manifests"
        ][0]["digest"]
        blobs = layout / "blobs" / "sha256"
        manifest = (blobs / digest.removeprefix("sha256:")).read_bytes()
        descriptor = json.loads(manifest)
        size = len(manifest)

        config_body = (
            blobs / descriptor["config"]["digest"].removeprefix("sha256:")
        ).read_bytes()
        self._push_blob(repository, descriptor["config"]["digest"], config_body)
        for layer in descriptor["layers"]:
            self._push_blob(
                repository,
                layer["digest"],
                (blobs / layer["digest"].removeprefix("sha256:")).read_bytes(),
            )

        status, _, body = self.put(
            f"/v2/{repository}/manifests/{tag}",
            manifest,
            {"Content-Type": MANIFEST_MEDIA_TYPE, "Content-Length": str(size)},
        )
        if status != 201:
            raise FixtureError(f"manifest PUT failed: {status} {body!r}")
        return digest

    def _push_blob(self, repository: str, digest: str, body: bytes) -> None:
        # The distribution flow is POST to open the session, then PUT to the
        # returned location with the digest attached.
        status, headers, payload = self._send(
            "POST",
            f"/v2/{repository}/blobs/uploads/",
            body=b"",
            headers={"Content-Length": "0"},
        )
        if status != 202:
            raise FixtureError(f"blob upload rejected: {status} {payload!r}")
        location = headers.get("Location") or headers.get("location")
        if not location:
            raise FixtureError(f"blob upload returned no Location: {headers!r}")
        path = location.split("/v2/", 1)[-1] if "/v2/" in location else location
        separator = "&" if "?" in path else "?"
        status, _, payload = self._send(
            "PUT",
            f"/v2/{path}{separator}digest={digest}",
            body=body,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(body)),
            },
        )
        if status != 201:
            raise FixtureError(f"blob PUT failed: {status} {payload!r}")


@dataclasses.dataclass(frozen=True)
class Check:
    name: str
    repository: str
    reference: str
    expect_present: bool
    because: str

    def outcome(self, registry: DisposableRegistry) -> dict[str, Any]:
        present = registry.manifest_exists(self.repository, self.reference)
        return {
            "name": self.name,
            "repository": self.repository,
            "reference": self.reference,
            "expected": "present" if self.expect_present else "collected",
            "observed": "present" if present else "collected",
            "passed": present is self.expect_present,
            "because": self.because,
        }


def retention_tag(digest: str) -> str:
    """The tag name the reconciler would mint for a digest."""
    return f"retention-deployed-{digest.removeprefix('sha256:')[:16]}"


def run(binary: str, workspace: pathlib.Path) -> dict[str, Any]:
    build = zot_build(binary)
    version = zot_version(binary)
    layouts = workspace / "layouts"
    layouts.mkdir(parents=True)
    kept = build_image(layouts / "kept", b"held by a retention tag")
    dropped = build_image(layouts / "dropped", b"loses every tag")
    repointed = build_image(layouts / "repointed", b"superseded digest")
    successor = build_image(layouts / "successor", b"what the tag points at now")

    port = free_port()
    with DisposableRegistry(binary, workspace / "registry", port) as registry:
        # A manifest that keeps a tag of its own is the baseline: it must
        # survive, otherwise nothing below means anything.
        registry.push(layouts / "kept", "apps/baseline", "sha-baseline")
        baseline_digest = json.loads(
            (layouts / "kept" / "index.json").read_text(encoding="utf-8")
        )["manifests"][0]["digest"]

        # Only a retention tag holds this one, mirroring the lock's guarantee.
        registry.push(layouts / "kept", "apps/retention", retention_tag(kept))

        # Every tag is removed, so nothing references it any more.
        registry.push(layouts / "dropped", "apps/untagged", "sha-dropped")
        removed = registry.delete("/v2/apps/untagged/manifests/sha-dropped")
        if removed not in {200, 202}:
            raise FixtureError(
                f"removing the tag returned {removed}; "
                f"tags still present: {registry.tags('apps/untagged')}"
            )

        # The retention tag is repointed at a newer digest, exactly the
        # mistaken-pointer case: the old manifest is now unreferenced.
        registry.push(layouts / "repointed", "apps/repointed", retention_tag(repointed))
        registry.push(layouts / "successor", "apps/repointed", retention_tag(repointed))

        checks = [
            Check(
                name="tagged manifest survives collection",
                repository="apps/baseline",
                reference=baseline_digest,
                expect_present=True,
                because="a manifest with a tag is not collectable",
            ),
            Check(
                name="retention tag alone keeps a manifest",
                repository="apps/retention",
                reference=kept,
                expect_present=True,
                because=(
                    "the retention tag is what keeps the locked digest pullable "
                    "once its ordinary tag is gone"
                ),
            ),
            Check(
                name="manifest losing every tag is collected",
                repository="apps/untagged",
                reference=dropped,
                expect_present=False,
                because=(
                    "production sets gc with no retention policy, so an "
                    "untagged manifest is collectable and stops being pullable"
                ),
            ),
            Check(
                name="digest a repointed tag used to name is collected",
                repository="apps/repointed",
                reference=repointed,
                expect_present=False,
                because=(
                    "a retention tag pointing at the wrong digest leaves the "
                    "locked one unreferenced and collectable"
                ),
            ),
            Check(
                name="the digest a repointed tag now names survives",
                repository="apps/repointed",
                reference=successor,
                expect_present=True,
                because="the tag still references it",
            ),
        ]

        try:
            _await_collection(registry, checks)
        except FixtureError as error:
            detail = {
                check.reference: registry.tags(check.repository) for check in checks
            }
            raise FixtureError(
                f"{error}\ntags by repository: {detail}\n"
                f"zot log tail:\n{registry.log_tail()[-1500:]}"
            ) from error
        outcomes = [check.outcome(registry) for check in checks]

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "registry-gc-fixture-report",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "ok" if all(item["passed"] for item in outcomes) else "failed",
        "zot_version": version,
        "zot_build": build,
        "expected_zot_version": EXPECTED_ZOT_VERSION,
        "version_matches_production": version == EXPECTED_ZOT_VERSION,
        "checks": outcomes,
    }


def _await_collection(registry: DisposableRegistry, checks: list[Check]) -> None:
    """Give the collector time to run, returning once the outcome is stable.

    Polling waits for the manifests expected to disappear to actually be gone
    rather than sleeping a fixed interval, so a slow collector does not produce
    a false failure and a fast one is not made to wait out the full budget.
    """
    deadline = time.monotonic() + COLLECT_TIMEOUT
    pending = [check for check in checks if not check.expect_present]
    while pending and time.monotonic() < deadline:
        pending = [
            check
            for check in pending
            if registry.manifest_exists(check.repository, check.reference)
        ]
        if not pending:
            return
        time.sleep(2)
    if pending:
        names = ", ".join(check.reference for check in pending)
        raise FixtureError(f"zot did not collect {names} within {COLLECT_TIMEOUT}s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.registry.gc_fixture")
    parser.add_argument("--report", type=pathlib.Path)
    parser.add_argument("--binary", default="zot")
    args = parser.parse_args(argv)

    if shutil.which(args.binary) is None:
        print(
            f"{args.binary} is not on PATH; the fixture runs the zot release the "
            "registry serves, so run it inside the devshell",
            file=sys.stderr,
        )
        return 2

    workspace = pathlib.Path(tempfile.mkdtemp(prefix="gc-fixture-"))
    try:
        report = run(args.binary, workspace)
    except FixtureError as error:
        report = {
            "schema_version": SCHEMA_VERSION,
            "kind": "registry-gc-fixture-report",
            "status": "failed",
            "error": str(error),
            "checks": [],
        }
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    payload = json.dumps(report, indent=2, sort_keys=True)
    print(payload)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload + "\n", encoding="utf-8")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
