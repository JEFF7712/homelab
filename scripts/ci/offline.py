from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import shlex
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


def verify_image(root: Path) -> str:
    descriptor = json.loads((root / "index.json").read_bytes())["manifests"][0]
    manifest = json.loads(
        (root / "blobs/sha256" / descriptor["digest"][7:]).read_bytes()
    )
    for blob in [descriptor, manifest["config"], *manifest["layers"]]:
        data = (root / "blobs/sha256" / blob["digest"][7:]).read_bytes()
        if (
            len(data) != blob["size"]
            or "sha256:" + hashlib.sha256(data).hexdigest() != blob["digest"]
        ):
            raise ValueError(f"retained OCI blob failed verification: {blob['digest']}")
    return manifest["config"]["digest"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Execute full offline validation in a fresh network-disabled container"
    )
    parser.add_argument("inputs", type=Path, help="retained offline-inputs.json")
    parser.add_argument(
        "proof", type=Path, help="new directory for isolated storage, logs and receipt"
    )
    args = parser.parse_args(argv)
    inputs = args.inputs.resolve()
    specification = json.loads(inputs.read_text())
    root = inputs.parent
    proof = args.proof.resolve()
    proof.mkdir(parents=True, exist_ok=False)
    workspace = proof / "workspace"
    workspace.mkdir()
    engine = [
        "podman",
        "--root",
        str(proof / "storage"),
        "--runroot",
        str(proof / "runroot"),
        "--storage-driver",
        "vfs",
    ]

    def run(arguments: list[str], name: str) -> str:
        with (proof / (name + ".log")).open("w") as log:
            result = subprocess.run(
                [*engine, *arguments], stdout=log, stderr=subprocess.STDOUT, check=False
            )
        if result.returncode:
            raise RuntimeError(
                f"{name} failed ({result.returncode}); see {proof / (name + '.log')}"
            )
        return (proof / (name + ".log")).read_text()

    if run(["image", "ls", "-q"], "initial-images").strip():
        raise RuntimeError("offline builder must start with no images")
    tools = {}
    for name, relative in specification["tools"].items():
        image = root / relative
        tools[name] = verify_image(image)
        run(["pull", "oci:" + str(image)], "load-" + name)
    bundle = root / specification["bundle"]
    if (
        hashlib.sha256(bundle.read_bytes()).hexdigest()
        != specification["bundle_sha256"]
    ):
        raise ValueError("source bundle checksum mismatch")
    restrictions = [
        "--rm",
        "--network=none",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=0:0",
        "--tmpfs=/tmp:rw,nosuid,nodev,mode=1777",
    ]
    source = specification["source"]
    checkout = (
        "git init -q /work/source; git -C /work/source fetch -q /inputs/source.bundle "
        + shlex.quote(specification["ref"])
        + "; git -C /work/source checkout -q --detach "
        + shlex.quote(source)
        + "; test $(git -C /work/source rev-parse HEAD) = "
        + shlex.quote(source)
    )
    run(
        [
            "run",
            *restrictions,
            "--read-only",
            "-v",
            str(bundle) + ":/inputs/source.bundle:ro",
            "-v",
            str(workspace) + ":/work:rw",
            "--entrypoint",
            "/bin/sh",
            tools["git"],
            "-eu",
            "-c",
            checkout,
        ],
        "checkout",
    )
    paths = specification["store_paths"]
    if not paths or any(
        not path.startswith("/nix/store/") or "/" in path[11:] for path in paths
    ):
        raise ValueError("invalid retained store roots")
    configuration = "\n".join(
        [
            "experimental-features = nix-command flakes",
            "sandbox = false",
            "build-users-group =",
            "accept-flake-config = false",
            "require-sigs = true",
            # Nix auto-detects isolation and otherwise disables the file cache too.
            "substitute = true",
            "substituters = file:///inputs/cache",
            "trusted-public-keys = " + " ".join(specification["trusted_public_keys"]),
        ]
    )
    script = "\n".join(
        [
            "test $(wc -l </proc/net/route) -eq 1",
            *["test ! -e " + shlex.quote(path) for path in paths],
            "nix copy --from file:///inputs/cache " + " ".join(map(shlex.quote, paths)),
            "cd /source",
            "nix develop 'path:.?dir=flake' -c bash -euo pipefail -c "
            + shlex.quote(
                "just provision-check-deps; CI_TEST_REPORT=artifacts/ci/offline-tests.xml just check; just fmt-check"
            ),
        ]
    )
    run(
        [
            "run",
            *restrictions,
            "--cap-add=SETUID",
            "--cap-add=SETGID",
            "--memory=8g",
            "--cpus=4",
            "-e",
            "NIX_REMOTE=local",
            "-e",
            "NIX_CONFIG=" + configuration,
            "-v",
            str(workspace / "source") + ":/source:rw",
            "-v",
            str(root / specification["cache"]) + ":/inputs/cache:ro",
            "--entrypoint",
            "/bin/sh",
            tools["nix"],
            "-eu",
            "-c",
            script,
        ],
        "full-ci",
    )
    report = ET.parse(workspace / "source/artifacts/ci/offline-tests.xml").getroot()
    if (
        int(report.get("tests", "0")) == 0
        or int(report.get("failures", "0"))
        or int(report.get("errors", "0"))
    ):
        raise RuntimeError("offline CI did not execute a passing test suite")
    receipt = {
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "source": source,
        "inputs_sha256": hashlib.sha256(inputs.read_bytes()).hexdigest(),
        "initial_image_count": 0,
        "retained_roots_absent_before_import": True,
        "network": "none",
        "credentials": "none",
        "signature_checks_disabled": False,
        "capabilities": ["SETUID", "SETGID"],
        "tests": int(report.get("tests", "0")),
        "skipped": int(report.get("skipped", "0")),
        "result": "full validation and formatting passed",
        "tools": tools,
        "platform_restore": "not tested",
    }
    (proof / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
