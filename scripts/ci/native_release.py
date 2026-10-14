from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

BUILDER = "rupan@10.0.30.14"
REPOSITORY = "apps/jarvis-nemotron"
MODEL = Path(
    "/persist/voice-models/nemotron/nemotron-speech-streaming-en-0.6b.q8_0.gguf"
)
MODEL_SHA = "d9a01898d2a611c8764e23a1c2f45e70bbd5a425dc4de93692ac951dd603812d"


def check_request(request: dict[str, Any]) -> None:
    sha = request.get("sha")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Native release requires an exact source SHA")
    for name in ("number", "parent"):
        value = request.get(name)
        if type(value) is not int or not 0 < value < 1_000_000_000:
            raise ValueError("Native release requires positive pipeline identities")
    for name in ("source_token", "supply_token", "registry_read_password"):
        value = request.get(name)
        if not isinstance(value, str) or not value or "\n" in value or "\r" in value:
            raise ValueError("Native release credential is missing or invalid")
    publisher_password(request["publisher_auth"])


def publisher_password(auth: dict[str, Any]) -> str:
    entries = auth["auths"]
    if set(entries) != {"registry.rupan.dev"}:
        raise ValueError("Native publisher auth must name only the local registry")
    user, password = (
        base64.b64decode(entries["registry.rupan.dev"]["auth"], validate=True)
        .decode()
        .split(":", 1)
    )
    if (
        user != "publisher-jarvis-nemotron"
        or not password
        or "\n" in password
        or "\r" in password
    ):
        raise ValueError("Native release requires its scoped publisher identity")
    return password


def execute(args: list[str], log: Path, **kwargs: Any) -> None:
    with log.open("ab") as output:
        result = subprocess.run(args, stdout=output, stderr=subprocess.STDOUT, **kwargs)
    if result.returncode:
        raise RuntimeError(f"Native release step failed; inspect {log.name}")


def registry_auth(directory: Path, user: str, password: str) -> Path:
    directory.mkdir(mode=0o700)
    path = directory / "config.json"
    path.write_text(
        json.dumps(
            {
                "auths": {
                    "registry.rupan.dev": {
                        "auth": base64.b64encode(f"{user}:{password}".encode()).decode()
                    }
                }
            }
        )
    )
    path.chmod(0o600)
    return directory


def published_receipt(
    request: dict[str, Any], tag: str, retain: bool = True
) -> dict[str, Any]:
    password = publisher_password(request["publisher_auth"])
    auth = base64.b64encode(f"publisher-jarvis-nemotron:{password}".encode()).decode()
    headers = {
        "Authorization": "Basic " + auth,
        "Accept": "application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json",
    }
    base = "https://registry.rupan.dev/v2/" + REPOSITORY + "/"
    with urllib.request.urlopen(
        urllib.request.Request(base + "manifests/" + tag, headers=headers), timeout=60
    ) as response:
        raw = response.read()
        digest = response.headers["Docker-Content-Digest"]
    if "sha256:" + hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("Published native manifest digest mismatch")
    manifest = json.loads(raw)
    config_digest = manifest["config"]["digest"]
    with urllib.request.urlopen(
        urllib.request.Request(base + "blobs/" + config_digest, headers=headers),
        timeout=60,
    ) as response:
        config_raw = response.read()
    if "sha256:" + hashlib.sha256(config_raw).hexdigest() != config_digest:
        raise ValueError("Published native configuration digest mismatch")
    config = json.loads(config_raw)
    if (
        config["config"]["Labels"]["org.opencontainers.image.revision"]
        != request["sha"]
        or config["config"]["User"] != "1000:1000"
        or config["config"]["Entrypoint"] != ["/opt/venv/bin/python", "/app/bridge.py"]
    ):
        raise ValueError("Published native runtime contract mismatch")
    retention = "retention-deployed-" + digest.split(":", 1)[1][:16]
    if retain:
        retain_manifest(base, retention, raw, headers, manifest["mediaType"])
    return {
        "source_revision": request["sha"],
        "pipeline": request["number"],
        "parent_validation": request["parent"],
        "repository": REPOSITORY,
        "tag": tag,
        "digest": digest,
        "config_digest": config_digest,
        "retention_tag": retention if retain else None,
        "model_sha256": MODEL_SHA,
        "cold_build_network": "none",
        "gpu_corpus_verified": True,
        "deployment": "Requires a separate protected promotion PR",
    }


