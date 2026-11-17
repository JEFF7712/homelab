"""Issue one-hour CI credentials through separate fixed SSH capabilities."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROFILES = {
    "ha": {
        "account": "kube-ci-ha",
        "namespace": "home-assistant",
        "service_account": "ha-deployer",
        "secret": "ha_issuer_ssh_key",
        "legacy_secret": "ha_deployer_kubeconfig",
        "public_key": "ha-issuer.pub",
    },
    "registry-advance": {
        "account": "kube-ci-registry",
        "namespace": "automation",
        "service_account": "registry-advance",
        "secret": "registry_issuer_ssh_key",
        "legacy_secret": "registry_advance_kubeconfig",
        "public_key": "registry-issuer.pub",
    },
}
# Existing HA callers keep their fixed default while the second capability is staged.
ACCOUNT = PROFILES["ha"]["account"]
SUBJECT = "system:serviceaccount:home-assistant:ha-deployer"
NAME = PROFILES["ha"]["secret"]
OLD_NAME = PROFILES["ha"]["legacy_secret"]
API = "https://ci.rupan.dev/api/repos/1"
MAX_DURATION = 3600


def profile_settings(profile: str) -> dict[str, str]:
    if profile not in PROFILES:
        raise ValueError("Unknown fixed CI credential profile")
    return PROFILES[profile]


def subject(profile: str) -> str:
    settings = profile_settings(profile)
    return (
        "system:serviceaccount:"
        + settings["namespace"]
        + ":"
        + settings["service_account"]
    )


def validate_token(
    token: str, now: float | None = None, *, profile: str = "ha"
) -> None:
    """The local API authenticates the token; enforce its requested scope/lifetime."""
    try:
        if len(token) > 16384 or len(token.split(".")) != 3:
            raise ValueError
        encoded = token.split(".")[1]
        claims = json.loads(
            base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        )
        if not isinstance(claims, dict):
            raise ValueError
        current = time.time() if now is None else now
        if (
            claims.get("sub") != subject(profile)
            or type(claims.get("exp")) is not int
            or type(claims.get("iat")) is not int
            or claims["iat"] > current + 60
            or claims["exp"] <= current
            or claims["exp"] - claims["iat"] > MAX_DURATION + 60
            or claims["exp"] > current + MAX_DURATION + 60
        ):
            raise ValueError
    except (ValueError, IndexError, TypeError, KeyError):
        raise ValueError(
            "Issuer returned an invalid, expired or overlong CI token"
        ) from None


def configuration(
    token: str, ca: bytes, server: str, *, profile: str = "ha"
) -> dict[str, Any]:
    settings = profile_settings(profile)
    validate_token(token, profile=profile)
    parsed = urllib.parse.urlparse(server)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.path not in ("", "/")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("A trusted HTTPS Kubernetes endpoint is required")
    if b"-----BEGIN CERTIFICATE-----" not in ca:
        raise ValueError("Kubernetes CA file is not a PEM certificate")
    return {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [
            {
                "name": "homelab",
                "cluster": {
                    "server": server,
                    "certificate-authority-data": base64.b64encode(ca).decode(),
                },
            }
        ],
        "users": [{"name": settings["service_account"], "user": {"token": token}}],
        "contexts": [
            {
                "name": settings["service_account"],
                "context": {
                    "cluster": "homelab",
                    "user": settings["service_account"],
                    "namespace": settings["namespace"],
                },
            }
        ],
        "current-context": settings["service_account"],
    }


def issue(server: str, *, profile: str = "ha") -> dict[str, Any]:
    settings = profile_settings(profile)
    if os.geteuid() != 0:
        raise ValueError("The fixed issuer helper must run as root on the API server")
    result = subprocess.run(
        [
            "kubectl",
            "--kubeconfig=/etc/rancher/k3s/k3s.yaml",
            "--request-timeout=20s",
            "--namespace=" + settings["namespace"],
            "create",
            "token",
            settings["service_account"],
            "--duration=1h",
        ],
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Kubernetes refused the scoped token request")
    ca = Path("/var/lib/rancher/k3s/server/tls/server-ca.crt").read_bytes()
    return configuration(result.stdout.strip(), ca, server, profile=profile)


def private_file(path: Path) -> None:
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("SSH private key must be a private regular file")


def fetch(
    key: Path, known_hosts: Path, output: Path, server: str, *, profile: str = "ha"
) -> None:
    settings = profile_settings(profile)
    private_file(key)
    if not known_hosts.is_file():
        raise ValueError("A pinned SSH known-hosts file is required")
    host = urllib.parse.urlparse(server).hostname
    if not host or host.startswith("-"):
        raise ValueError("Invalid Kubernetes server identity")
    result = subprocess.run(
        [
            "ssh",
            "-F",
            "/dev/null",
            "-T",
            "-i",
            str(key),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "IdentityAgent=none",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts}",
            "-o",
            "GlobalKnownHostsFile=/dev/null",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ForwardAgent=no",
            "-o",
            "ClearAllForwardings=yes",
            "-o",
            "RequestTTY=no",
            f"{settings['account']}@{host}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode or len(result.stdout) > 65536:
        raise RuntimeError("Restricted CI credential issuer failed")
    try:
        received = json.loads(result.stdout)
        token = received["users"][0]["user"]["token"]
        ca = base64.b64decode(
            received["clusters"][0]["cluster"]["certificate-authority-data"],
            validate=True,
        )
        expected = configuration(token, ca, server, profile=profile)
        if received != expected:
            raise ValueError
    except (ValueError, KeyError, TypeError, IndexError):
        raise ValueError("Unexpected scoped kubeconfig from CI issuer") from None
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=output.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(expected, stream)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(method: str, path: str, auth: str, body: Any = None) -> Any:
    req = urllib.request.Request(
        API + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {auth}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(
            req, timeout=30
        ) as response:
            data = response.read(1048577)
    except urllib.error.HTTPError as error:
        if error.code == 404 and method == "GET":
            return None
        raise RuntimeError(
            f"Woodpecker {method} failed with HTTP {error.code}"
        ) from None
    except (OSError, urllib.error.URLError):
        raise RuntimeError(f"Woodpecker {method} request failed") from None
    if len(data) > 1048576:
        raise ValueError("Oversized Woodpecker response")
    try:
        return json.loads(data) if data else None
    except ValueError:
        raise ValueError("Invalid Woodpecker response") from None


def provisioning_plan(auth: str, *, profile: str = "ha") -> dict[str, Any]:
    settings = profile_settings(profile)
    repository = request("GET", "", auth)
    if (
        not isinstance(repository, dict)
        or repository.get("full_name") != "JEFF7712/homelab"
    ):
        raise ValueError("Woodpecker repository identity does not match homelab")
    old = request("GET", f"/secrets/{settings['legacy_secret']}", auth)
    if (
        not isinstance(old, dict)
        or old.get("name") != settings["legacy_secret"]
        or old.get("repo_id") != 1
    ):
        raise ValueError("Existing CI credential metadata is unavailable")
    events = old.get("events")
    if (
        not isinstance(events, list)
        or not events
        or any(
            not isinstance(event, str) or event.startswith("pull_request")
            for event in events
        )
        or not isinstance(old.get("images"), list)
    ):
        raise ValueError(
            "Existing HA secret has unsafe or unspecified event/image restrictions"
        )
    metadata = {
        "name": settings["secret"],
        "events": events,
        "images": old["images"],
        "note": f"Restricted {profile} kubeconfig issuer SSH key; no Kubernetes bearer token",
    }
    return {
        "metadata": metadata,
        "sha256": hashlib.sha256(
            json.dumps(metadata, sort_keys=True).encode()
        ).hexdigest(),
    }


def provision(auth: str, key: Path, expected: str, *, profile: str = "ha") -> None:
    settings = profile_settings(profile)
    if os.environ.get("HOMELAB_AUTHORIZED") != "1":
        raise ValueError("Provision requires HOMELAB_AUTHORIZED=1 after exact approval")
    plan = provisioning_plan(auth, profile=profile)
    if plan["sha256"] != expected:
        raise ValueError("CI credential metadata changed since the reviewed plan")
    if request("GET", f"/secrets/{settings['secret']}", auth) is not None:
        raise ValueError(
            "CI issuer secret already exists; review its identity before replacement"
        )
    private_file(key)
    private = key.read_text()
    if not private.startswith("-----BEGIN OPENSSH PRIVATE KEY-----"):
        raise ValueError("Expected an OpenSSH private identity")
    derived = subprocess.run(
        ["ssh-keygen", "-y", "-f", str(key)],
        capture_output=True,
        text=True,
        check=False,
    )
    expected_public = " ".join(
        (ROOT / "config/ci" / settings["public_key"]).read_text().split()[:2]
    )
    if derived.returncode or " ".join(derived.stdout.split()[:2]) != expected_public:
        raise ValueError(
            "CI issuer private identity does not match approved public key"
        )
    request("POST", "/secrets", auth, {**plan["metadata"], "value": private})
    after = request("GET", f"/secrets/{settings['secret']}", auth)
    if not isinstance(after, dict) or any(
        after.get(k) != v for k, v in plan["metadata"].items()
    ):
        raise RuntimeError(
            "CI issuer secret metadata verification failed; legacy HA credential remains available"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    issuer = sub.add_parser("issue")
    issuer.add_argument("--server", required=True)
    client = sub.add_parser("fetch")
    client.add_argument("--server")
    client.add_argument("--key-file", type=Path, required=True)
    client.add_argument("--known-hosts", type=Path, required=True)
    client.add_argument("--output", type=Path, required=True)
    plan = sub.add_parser("provision-plan")
    plan.add_argument("--api-token-file", type=Path, required=True)
    apply = sub.add_parser("provision")
    apply.add_argument("--api-token-file", type=Path, required=True)
    apply.add_argument("--key-file", type=Path, required=True)
    apply.add_argument("--expected-metadata-sha256", required=True)
    for command in (issuer, client, plan, apply):
        command.add_argument("--profile", choices=tuple(PROFILES), default="ha")
    args = parser.parse_args()
    if args.mode == "issue":
        print(json.dumps(issue(args.server, profile=args.profile)))
    elif args.mode == "fetch":
        server = (
            args.server
            or json.loads((ROOT / "inventory/fleet.json").read_text())["cluster_api"]
        )
        fetch(
            args.key_file, args.known_hosts, args.output, server, profile=args.profile
        )
        print("Obtained one-hour scoped CI credentials")
    elif args.mode == "provision-plan":
        print(
            json.dumps(
                provisioning_plan(
                    args.api_token_file.read_text().strip(), profile=args.profile
                )
            )
        )
    else:
        provision(
            args.api_token_file.read_text().strip(),
            args.key_file,
            args.expected_metadata_sha256,
            profile=args.profile,
        )
        print("Provisioned restricted CI issuer identity; legacy credential retained")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        raise SystemExit(
            "CI credential operation failed; inspect prerequisites without exposing credential output"
        ) from None
