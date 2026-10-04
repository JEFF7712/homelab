from __future__ import annotations

import argparse
import base64
import json
import os
import re
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def current_source(environment: dict[str, str]) -> None:
    sha = environment["CI_COMMIT_SHA"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Invalid source SHA")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    remote = subprocess.check_output(
        ["git", "ls-remote", "origin", "refs/heads/main"], text=True
    ).split()
    if head != sha or not remote or remote[0] != sha:
        raise ValueError("Operation source must be the current main commit")


def artifact_url(environment: dict[str, str], operation: str) -> str:
    parent = environment["CI_OPERATION_PARENT"]
    if not re.fullmatch(r"[1-9][0-9]*", parent) or not re.fullmatch(
        r"[a-z0-9-]+", operation
    ):
        raise ValueError("Invalid artifact identity")
    return f"https://s3.internal:3902/artifacts/{environment['CI_COMMIT_SHA']}/{parent}/{operation}"


def transfer(
    environment: dict[str, str], operation: str, body: bytes | None = None
) -> bytes:
    authorization = base64.b64encode(
        f"{environment['TF_HTTP_USERNAME']}:{environment['TF_HTTP_PASSWORD']}".encode()
    ).decode()
    request = urllib.request.Request(
        artifact_url(environment, operation),
        data=body,
        headers={"Authorization": f"Basic {authorization}"},
    )
    context = ssl.create_default_context(cafile=environment["STATE_CA_FILE"])
    with urllib.request.urlopen(request, context=context, timeout=60) as response:
        return response.read(33554433)


def pack(root: Path, outputs: list[str], sha: str, operation: str) -> bytes:
    files: dict[str, str] = {}
    for output in outputs:
        path = root / output
        paths = path.rglob("*") if path.is_dir() else [path]
        for candidate in paths:
            if candidate.is_symlink():
                raise ValueError("Artifact symlinks are prohibited")
            if candidate.is_file():
                files[candidate.relative_to(root).as_posix()] = base64.b64encode(
                    candidate.read_bytes()
                ).decode()
    body = json.dumps({"sha": sha, "operation": operation, "files": files}).encode()
    if len(body) > 33554432:
        raise ValueError("Artifact exceeds size limit")
    return body


def unpack(
    root: Path, body: bytes, sha: str, operation: str, outputs: list[str]
) -> None:
    manifest = json.loads(body)
    if manifest["sha"] != sha or manifest["operation"] != operation:
        raise ValueError("Artifact source does not match")
    files = manifest["files"]
    if not files and outputs:
        raise ValueError("Artifact contains no files")
    pending: list[tuple[Path, bytes]] = []
    for name, encoded in files.items():
        path = Path(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not any(
                name == item or item.endswith("/") and name.startswith(item)
                for item in outputs
            )
        ):
            raise ValueError("Artifact path is not allowed")
        destination = root / path
        if not destination.resolve().is_relative_to(root.resolve()):
            raise ValueError("Artifact escapes workspace")
        pending.append((destination, base64.b64decode(encoded, validate=True)))
    for destination, content in pending:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        destination.chmod(0o600)


def run(name: str, catalog: dict[str, Any]) -> None:
    operation = catalog[name]
    environment = dict(os.environ)
    current_source(environment)
    environment["CI_DEFAULT_BRANCH"] = "main"
    with tempfile.TemporaryDirectory(prefix="ci-secrets-") as temporary:
        for variable in operation["file_secrets"]:
            value = environment[variable]
            if environment.get("CI_FILE_SECRET_MODE") == "paths":
                value = Path(value).read_text()
            secret = Path(temporary) / variable
            secret.write_text(value)
            secret.chmod(0o600)
            environment[variable] = str(secret)
        for variable, value in operation["environment"].items():
            if isinstance(value, str):
                environment[variable] = re.sub(
                    r"\$([A-Za-z_][A-Za-z0-9_]*)",
                    lambda match: environment[match[1]],
                    value,
                )
        if environment.get("CI_OPERATION_AUTHORITY") == "gitlab":
            role = "deploy" if operation["mutation"] else "plan"
            environment["TF_HTTP_USERNAME"] = "gitlab-" + role
            environment["TF_HTTP_PASSWORD"] = environment[
                "DR_STATE_" + role.upper() + "_PASSWORD"
            ]
        if "STATE_CA_FILE" in environment:
            environment["TF_HTTP_CLIENT_CA_CERTIFICATE_PEM"] = Path(
                environment["STATE_CA_FILE"]
            ).read_text()
        context = ssl.create_default_context(cafile=environment["STATE_CA_FILE"])
        authorization = base64.b64encode(
            f"{environment['TF_HTTP_USERNAME']}:{environment['TF_HTTP_PASSWORD']}".encode()
        ).decode()
        permit = urllib.request.Request(
            "https://s3.internal:3902/permit",
            data=b"",
            headers={"Authorization": "Basic " + authorization},
        )
        with urllib.request.urlopen(permit, context=context, timeout=10) as response:
            response.read()
        try:
            transfer(environment, name)
        except urllib.error.HTTPError as error:
            if error.code not in {404, 410}:
                raise
            if error.code == 410:
                raise ValueError(
                    "Operation receipt expired; run new validation"
                ) from error
        else:
            raise ValueError("Operation already completed for this validation")
        prerequisites = list(operation["needs"])
        if operation["prerequisite"] and operation["prerequisite"] not in prerequisites:
            prerequisites.append(operation["prerequisite"])
        for prerequisite in prerequisites:
            outputs = catalog[prerequisite]["outputs"]
            unpack(
                ROOT,
                transfer(environment, prerequisite),
                environment["CI_COMMIT_SHA"],
                prerequisite,
                outputs,
            )
        subprocess.run(
            [
                "nix",
                "develop",
                "./flake",
                "-c",
                "bash",
                "-euo",
                "pipefail",
                "-c",
                "umask 077\n" + "\n".join(operation["commands"]),
            ],
            cwd=ROOT,
            env=environment,
            check=True,
        )
        transfer(
            environment,
            name,
            pack(ROOT, operation["outputs"], environment["CI_COMMIT_SHA"], name),
        )


def main() -> None:
    catalog = json.loads((ROOT / "config/ci/operations.json").read_text())
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=sorted(catalog))
    args = parser.parse_args()
    run(args.operation, catalog)


if __name__ == "__main__":
    main()