def retain_manifest(
    base: str, retention: str, raw: bytes, headers: dict[str, str], media: str
) -> None:
    with urllib.request.urlopen(
        urllib.request.Request(
            base + "manifests/" + retention,
            data=raw,
            method="PUT",
            headers={**headers, "Content-Type": media},
        ),
        timeout=60,
    ) as response:
        if response.status != 201:
            raise ValueError("Native image retention failed")


def remote(request: dict[str, Any]) -> dict[str, Any]:
    check_request(request)
    root = Path("/persist/native-asr-ci")
    root.mkdir(mode=0o700, exist_ok=True)
    with (root / "release.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        work = root / f"{request['number']}-{request['sha']}"
        work.mkdir(mode=0o700, exist_ok=False)
        source = work / "source"
        log = work / "release.log"
        with tempfile.TemporaryDirectory(prefix="credentials-", dir=work) as temporary:
            protected = Path(temporary)
            askpass = protected / "askpass"
            askpass.write_text(
                '#!/bin/sh\ncase "$1" in *Username*) printf "%s\\n" homelab-ci-source;; '
                '*Password*) printf "%s\\n" "$FORGEJO_SOURCE_READ_TOKEN";; *) exit 1;; esac\n'
            )
            askpass.chmod(0o700)
            git_env = {
                "PATH": os.environ["PATH"],
                "HOME": str(protected),
                "GIT_ASKPASS": str(askpass),
                "GIT_TERMINAL_PROMPT": "0",
                "FORGEJO_SOURCE_READ_TOKEN": request["source_token"],
            }
            execute(["git", "init", "--quiet", str(source)], log, env=git_env)
            execute(
                [
                    "git",
                    "-C",
                    str(source),
                    "-c",
                    "credential.helper=",
                    "fetch",
                    "--depth=1",
                    "https://git.rupan.dev/JEFF7712/homelab.git",
                    request["sha"],
                ],
                log,
                env=git_env,
            )
            execute(
                ["git", "-C", str(source), "checkout", "--detach", "FETCH_HEAD"],
                log,
                env=git_env,
            )
            actual = subprocess.check_output(
                ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
            ).strip()
            if actual != request["sha"]:
                raise ValueError("Native builder cloned an unexpected source commit")
            sys.path.insert(0, str(source))
            from scripts.voice.build_image import BASES
            from scripts.voice.fetch_supply import fetch

            supply = source / "services/voice-supply"
            inputs = work / "inputs"
            fetch(
                supply / "native-inputs-manifest.json", inputs, request["supply_token"]
            )
            fetch(
                supply / "native-wheels-package.json",
                work / "wheels",
                request["supply_token"],
            )
            import shutil

            shutil.move(work / "wheels/native-wheels", inputs / "native-wheels")
            fetch(
                supply / "native-corpus-package.json",
                work / "corpus",
                request["supply_token"],
            )
            (work / "corpus").chmod(0o755)
            for path in (work / "corpus").iterdir():
                if path.is_file():
                    path.chmod(0o644)
            with MODEL.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != MODEL_SHA:
                    raise ValueError("Native acceptance model digest mismatch")
            reader = registry_auth(
                protected / "reader", "node", request["registry_read_password"]
            )
            for image in BASES:
                execute(["docker", "--config", str(reader), "pull", image], log)
            image = "native-ci:" + str(request["number"])
            execute(
                [
                    sys.executable,
                    str(source / "scripts/voice/build_image.py"),
                    str(inputs),
                    image,
                ],
                log,
                env={
                    "PATH": os.environ["PATH"],
                    "HOME": str(protected),
                    "DOCKER_CONFIG": str(protected / "empty-docker-config"),
                },
            )
            execute(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network=none",
                    "--read-only",
                    "--memory=4g",
                    "--cpus=2",
                    "--device=nvidia.com/gpu=all",
                    "--tmpfs",
                    "/tmp:exec,size=256m",
                    "-e",
                    "NEMO_GPU=0",
                    "-e",
                    "NEMO_RIGHT_CTX=6",
                    "-e",
                    "NEMO_INTERIM=0",
                    "-e",
                    "NEMO_MODEL=/inputs/model.gguf",
                    "-v",
                    str(MODEL) + ":/inputs/model.gguf:ro",
                    "-v",
                    str(work / "corpus") + ":/corpus:ro",
                    "--entrypoint",
                    "/opt/venv/bin/python",
                    image,
                    "/app/verify_contract.py",
                    "--manifest",
                    "/corpus/acceptance-manifest.json",
                ],
                log,
            )
            tag = "candidate-ci-" + str(request["number"])
            destination = "registry.rupan.dev/" + REPOSITORY + ":" + tag
            writer = registry_auth(
                protected / "publisher",
                "publisher-jarvis-nemotron",
                publisher_password(request["publisher_auth"]),
            )
            execute(["docker", "tag", image, destination], log)
            execute(["docker", "--config", str(writer), "push", destination], log)
            receipt = published_receipt(request, tag, retain=False)
            (work / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
            return receipt


def call_builder(request: dict[str, Any], mode: str) -> dict[str, Any]:
    check_request(request)
    program = base64.b64encode(Path(__file__).read_bytes()).decode()
    code = f"import base64,sys;sys.argv=['native-release',{mode!r}];exec(compile(base64.b64decode({program!r}),'<server-owned-native-release>','exec'))"
    bootstrap = (
        "set -eu; native_python=''; native_git=''; "
        "for native_path in $(nix-store -q --requisites /run/current-system); do "
        'if test -z "$native_python" && test -x "$native_path/bin/python3"; then native_python=$native_path/bin/python3; fi; '
        'if test -z "$native_git" && test -x "$native_path/bin/git"; then native_git=$native_path/bin; fi; done; '
        'test -n "$native_python"; test -n "$native_git"; '
        'exec sudo -n env PATH="$native_git:/run/current-system/sw/bin" "$native_python" -c '
        + shlex.quote(code)
    )
    result = subprocess.run(
        ["ssh", *shlex.split(os.environ["NIX_SSHOPTS"]), BUILDER, bootstrap],
        input=json.dumps(request),
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError("Native builder failed; inspect its protected release.log")
    receipt = json.loads(result.stdout.splitlines()[-1])
    if (
        receipt["source_revision"] != request["sha"]
        or receipt["pipeline"] != request["number"]
    ):
        raise ValueError("Native builder returned an unexpected receipt")
    return receipt


def proof_job(request: dict[str, Any], digest: str) -> dict[str, Any]:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Native proof requires an immutable digest")
    corpus = f"/persist/native-asr-ci/{request['number']}-{request['sha']}/corpus"
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": "native-asr-proof-" + str(request["number"]),
            "namespace": "voice",
        },
        "spec": {
            "backoffLimit": 0,
            "ttlSecondsAfterFinished": 3600,
            "template": {
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "runtimeClassName": "nvidia",
                    "nodeSelector": {"kubernetes.io/hostname": "homelab-04"},
                    "securityContext": {
                        "runAsUser": 1000,
                        "runAsGroup": 1000,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "proof",
                            "image": "registry.rupan.dev/" + REPOSITORY + "@" + digest,
                            "command": [
                                "/opt/venv/bin/python",
                                "/app/verify_contract.py",
                            ],
                            "args": ["--manifest", "/corpus/acceptance-manifest.json"],
                            "env": [
                                {"name": k, "value": v}
                                for k, v in {
                                    "NVIDIA_VISIBLE_DEVICES": "all",
                                    "NVIDIA_DRIVER_CAPABILITIES": "compute,utility",
                                    "NEMO_GPU": "0",
                                    "NEMO_MODEL": "/inputs/model.gguf",
                                    "NEMO_RIGHT_CTX": "6",
                                    "NEMO_INTERIM": "0",
                                }.items()
                            ],
                            "securityContext": {
                                "readOnlyRootFilesystem": True,
                                "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "1Gi"},
                                "limits": {"cpu": "2", "memory": "4Gi"},
                            },
                            "volumeMounts": [
                                {
                                    "name": "model",
                                    "mountPath": "/inputs/model.gguf",
                                    "readOnly": True,
                                },
                                {
                                    "name": "corpus",
                                    "mountPath": "/corpus",
                                    "readOnly": True,
                                },
                                {"name": "temporary", "mountPath": "/tmp"},
                            ],
                        }
                    ],
                    "volumes": [
                        {
                            "name": "model",
                            "hostPath": {"path": str(MODEL), "type": "File"},
                        },
                        {
                            "name": "corpus",
                            "hostPath": {"path": corpus, "type": "Directory"},
                        },
                        {"name": "temporary", "emptyDir": {"sizeLimit": "256Mi"}},
                    ],
                }
            },
        },
    }


