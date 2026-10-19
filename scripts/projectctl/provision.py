from __future__ import annotations

import getpass
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import SECRET_NAMES, Configuration
from .errors import Code, ProtocolError
from .http import Api


def credentials(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != SECRET_NAMES:
        raise ProtocolError(Code.INVALID_SPEC)
    for secret in value.values():
        if (
            not isinstance(secret, str)
            or not secret
            or len(secret) > 65536
            or secret != secret.strip()
            or "\n" in secret
            or "\r" in secret
        ):
            raise ProtocolError(Code.INVALID_SPEC)
    if (
        value["forgejo-read"] == value["forgejo-write"]
        or value["woodpecker-read"] == value["woodpecker-write"]
    ):
        raise ProtocolError(Code.INVALID_SPEC)
    return value


def collect(output: Path) -> dict[str, Any]:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
    if output.exists() or output.is_symlink():
        raise ProtocolError(Code.RESOURCE_CONFLICT)
    values = credentials(
        {name: getpass.getpass(f"{name}: ") for name in sorted(SECRET_NAMES)}
    )
    from .codec import canonical

    result = subprocess.run(
        [
            "sops",
            "--encrypt",
            "--input-type",
            "json",
            "--output-type",
            "json",
            "--filename-override",
            str(output.absolute()),
            "/dev/stdin",
        ],
        input=canonical(values),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ProtocolError(Code.DEPENDENCY_UNAVAILABLE)
    descriptor = os.open(
        output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(result.stdout)
        handle.flush()
        os.fsync(handle.fileno())
    return {"encrypted_file": str(output), "credentials": sorted(values)}


def install(
    value: Any, directory: Path, worker_uid: int, worker_gid: int
) -> dict[str, Any]:
    values = credentials(value)
    if (
        not directory.is_absolute()
        or ".." in directory.parts
        or directory.exists()
        or directory.is_symlink()
    ):
        raise ProtocolError(Code.RESOURCE_CONFLICT)
    directory.mkdir(mode=0o700)
    for name, value in values.items():
        path = directory / name
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "w") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        if name.endswith("-read"):
            os.chown(path, worker_uid, worker_gid)
    os.chown(directory, os.geteuid(), worker_gid)
    directory.chmod(0o750)
    return {"directory": str(directory), "installed": sorted(values)}


def verify_identities(config: Configuration) -> dict[str, Any]:
    forgejo, woodpecker = config.apis["forgejo"], config.apis["woodpecker"]
    forgejo_user = forgejo.request("/api/v1/user")
    woodpecker_user = woodpecker.request("/api/user")
    write_users = {}
    for name, api in config.apis.items():
        verifier = Api(
            api.base,
            lambda write, service=name: config.authorization(service, True),
            allow_plaintext=True,
        )
        write_users[name] = verifier.request(
            "/api/v1/user" if name == "forgejo" else "/api/user"
        )
    for user, field in (
        (forgejo_user, "is_admin"),
        (woodpecker_user, "admin"),
        (write_users["forgejo"], "is_admin"),
        (write_users["woodpecker"], "admin"),
    ):
        omitted = False if field == "admin" else None
        if not isinstance(user, dict) or user.get(field, omitted) is not False:
            raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
    for user in (woodpecker_user, write_users["woodpecker"]):
        if user.get("admin_env", False) is not False:
            raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
    if (
        not forgejo_user.get("login")
        or forgejo_user.get("login") != woodpecker_user.get("login")
        or write_users["forgejo"].get("login") != write_users["woodpecker"].get("login")
        or not write_users["forgejo"].get("login")
        or forgejo_user["login"] == write_users["forgejo"]["login"]
    ):
        raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
    for name in ("forgejo-read", "woodpecker-read"):
        info = Path(config.secret_files[name]).stat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
    return {
        "forgejo_reader": forgejo_user.get("login"),
        "woodpecker_reader": woodpecker_user.get("login"),
        "provisioner": write_users["forgejo"]["login"],
        "administrators": False,
        "write_scopes": "operator verification required",
        "registry_acl": "operator verification required",
    }
