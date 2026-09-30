"""TEMPORARY verification of the in-process read path against a public registry.

Runs on the feature branch, where no protected credential is available, so it
uses an anonymous public registry. It covers everything the authenticated run
cannot reach from here except the credential itself: URL construction, Accept
negotiation for a multi-arch index, digest computation, that a genuine absence
is reported as a definite error rather than softened, and that reads never shell
out. The credential is a single Authorization header, and a failure to attach it
now says so in the error rather than looking like the registry refusing.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.registry import core  # noqa: E402
from scripts.registry.core import OciClient, RegistryError, TransientRegistryError  # noqa: E402

REPOSITORY = "external-secrets/external-secrets"
INDEX_DIGEST = "sha256:814117b0fd6d121b03e8ba3b6db1cecbe7449a354fc0fc9c4faf73a37aa221b1"

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {name}{(': ' + detail) if detail else ''}")
    if not condition:
        failures.append(name)


def main() -> int:
    # No shell-out for reads, which is the whole point of the change.
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("reads must not shell out to skopeo")

    client = OciClient(retries=1, cache_dir=False)
    original = subprocess.run
    subprocess.run = forbidden  # type: ignore[assignment]
    try:
        # 1. a multi-arch index, read anonymously
        raw = client.raw_manifest(f"ghcr.io/{REPOSITORY}@{INDEX_DIGEST}")
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        check("index digest matches the body", digest == INDEX_DIGEST, digest)
        manifest = json.loads(raw)
        check(
            "index media type negotiated",
            manifest.get("mediaType")
            == "application/vnd.oci.image.index.v1+json",
            str(manifest.get("mediaType")),
        )
        platforms = sorted(
            f"{entry.get('platform', {}).get('os')}/{entry.get('platform', {}).get('architecture')}"
            for entry in manifest.get("manifests", [])
        )
        check(
            "index lists its platforms",
            platforms == ["linux/amd64", "linux/arm64", "linux/ppc64le", "linux/s390x"],
            ", ".join(platforms),
        )

        # 2. the same content by tag, proving tag references resolve
        by_tag = client.raw_manifest(f"ghcr.io/{REPOSITORY}:v2.10.0")
        check(
            "tag reference resolves to the same manifest",
            "sha256:" + hashlib.sha256(by_tag).hexdigest() == INDEX_DIGEST,
        )

        # 3. tag listing
        tags = client.list_tags(f"ghcr.io/{REPOSITORY}")
        check("tag listing returns the locked tag", "v2.10.0" in tags, f"{len(tags)} tags")

        # 4. a genuine absence must be a definite error, not inconclusive
        try:
            client.raw_manifest(f"ghcr.io/{REPOSITORY}:no-such-tag-zzz")
        except TransientRegistryError as error:
            check("absence is a definite error", False, f"got transient: {error}")
        except RegistryError as error:
            check("absence is a definite error", "404" in str(error), str(error)[:70])
        else:
            check("absence is a definite error", False, "no error raised")

        # 5. an unparseable reference is rejected before any request
        try:
            client.raw_manifest("not-a-reference")
        except RegistryError as error:
            check("bad reference rejected locally", "parse" in str(error).lower())
        else:
            check("bad reference rejected locally", False, "no error raised")
    except AssertionError as error:
        failures.append(str(error))
        print(f"  FAIL  {error}")
    finally:
        subprocess.run = original  # type: ignore[assignment]

    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
