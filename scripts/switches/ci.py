"""Send source-bound data requests to the installed management-host worker."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from scripts.switches.model import TARGETS, canonical, validate_inventory
from scripts.switches.runner import blueprint_digest

ROOT = Path(__file__).resolve().parents[2]


def request(
    action: str,
    name: str,
    revision: str,
    inventory: dict,
    backend: dict,
    receipt: dict | None = None,
) -> dict:
    if (
        action not in {"plan", "apply"}
        or name not in TARGETS
        or not re.fullmatch(r"[0-9a-f]{40}", revision)
    ):
        raise ValueError(
            "An exact source revision and known switch operation are required"
        )
    validate_inventory(inventory)
    payload = {
        "switch": name,
        "source_revision": revision,
        "inventory": inventory,
        "blueprint_sha256": blueprint_digest(ROOT / "tofu/switches"),
        "backend": backend,
    }
    if receipt is not None:
        payload["receipt"] = receipt
    command = [
        "ssh",
        "-i",
        os.environ["SSH_DEPLOY_KEY"],
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "UserKnownHostsFile=" + os.environ["HOSTS_KNOWN"],
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "rupan@10.0.30.10",
        "sudo -n /run/current-system/sw/bin/switch-tofu " + action,
    ]
    result = subprocess.run(
        command, input=canonical(payload), capture_output=True, timeout=900
    )
    if result.returncode:
        error = result.stderr.decode(errors="replace")
        error = error.replace(backend["password"], "[REDACTED]")
        raise ValueError("Management-host operation failed: " + error[-6000:])
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["plan", "apply"])
    args = parser.parse_args()
    revision = os.environ["CI_COMMIT_SHA"]
    inventory = json.loads((ROOT / "tofu/switches/inventory.json").read_text())
    backend = {
        "username": os.environ["TF_HTTP_USERNAME"],
        "password": os.environ["TF_HTTP_PASSWORD"],
        "ca_pem": Path(os.environ["STATE_CA_FILE"]).read_text(),
    }
    artifacts = ROOT / "artifacts/switches"
    artifacts.mkdir(parents=True, exist_ok=True)
    for name in sorted(TARGETS):
        path = artifacts / (name + "-plan.json")
        receipt = json.loads(path.read_text()) if args.action == "apply" else None
        result = request(args.action, name, revision, inventory, backend, receipt)
        output = path if args.action == "plan" else artifacts / (name + "-apply.json")
        output.write_bytes(canonical(result))
        if args.action == "apply":
            verification = request("plan", name, revision, inventory, backend)
            (artifacts / (name + "-verify.json")).write_bytes(canonical(verification))
            if not verification["review"]["zero_change"]:
                raise ValueError(f"Post-apply verification found drift on {name}")
        print(
            json.dumps(
                {"switch": name, "operation": args.action, "review": result["review"]}
            )
        )


if __name__ == "__main__":
    main()
