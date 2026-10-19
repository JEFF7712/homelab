from __future__ import annotations

import base64
import getpass
import json
import os
import secrets
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .__main__ import Parser, read_document
from .authorization import SIGNATURE_PREFIX, Approval
from .codec import MAX_BYTES, canonical, digest, document, loads
from .config import Configuration
from .errors import Code, ProtocolError, success
from .models import Plan, obj, stamp, string
from .proofs import PROBE_DOMAIN, probe_payload
from .provision import collect, install, verify_identities
from .runtime import Runtime


def private_key(path: Path) -> Ed25519PrivateKey:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid not in {0, os.geteuid()}
                or info.st_mode & 0o077
                or info.st_nlink != 1
            ):
                raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
            raw = handle.read(16385)
        if len(raw) > 16384:
            raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
        loader = (
            serialization.load_ssh_private_key
            if raw.startswith(b"-----BEGIN OPENSSH PRIVATE KEY-----")
            else serialization.load_pem_private_key
        )
        try:
            key = loader(raw, password=None)
        except TypeError:
            if not sys.stdin.isatty():
                raise ProtocolError(Code.AUTHORIZATION_REQUIRED) from None
            key = loader(
                raw, password=getpass.getpass("Operator key passphrase: ").encode()
            )
        if not isinstance(key, Ed25519PrivateKey):
            raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
        return key
    except (OSError, ValueError, TypeError):
        raise ProtocolError(Code.AUTHORIZATION_REQUIRED) from None


def signed(
    payload: dict[str, Any], domain: bytes, path: Path, key_id: str
) -> dict[str, Any]:
    string(key_id)
    signature = private_key(path).sign(domain + canonical(payload))
    return {
        "schema_version": 1,
        "payload": payload,
        "key_id": key_id,
        "signature": base64.b64encode(signature).decode(),
    }


def build_parser() -> Parser:
    parser = Parser(prog="projectctl-operator")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    collection = commands.add_parser("collect-credentials")
    collection.add_argument("--output", required=True, type=Path)
    installation = commands.add_parser("install-credentials")
    installation.add_argument("--directory", required=True, type=Path)
    verification = commands.add_parser("verify-credentials")
    verification.add_argument("--config", required=True, type=Path)
    dispatch = commands.add_parser("dispatch")
    dispatch.add_argument("request_id")
    dispatch.add_argument("--config", required=True, type=Path)
    backup = commands.add_parser("backup")
    backup.add_argument("destination", type=Path)
    backup.add_argument("--config", required=True, type=Path)
    export = commands.add_parser("enrollment")
    export.add_argument("request_id")
    export.add_argument("--config", required=True, type=Path)
    approval = commands.add_parser("approve")
    approval.add_argument("request", type=Path)
    approval.add_argument("--context", required=True, type=Path)
    approval.add_argument("--ttl", type=int, default=900)
    probe = commands.add_parser("sign-probe")
    probe.add_argument("probe", type=Path)
    for command in (approval, probe):
        command.add_argument("--key-file", required=True, type=Path)
        command.add_argument("--key-id", required=True)
    return parser


def unwrap(value: Any) -> Any:
    return (
        value["data"] if isinstance(value, dict) and value.get("ok") is True else value
    )


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if args.command == "collect-credentials":
            data = collect(args.output)
        elif args.command == "install-credentials":
            import pwd

            if os.geteuid() != 0:
                raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
            account = pwd.getpwnam("projectctl-worker")
            data = install(
                loads(sys.stdin.buffer.read(MAX_BYTES + 1)),
                args.directory,
                account.pw_uid,
                account.pw_gid,
            )
        elif args.command == "verify-credentials":
            data = verify_identities(Configuration.load(args.config))
        elif args.command == "approve":
            status = unwrap(read_document(args.request))
            context = obj(
                unwrap(read_document(args.context)),
                "schema_version request_id catalog_revision catalog_entry_digest",
            )
            plan = Plan.parse(status["plan"])
            if (
                status["request_id"] != context["request_id"]
                or not 0 < args.ttl <= 3600
                or status["plan_digest"] != digest(document(plan))
            ):
                raise ProtocolError(Code.INVALID_SPEC)
            now = datetime.now(timezone.utc)
            approval = Approval(
                1,
                "grant-" + secrets.token_hex(16),
                status["request_id"],
                status["plan_digest"],
                plan.principal,
                plan.project.project_key,
                plan.platform,
                plan.policy_digest,
                tuple(operation.operation_id for operation in plan.operations),
                stamp(now),
                stamp(now + timedelta(seconds=args.ttl)),
                context["catalog_revision"],
                context["catalog_entry_digest"],
            )
            payload = document(Approval.parse(document(approval)))
            data = signed(payload, SIGNATURE_PREFIX, args.key_file, args.key_id)
        elif args.command == "sign-probe":
            payload = probe_payload(unwrap(read_document(args.probe)))
            data = signed(payload, PROBE_DOMAIN, args.key_file, args.key_id)
        else:
            runtime = Runtime(Configuration.load(args.config))
            if args.command == "backup":
                runtime.journal.backup(args.destination)
                data = {"backup": str(args.destination)}
            elif args.command == "enrollment":
                string(args.request_id)
                data = read_document(
                    runtime.config.state_directory
                    / "artifacts"
                    / (args.request_id + ".json")
                )
            else:
                string(args.request_id)
                with runtime.journal.connection() as db:
                    row = db.execute(
                        "SELECT principal FROM requests WHERE id=?", (args.request_id,)
                    ).fetchone()
                if row is None:
                    raise ProtocolError(Code.AUTHORIZATION_REQUIRED)
                verify_identities(runtime.config)
                data = runtime.worker.run(args.request_id, row["principal"])
        response, code = success(data), 0
    except ProtocolError as error:
        response, code = error.envelope(), error.exit_code
    except Exception:
        error = ProtocolError(Code.INTERNAL_ERROR)
        response, code = error.envelope(), error.exit_code
    print(json.dumps(response, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