def kubernetes_probe(request: dict[str, Any], digest: str) -> None:
    job = proof_job(request, digest)
    name = job["metadata"]["name"]
    ssh = ["ssh", *shlex.split(os.environ["NIX_SSHOPTS"]), "rupan@10.0.30.11"]
    result = subprocess.run(
        [*ssh, "sudo -n k3s kubectl create -f -"],
        input=json.dumps(job),
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError("Native k3s proof Job creation failed")
    result = subprocess.run(
        [
            *ssh,
            "sudo -n k3s kubectl wait -n voice --for=condition=complete job/"
            + name
            + " --timeout=180s",
        ],
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError("Native k3s proof failed; inspect Job " + name)
    result = subprocess.run(
        [*ssh, "sudo -n k3s kubectl logs -n voice job/" + name],
        capture_output=True,
        check=True,
    )
    path = Path("artifacts/native-asr/kubernetes-proof.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(result.stdout)
    path.chmod(0o600)


def finalize(request: dict[str, Any]) -> dict[str, Any]:
    check_request(request)
    candidate = published_receipt(
        request, "candidate-ci-" + str(request["number"]), retain=False
    )
    if candidate["digest"] != request["digest"]:
        raise ValueError("Native candidate changed after k3s verification")
    tag = "0.0." + str(1_000_000 + request["number"])
    auth = base64.b64encode(
        (
            "publisher-jarvis-nemotron:" + publisher_password(request["publisher_auth"])
        ).encode()
    ).decode()
    base = "https://registry.rupan.dev/v2/" + REPOSITORY + "/manifests/"
    headers = {
        "Authorization": "Basic " + auth,
        "Accept": "application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json",
    }
    with urllib.request.urlopen(
        urllib.request.Request(base + request["digest"], headers=headers), timeout=60
    ) as response:
        raw = response.read()
    if "sha256:" + hashlib.sha256(raw).hexdigest() != request["digest"]:
        raise ValueError("Native accepted manifest digest mismatch")
    manifest = json.loads(raw)
    with urllib.request.urlopen(
        urllib.request.Request(
            base + tag,
            data=raw,
            method="PUT",
            headers={**headers, "Content-Type": manifest["mediaType"]},
        ),
        timeout=60,
    ) as response:
        if response.status != 201:
            raise ValueError("Native accepted release tag creation failed")
    receipt = published_receipt(request, tag)
    receipt["kubernetes_runtime_verified"] = True
    return receipt


def coordinate() -> None:
    request = {
        "sha": os.environ["CI_COMMIT_SHA"],
        "number": int(os.environ["CI_PIPELINE_NUMBER"]),
        "parent": int(os.environ["CI_OPERATION_PARENT"]),
        "source_token": os.environ["FORGEJO_SOURCE_READ_TOKEN"],
        "supply_token": os.environ["FORGEJO_PACKAGE_TOKEN"],
        "registry_read_password": os.environ["REGISTRY_READ_PASSWORD"],
        "publisher_auth": json.loads(
            Path(os.environ["REGISTRY_JARVIS_PUBLISHER_AUTH_FILE"]).read_text()
        ),
    }
    receipt = call_builder(request, "remote")
    kubernetes_probe(request, receipt["digest"])
    receipt = call_builder({**request, "digest": receipt["digest"]}, "finalize")
    path = Path("artifacts/native-asr/receipt.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2) + "\n")
    path.chmod(0o600)
    print("Native image publication verified: " + receipt["digest"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=("coordinate", "remote", "finalize"),
        default="coordinate",
        nargs="?",
    )
    args = parser.parse_args()
    if args.mode == "remote":
        print(json.dumps(remote(json.load(sys.stdin))))
    elif args.mode == "finalize":
        print(json.dumps(finalize(json.load(sys.stdin))))
    else:
        coordinate()


if __name__ == "__main__":
    main()
