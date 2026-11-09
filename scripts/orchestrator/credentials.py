"""Generate the orchestrator's machine credentials straight into SOPS files.

`python -m scripts.orchestrator.credentials generate` creates random values
for the credentials nobody has to type: the client token, the two intake
tokens and the ntfy publish token. It writes each Secret encrypted to the
public age recipient of `gitops/secrets/` (.sops.yaml), passing plaintext to
`sops --encrypt` on stdin only, so no value is printed or stored unencrypted.
Whoever runs it cannot read the result back without the operator's key.

Each intake token is written twice, to the controller's Secret and to the
Secret its sender mounts, because the copies cannot be made later without
decrypting. The files are not in gitops/secrets/kustomization.yaml; the
deployment adds them. The human-issued credentials (Forgejo tokens, the
Claude token) are added by the operator; see
docs/runbooks/orchestrator-credentials.md.
"""

from __future__ import annotations

import argparse
import secrets
import string
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SECRETS = ROOT / "gitops/secrets"
# ntfy access tokens are "tk_" and 29 lowercase letters or digits.
NTFY_ALPHABET = string.ascii_lowercase + string.digits


@dataclass(frozen=True)
class SecretFile:
    path: str
    namespace: str
    name: str
    data: dict[str, str]


def ntfy_token() -> str:
    return "tk_" + "".join(secrets.choice(NTFY_ALPHABET) for _ in range(29))


def machine_secrets() -> list[SecretFile]:
    alertmanager = secrets.token_hex(32)
    sentinel = secrets.token_hex(32)
    return [
        SecretFile(
            "orchestrator.sops.yaml",
            "persistent-agent",
            "orchestrator-secrets",
            {
                "client-token": secrets.token_hex(32),
                "alertmanager-intake-token": alertmanager,
                "sentinel-intake-token": sentinel,
                "ntfy-token": ntfy_token(),
            },
        ),
        SecretFile(
            "orchestrator-intake-alertmanager.sops.yaml",
            "observability",
            "orchestrator-intake-alertmanager",
            {"token": alertmanager},
        ),
        SecretFile(
            "orchestrator-intake-sentinel.sops.yaml",
            "automation",
            "orchestrator-intake-sentinel",
            {"token": sentinel},
        ),
    ]


def manifest(secret: SecretFile) -> str:
    return yaml.safe_dump(
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": secret.name, "namespace": secret.namespace},
            "type": "Opaque",
            "stringData": secret.data,
        },
        sort_keys=False,
    )


def encrypt(secret: SecretFile, sops: str = "sops") -> bytes:
    """Encrypt with the repository's creation rule for the target path."""
    target = SECRETS / secret.path
    result = subprocess.run(
        [
            sops,
            "--encrypt",
            "--encrypted-regex",
            "^(data|stringData)$",
            "--input-type",
            "yaml",
            "--output-type",
            "yaml",
            "--filename-override",
            str(target.relative_to(ROOT)),
            "/dev/stdin",
        ],
        input=manifest(secret).encode(),
        capture_output=True,
        cwd=ROOT,
        check=False,
    )
    if result.returncode != 0:
        # sops reports configuration errors on stderr; it never echoes input.
        raise RuntimeError(
            f"sops failed for {secret.path}: {result.stderr.decode()[-300:]}"
        )
    if b"ENC[AES256_GCM" not in result.stdout:
        raise RuntimeError(f"{secret.path} came back unencrypted")
    return result.stdout


def generate(force: bool, sops: str) -> list[Path]:
    files = machine_secrets()
    targets = [SECRETS / secret.path for secret in files]
    existing = [path for path in targets if path.exists()]
    if existing and not force:
        raise SystemExit(
            "refusing to replace existing credentials: "
            + ", ".join(str(p.relative_to(ROOT)) for p in existing)
            + " (rotate with --force)"
        )
    encrypted = [encrypt(secret, sops) for secret in files]
    for path, content in zip(targets, encrypted):
        path.write_bytes(content)
    return targets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.orchestrator.credentials")
    parser.add_argument("command", choices=("generate",))
    parser.add_argument("--force", action="store_true", help="rotate existing files")
    parser.add_argument("--sops", default="sops")
    args = parser.parse_args(argv)
    for path in generate(args.force, args.sops):
        print(f"wrote {path.relative_to(ROOT)} (encrypted)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
