from __future__ import annotations

import argparse
import datetime
import hashlib
import hmac
import io
import json
import re
import secrets
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from scripts.ci.authority import select
from scripts.ci.migrate_secrets import ROOT, variables
from scripts.ci.state import Store

RECIPIENT = "age1k2vhn663mmw9ancuwm2hfmtfg4xyus9xlvp8rkep4fpvxlqa9gdsms9usl"


def s3_state(name: str, key_id: str, secret: str) -> bytes:
    now = datetime.datetime.now(datetime.UTC)
    stamp, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    path = f"/homelab-tofu-state/{name}/terraform.tfstate"
    payload_hash = hashlib.sha256(b"").hexdigest()
    headers = {
        "host": "s3.internal:3900",
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": stamp,
    }
    signed = ";".join(headers)
    canonical = (
        f"GET\n{path}\n\n"
        + "".join(f"{key}:{value}\n" for key, value in headers.items())
        + f"\n{signed}\n{payload_hash}"
    )
    scope = f"{day}/homelab/s3/aws4_request"
    message = f"AWS4-HMAC-SHA256\n{stamp}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    signing = ("AWS4" + secret).encode()
    for value in [day, "homelab", "s3", "aws4_request"]:
        signing = hmac.new(signing, value.encode(), hashlib.sha256).digest()
    signature = hmac.new(signing, message.encode(), hashlib.sha256).hexdigest()
    headers["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={key_id}/{scope}, SignedHeaders={signed}, Signature={signature}"
    )
    request = urllib.request.Request("http://10.0.30.20:3900" + path, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read(33554433)


def certificate(hostname: str = "s3.internal") -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM), key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--overrides",
        type=Path,
        required=True,
        help="JSON credential overrides, including freshly provisioned Forgejo bot tokens",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite a prepared recovery bundle")
    source = variables()
    source.update(json.loads(args.overrides.read_text()))
    catalog = json.loads((ROOT / "config/ci/operations.json").read_text())
    cert, key = certificate()
    passwords = {
        name: secrets.token_urlsafe(48)
        for name in ["plan", "deploy", "gitlab-plan", "gitlab-deploy"]
    }
    credentials = {
        name: {
            "password": password,
            "role": name.removeprefix("gitlab-"),
            "authority": "gitlab" if name.startswith("gitlab-") else "woodpecker",
        }
        for name, password in passwords.items()
    }
    available = {key.lower(): value for key, value in source.items()}
    available.update(
        state_plan_password=passwords["plan"],
        state_deploy_password=passwords["deploy"],
        state_ca_file=cert.decode(),
    )
    required = {
        item["from_secret"]
        for op in catalog.values()
        for item in op["environment"].values()
        if isinstance(item, dict)
    }
    automatic_events: dict[str, set[str]] = {}
    for event, targets in {
        "push": ["opnsense-plan", "cloudflare-plan", "sync-to-github", "cache-publish"],
        "cron": [
            "registry-drift-check",
            "registry-retention-reconcile",
            "registry-promote-first-party",
        ],
    }.items():
        for target in targets:
            for value in catalog[target]["environment"].values():
                if isinstance(value, dict):
                    automatic_events.setdefault(value["from_secret"], set()).add(event)
    missing = sorted(required - available.keys())
    if missing:
        raise ValueError("Missing explicit credentials: " + ", ".join(missing))
    garage = subprocess.check_output(
        [
            "ssh",
            "rupan@10.0.30.20",
            "sudo bash -c 'set -a; source /persist/garage/garage.env; garage -c /etc/garage.toml key info --show-secret homelab-tofu-key'",
        ],
        text=True,
    )
    key_id = re.search(r"Key ID:\s*(\S+)", garage)
    key_secret = re.search(r"Secret key:\s*(\S+)", garage, re.IGNORECASE)
    if not key_id or not key_secret:
        raise ValueError("Cannot read Garage migration identity")
    with tempfile.TemporaryDirectory(prefix="platform-prepare-") as temporary:
        database = Path(temporary) / "state.sqlite"
        store = Store(database)
        fingerprints = {}
        for name in ["opnsense", "cloudflare"]:
            body = s3_state(name, key_id[1], key_secret[1])
            store.import_state(name, body)
            fingerprints[name] = hashlib.sha256(body).hexdigest()
        select(database, "disabled")
        files = {
            "nas-01/tofu-state/state.sqlite": database.read_bytes(),
            "nas-01/tofu-state/credentials.json": json.dumps(credentials).encode(),
            "nas-01/tofu-state/server.pem": cert,
            "nas-01/tofu-state/server-key.pem": key,
            "woodpecker-secrets.json": json.dumps(
                [
                    {
                        "name": name,
                        "value": available[name],
                        "events": sorted(
                            {"deployment"} | automatic_events.get(name, set())
                        ),
                        "images": [],
                    }
                    for name in sorted(required)
                ]
            ).encode(),
            "gitlab-state-passwords.json": json.dumps(
                {
                    "DR_STATE_PLAN_PASSWORD": passwords["gitlab-plan"],
                    "DR_STATE_DEPLOY_PASSWORD": passwords["gitlab-deploy"],
                }
            ).encode(),
            "state-fingerprints.json": json.dumps(fingerprints).encode(),
        }
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            for name, body in files.items():
                info = tarfile.TarInfo(name)
                info.mode = 0o600
                info.size = len(body)
                tar.addfile(info, io.BytesIO(body))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["age", "--recipient", RECIPIENT, "--output", str(args.output)],
            input=archive.getvalue(),
            check=True,
        )
        args.output.chmod(0o600)
    print(
        "Encrypted platform bundle prepared with authority disabled. State bytes and lineages are preserved."
    )


if __name__ == "__main__":
    main()
