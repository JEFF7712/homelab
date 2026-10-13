from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

BASES = (
    "registry.rupan.dev/upstream/ghcr.io/astral-sh/uv@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58",
    "registry.rupan.dev/upstream/docker.io/nixos/nix@sha256:7a007c766426c1877758ddc5cb87a965ac131fc78c582ce0083d922d51ae945c",
)


def verify_file(path: Path, expected: str) -> None:
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected:
        raise ValueError("Retained build input mismatch: " + path.name)


def source_revision(source: Path) -> str:
    paths = [
        "scripts/voice/build_image.py",
        "services/voice-supply/Dockerfile.nemotron",
        "services/voice-supply/native-sdk-manifest.json",
        "services/voice-supply/native-source-manifest.json",
        "services/voice-supply/native-wheels.json",
        "gitops/voice/nemotron-bridge/bridge.py",
        "gitops/voice/nemotron-bridge/proxy.py",
        "gitops/voice/nemotron-bridge/verify_contract.py",
    ]
    subprocess.run(
        ["git", "-C", str(source), "ls-files", "--error-unmatch", "--", *paths],
        stdout=subprocess.DEVNULL,
        check=True,
    )
    changes = subprocess.check_output(
        ["git", "-C", str(source), "status", "--porcelain", "--", *paths],
        text=True,
    )
    if changes:
        raise ValueError("Native image source has uncommitted changes")
    return subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()


def build(inputs: Path, tag: str, reuse_build_cache: bool = False) -> None:
    source = Path(__file__).resolve().parents[2]
    revision = source_revision(source)
    manifests = source / "services/voice-supply"
    sdk = json.loads((manifests / "native-sdk-manifest.json").read_text())
    native = json.loads((manifests / "native-source-manifest.json").read_text())
    wheels = json.loads((manifests / "native-wheels.json").read_text())
    verify_file(inputs / "nemotron-build-sdk.nar.gz", sdk["sha256"])
    verify_file(inputs / "nemotron-source.tar.gz", native["sha256"])
    for item in wheels:
        verify_file(inputs / "native-wheels" / item["filename"], item["sha256"])
    for image in BASES:
        subprocess.run(
            ["docker", "image", "inspect", image], stdout=subprocess.DEVNULL, check=True
        )
    with tempfile.TemporaryDirectory(
        prefix="native-voice-build-", dir=inputs
    ) as temporary:
        context = Path(temporary)
        shutil.copyfile(inputs / "nemotron-build-sdk.nar.gz", context / "sdk.nar.gz")
        shutil.copyfile(
            inputs / "nemotron-source.tar.gz", context / "native-source.tar.gz"
        )
        shutil.copytree(inputs / "native-wheels", context / "wheels")
        (context / "wheel-inputs.txt").write_text(
            "".join(
                "/inputs/wheels/"
                + item["filename"]
                + " --hash=sha256:"
                + item["sha256"]
                + "\n"
                for item in wheels
            )
        )
        for name in ("bridge.py", "proxy.py", "verify_contract.py"):
            shutil.copyfile(
                source / "gitops/voice/nemotron-bridge" / name, context / name
            )
        shutil.copyfile(manifests / "Dockerfile.nemotron", context / "Dockerfile")
        shutil.copyfile(
            manifests / "native-sdk-manifest.json", context / "native-sdk-manifest.json"
        )
        subprocess.run(
            [
                "docker",
                "build",
                "--network=none",
                *([] if reuse_build_cache else ["--no-cache"]),
                "--pull=false",
                "--build-arg",
                "SOURCE_REVISION=" + revision,
                "-t",
                tag,
                str(context),
            ],
            env={**os.environ, "DOCKER_BUILDKIT": "0"},
            check=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", type=Path)
    parser.add_argument("tag")
    parser.add_argument("--reuse-build-cache", action="store_true")
    args = parser.parse_args()
    build(args.inputs.resolve(), args.tag, args.reuse_build_cache)


if __name__ == "__main__":
    main()
