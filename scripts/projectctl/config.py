from __future__ import annotations

import base64
import hashlib
import os
import stat
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .authorization import Policy
from .codec import MAX_BYTES, digest, loads
from .errors import Code, ProtocolError
from .http import Api
from .models import DIGEST, REPOSITORY, Platform, Project, number, obj, string

SECRET_NAMES = {
    "forgejo-read",
    "forgejo-write",
    "woodpecker-read",
    "woodpecker-write",
    "registry-password",
}


def _systemd_credential_file(descriptor: int, info: os.stat_result) -> bool:
    if (
        info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o440
        or not os.fstatvfs(descriptor).f_flag & os.ST_RDONLY
    ):
        return False
    # systemd's group mode bit is an ACL mask; the owning group has no access.
    entries = (
        (0x01, 4, 0xFFFFFFFF),
        (0x02, 4, os.geteuid()),
        (0x04, 0, 0xFFFFFFFF),
        (0x10, 4, 0xFFFFFFFF),
        (0x20, 0, 0xFFFFFFFF),
    )
    expected = struct.pack("<I", 2) + b"".join(
        struct.pack("<HHI", *entry) for entry in entries
    )
    return os.getxattr(descriptor, "system.posix_acl_access") == expected


def read_secret(path: Path, *, systemd_credential: bool = False) -> str:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "r") as handle:
            info = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid not in {0, os.geteuid()}
                or (
                    info.st_mode & 0o077
                    and not (
                        systemd_credential
                        and _systemd_credential_file(descriptor, info)
                    )
                )
                or info.st_nlink != 1
            ):
                raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
            value = handle.read(65537).strip()
        if not value or len(value) > 65536 or "\n" in value or "\r" in value:
            raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
        return value
    except (OSError, UnicodeError):
        raise ProtocolError(Code.AUTHORIZATION_REQUIRED) from None


def implementation_digest(ci_digest: str) -> str:
    modules = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(Path(__file__).parent.glob("*.py"))
    }
    return digest({"modules": modules, "ci_contract": ci_digest})


@dataclass(frozen=True)
class Scope:
    principal: str
    project_key: str
    repository: str
    visibility: str
    routine: dict[str, Any] | None


class Configuration:
    def __init__(self, value: Any):
        row = obj(
            value,
            "schema_version platform endpoints scopes trusted_keys secret_files state_directory clone_identity registry deployed_policy_file expected_ci_digest",
        )
        self.platform = Platform.parse(row["platform"])
        self.state_directory = self._path(row["state_directory"])
        self.deployed_policy_file = self._path(row["deployed_policy_file"])
        self.expected_ci_digest = string(row["expected_ci_digest"], DIGEST)
        self.clone_identity = string(row["clone_identity"])
        registry = obj(row["registry"], "address username actions")
        if registry["actions"] != ["read"]:
            raise ProtocolError(Code.INVALID_SPEC)
        self.registry = registry
        self.registry_address = string(
            registry["address"], r"[A-Za-z0-9][A-Za-z0-9.:-]{0,252}"
        )
        self.registry_username = string(registry["username"])
        self.secret_files = obj(row["secret_files"], " ".join(sorted(SECRET_NAMES)))
        for key, path in self.secret_files.items():
            self._path(path)
        if not isinstance(row["trusted_keys"], dict) or not isinstance(
            row["scopes"], list
        ):
            raise ProtocolError(Code.INVALID_SPEC)
        self.trusted_keys: dict[str, Ed25519PublicKey] = {}
        try:
            for key_id, encoded in row["trusted_keys"].items():
                string(key_id)
                self.trusted_keys[key_id] = Ed25519PublicKey.from_public_bytes(
                    base64.b64decode(encoded, validate=True)
                )
        except (ValueError, TypeError):
            raise ProtocolError(Code.INVALID_SPEC) from None
        self.scopes = []
        for item in row["scopes"]:
            scope = obj(item, "principal project_key repository visibility routine")
            routine = scope["routine"]
            if routine is not None:
                routine = obj(routine, "repository_id default_branch ci_contract")
                number(routine["repository_id"])
                string(routine["default_branch"], r"[a-zA-Z0-9][a-zA-Z0-9_./-]{0,127}")
                string(routine["ci_contract"], "python-uv-v1")
            self.scopes.append(
                Scope(
                    string(scope["principal"]),
                    string(scope["project_key"]),
                    string(scope["repository"], REPOSITORY),
                    string(scope["visibility"], "private|public"),
                    routine,
                )
            )
        identities = [(scope.principal, scope.project_key) for scope in self.scopes]
        if len(identities) != len(set(identities)):
            raise ProtocolError(Code.INVALID_SPEC)
        endpoints = obj(row["endpoints"], "forgejo woodpecker")
        self.bindings = {
            "endpoints": endpoints,
            "clone_identity": self.clone_identity,
            "registry": registry,
            "trusted_keys": row["trusted_keys"],
        }
        self.apis = {}
        for name, endpoint in endpoints.items():
            endpoint = obj(endpoint, "url allow_plaintext")
            if type(endpoint["allow_plaintext"]) is not bool:
                raise ProtocolError(Code.INVALID_SPEC)
            self.apis[name] = Api(
                endpoint["url"],
                lambda write, service=name: self.authorization(service, write),
                allow_plaintext=endpoint["allow_plaintext"],
            )

    @staticmethod
    def _path(value: Any) -> Path:
        if (
            not isinstance(value, str)
            or not Path(value).is_absolute()
            or ".." in Path(value).parts
        ):
            raise ProtocolError(Code.INVALID_SPEC)
        return Path(value)

    @classmethod
    def load(cls, path: Path) -> Configuration:
        try:
            with path.open("rb") as handle:
                value = loads(handle.read(MAX_BYTES + 1))
        except OSError:
            raise ProtocolError(Code.AUTHORIZATION_REQUIRED) from None
        return cls(value)

    def secret(self, name: str, *, write: bool = False) -> str:
        if name not in SECRET_NAMES:
            raise ProtocolError(Code.INVALID_SPEC)
        directory = os.environ.get("CREDENTIALS_DIRECTORY")
        if write:
            if not directory:
                raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
            return read_secret(Path(directory) / name, systemd_credential=True)
        return read_secret(Path(self.secret_files[name]))

    def authorization(self, service: str, write: bool) -> str:
        value = self.secret(f"{service}-{'write' if write else 'read'}", write=write)
        return ("token " if service == "forgejo" else "Bearer ") + value

    def policy(self, principal: str, project: Project) -> Policy:
        matches = [
            scope
            for scope in self.scopes
            if scope.principal == principal
            and scope.project_key == project.project_key
            and scope.repository == project.repository
            and scope.visibility == project.visibility
        ]
        if len(matches) != 1:
            raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
        routine = matches[0].routine
        if routine is not None and (
            project.default_branch != routine["default_branch"]
            or project.ci_contract != routine["ci_contract"]
        ):
            raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
        return Policy(
            self.platform,
            principal,
            project.project_key,
            project.repository,
            project.visibility,
            digest(
                {
                    "implementation": implementation_digest(self.expected_ci_digest),
                    "bindings": self.bindings,
                    "routine": routine,
                }
            ),
            routine_onboarding=routine is not None,
        )
