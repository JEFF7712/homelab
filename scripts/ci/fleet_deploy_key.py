"""Provision a dedicated fleet CI secret without changing the operator secret.

Run phase one from homelab-04 after its key and every host's public authorization
have deployed. Phase two switches catalog references in a separate change. The
old secret is retained unchanged for rollback; this tool never reads its value.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SERVER = "https://ci.rupan.dev"
REPOSITORY = "/api/repos/1"
SOURCE_NAME = "ssh_deploy_key"
NAME = "fleet_deploy_ssh_key"
METADATA = ("id", "repo_id", "org_id", "name", "events", "images", "note")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(
    method: str, path: str, auth: str, body: Any = None, *, missing_ok: bool = False
) -> Any:
    call = urllib.request.Request(
        SERVER + path,
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={"Authorization": f"Bearer {auth}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(
            call, timeout=30
        ) as response:
            payload = response.read(1_048_577)
    except urllib.error.HTTPError as error:
        if missing_ok and error.code == 404:
            return None
        raise RuntimeError(
            f"Woodpecker {method} failed with HTTP {error.code}"
        ) from None
    except (OSError, urllib.error.URLError):
        raise RuntimeError(f"Woodpecker {method} request failed") from None
    if len(payload) > 1_048_576:
        raise RuntimeError("Oversized Woodpecker response")
    try:
        return json.loads(payload) if payload else None
    except ValueError:
        raise RuntimeError("Invalid Woodpecker response") from None


def metadata(existing: dict[str, Any], *, name: str = SOURCE_NAME) -> dict[str, Any]:
    if existing.get("name") != name or existing.get("repo_id") != 1:
        raise ValueError("Expected repository 1's deployment secret metadata")
    events = existing.get("events")
    if (
        not isinstance(events, list)
        or not events
        or any(not isinstance(event, str) for event in events)
        or set(events) - {"cron", "deployment"}
    ):
        raise ValueError(
            "Existing deployment secret has missing or unsafe event restrictions"
        )
    if not isinstance(existing.get("images"), list):
        raise ValueError(
            "Existing deployment secret has no explicit image restriction list"
        )
    return {key: existing[key] for key in METADATA if key in existing}


def digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def public_identity(root: Path) -> tuple[str, str]:
    public = (root / "config/ci/fleet-deploy.pub").read_text().strip()
    kind, encoded, *_ = public.split()
    if kind != "ssh-ed25519":
        raise ValueError("Fleet deployment identity must be Ed25519")
    fingerprint = (
        base64.b64encode(
            hashlib.sha256(base64.b64decode(encoded, validate=True)).digest()
        )
        .decode()
        .rstrip("=")
    )
    return " ".join((kind, encoded)), "SHA256:" + fingerprint


def source_metadata(auth: str) -> dict[str, Any]:
    repository = request("GET", REPOSITORY, auth)
    if repository.get("full_name") != "JEFF7712/homelab":
        raise ValueError("Woodpecker repository identity does not match homelab")
    return metadata(request("GET", f"{REPOSITORY}/secrets/{SOURCE_NAME}", auth))


def inspect(auth: str, root: Path) -> dict[str, str]:
    current = source_metadata(auth)
    if (
        request("GET", f"{REPOSITORY}/secrets/{NAME}", auth, missing_ok=True)
        is not None
    ):
        raise ValueError(
            "Dedicated CI secret already exists; inspect instead of overwriting it"
        )
    _, fingerprint = public_identity(root)
    return {
        "metadata_sha256": digest(current),
        "ciphertext_sha256": hashlib.sha256(
            (root / "secrets/ci-fleet-deploy.sops.json").read_bytes()
        ).hexdigest(),
        "public_fingerprint": fingerprint,
    }


def verify_hosts(root: Path, private: bytes, known_hosts: Path) -> None:
    if not known_hosts.is_file():
        raise ValueError("A pinned SSH known-hosts file is required")
    expected, _ = public_identity(root)
    # The descriptor is anonymous RAM. No workspace or persistent private copy.
    descriptor = os.memfd_create("homelab-ci-fleet-deploy", flags=0)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(os.dup(descriptor), "wb") as output:
            output.write(private)
            output.flush()
        key = f"/proc/self/fd/{descriptor}"
        derived = subprocess.run(
            ["ssh-keygen", "-y", "-f", key],
            pass_fds=(descriptor,),
            capture_output=True,
            text=True,
            check=False,
        )
        if derived.returncode or " ".join(derived.stdout.split()[:2]) != expected:
            raise ValueError(
                "Runtime private key does not match the declared public identity"
            )
        hosts = json.loads((root / "inventory/fleet.json").read_text())["hosts"]
        if not hosts:
            raise ValueError("An explicit nonempty fleet inventory is required")
        for name, host in sorted(hosts.items()):
            result = subprocess.run(
                [
                    "ssh",
                    "-F",
                    "/dev/null",
                    "-i",
                    key,
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
                    "RequestTTY=no",
                    f"rupan@{host['ip']}",
                    "sudo -n true",
                ],
                pass_fds=(descriptor,),
                capture_output=True,
                check=False,
                timeout=30,
            )
            if result.returncode:
                raise RuntimeError(
                    f"New identity cannot deploy on {name}; no CI secret created"
                )
    finally:
        os.close(descriptor)


def apply(
    auth: str, root: Path, expected: dict[str, str], known_hosts: Path, key_file: Path
) -> None:
    if os.environ.get("HOMELAB_AUTHORIZED") != "1":
        raise ValueError(
            "Apply requires HOMELAB_AUTHORIZED=1 after exact source approval"
        )
    if inspect(auth, root) != expected:
        raise ValueError(
            "Source scopes or encrypted identity changed since the reviewed plan"
        )
    private = key_file.read_bytes()
    verify_hosts(root, private, known_hosts)
    # A slow fleet check must not race a metadata change or another creation.
    if inspect(auth, root) != expected:
        raise ValueError(
            "Source scopes or dedicated secret changed during host verification"
        )
    current = source_metadata(auth)
    if digest(current) != expected["metadata_sha256"]:
        raise ValueError("Source metadata changed before creation")
    body = {key: current[key] for key in ("events", "images", "note") if key in current}
    body.update(name=NAME, value=private.decode())
    try:
        request("POST", f"{REPOSITORY}/secrets", auth, body)
    except RuntimeError:
        raise RuntimeError(
            "New-secret creation failed or is uncertain; inspect it before retrying. Original CI secret is untouched"
        ) from None
    after = metadata(request("GET", f"{REPOSITORY}/secrets/{NAME}", auth), name=NAME)
    for key in ("events", "images", "note"):
        if after.get(key) != current.get(key):
            raise RuntimeError(
                "New CI secret scopes differ; do not activate the new catalog. Original CI secret is untouched"
            )
    if source_metadata(auth) != current:
        raise RuntimeError(
            "Original secret metadata changed externally during provisioning; inspect before cutover"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "apply", "verify"))
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--known-hosts", type=Path)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument(
        "--key-file", type=Path, default=Path("/run/secrets/ci-fleet-deploy-key")
    )
    args = parser.parse_args()
    try:
        if args.mode == "verify":
            if not args.known_hosts:
                raise ValueError("Verify requires --known-hosts")
            verify_hosts(ROOT, args.key_file.read_bytes(), args.known_hosts)
            print("Dedicated identity accepted by every intended fleet host")
            return
        auth = (
            args.token_file.read_text().strip()
            if args.token_file
            else (
                os.environ.get("WOODPECKER_TOKEN")
                or (Path.home() / ".config/woodpecker/token").read_text()
            ).strip()
        )
        if args.mode == "plan":
            report = inspect(auth, ROOT)
            if args.plan:
                descriptor = os.open(
                    args.plan, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                with os.fdopen(descriptor, "w") as output:
                    json.dump(report, output, indent=2)
            else:
                print(json.dumps(report, indent=2))
        else:
            if not args.plan or not args.known_hosts:
                raise ValueError("Apply requires --plan and --known-hosts")
            apply(
                auth,
                ROOT,
                json.loads(args.plan.read_text()),
                args.known_hosts,
                args.key_file,
            )
            print(
                "Dedicated fleet CI secret created; original operator secret unchanged. Validate protected dry-run before cutover"
            )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        raise SystemExit(f"Fleet key operation failed: {error}") from None


if __name__ == "__main__":
    main()
