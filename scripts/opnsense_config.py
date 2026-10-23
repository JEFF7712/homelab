from __future__ import annotations

import argparse
import ipaddress
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = {
    "adguard-rewrites.json": "inventory/adguard-rewrites.json",
    "unbound-host-overrides.json": "opnsense_reconciler/unbound-host-overrides.json",
    "fleet.json": "inventory/fleet.json",
    "main.tf.json": "tofu/opnsense/main.tf.json",
    "assignments.json": "opnsense_reconciler/assignments.json",
}
TEXT_OUTPUTS = {"coredns-custom.yaml": "gitops/platform/coredns-custom.yaml"}


def validate(inventory: dict[str, Any]) -> None:
    networks = inventory["networks"]
    subnets: dict[str, ipaddress.IPv4Network] = {}
    for name, network in networks.items():
        subnet = ipaddress.IPv4Network(network["cidr"])
        gateway = ipaddress.IPv4Address(network["gateway"])
        if gateway not in subnet or gateway in {
            subnet.network_address,
            subnet.broadcast_address,
        }:
            raise ValueError(f"{name}: gateway must be a usable address in {subnet}")
        for other_name, other in subnets.items():
            if subnet.overlaps(other):
                raise ValueError(f"{name}: subnet overlaps {other_name}")
        subnets[name] = subnet
        if dhcp := network["dhcp"]:
            for pool in dhcp["pools"]:
                start, end = map(ipaddress.IPv4Address, pool.split("-"))
                if not subnet.network_address < start <= end < subnet.broadcast_address:
                    raise ValueError(f"{name}: DHCP pool {pool} is outside {subnet}")
                if start <= gateway <= end:
                    raise ValueError(f"{name}: DHCP pool includes its gateway")
    for name, device in inventory["devices"].items():
        network = networks[device["network"]]
        subnet = subnets[device["network"]]
        address = ipaddress.IPv4Address(device["address"])
        if address not in subnet or address in {
            subnet.network_address,
            subnet.broadcast_address,
            ipaddress.IPv4Address(network["gateway"]),
        }:
            raise ValueError(f"{name}: device address must be usable in {subnet}")


def synchronize(rendered: Path, root: Path, *, check: bool) -> list[str]:
    pending: list[tuple[Path, str]] = []
    for source, destination in OUTPUTS.items():
        value = json.loads((rendered / source).read_text())
        target = root / destination
        if target.exists() and json.loads(target.read_text()) == value:
            continue
        pending.append((target, json.dumps(value, indent=2, sort_keys=True) + "\n"))
    for source, destination in TEXT_OUTPUTS.items():
        content = (rendered / source).read_text()
        target = root / destination
        if not target.exists() or target.read_text() != content:
            pending.append((target, content))
    if not check:
        for target, content in pending:
            target.write_text(content)
    return [str(target.relative_to(root)) for target, _ in pending]


def provider_options(root: Path, *, check: bool) -> bool:
    with tempfile.TemporaryDirectory(prefix="opnsense-provider-") as temporary:
        directory = Path(temporary)
        for name in ("providers.tf", "variables.tf", ".terraform.lock.hcl"):
            shutil.copyfile(root / "tofu/opnsense" / name, directory / name)
        subprocess.run(
            [
                "tofu",
                f"-chdir={directory}",
                "init",
                "-backend=false",
                "-lockfile=readonly",
                "-input=false",
            ],
            stdout=subprocess.DEVNULL,
            check=True,
        )
        schema = directory / "schema.json"
        with schema.open("w") as handle:
            subprocess.run(
                [
                    "tofu",
                    f"-chdir={directory}",
                    "providers",
                    "schema",
                    "-json",
                ],
                stdout=handle,
                check=True,
            )
        result = subprocess.run(
            [
                "nix",
                "build",
                "--impure",
                "--no-link",
                "--print-out-paths",
                "--no-write-lock-file",
                "--file",
                str(root / "nix/opnsense-provider-options.nix"),
                "--argstr",
                "flakePath",
                str(root / "flake"),
                "--argstr",
                "schemaPath",
                str(schema),
            ],
            text=True,
            stdout=subprocess.PIPE,
            check=True,
        )
        generated = directory / "provider.nix"
        shutil.copyfile(result.stdout.strip(), generated)
        subprocess.run(["nixfmt", str(generated)], check=True)
        target = root / "tofu/opnsense/generated/provider.nix"
        if target.exists() and target.read_text() == generated.read_text():
            return True
        if not check:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(generated.read_text())
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render or check OPNsense Tofunix outputs"
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--rendered", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--provider-options", action="store_true")
    args = parser.parse_args(argv)
    if args.provider_options:
        matches = provider_options(args.root, check=args.check)
        if args.check and not matches:
            parser.exit(
                1, "Stale provider options. Run just opnsense-provider-options.\n"
            )
        return 0
    rendered = args.rendered
    if rendered is None:
        result = subprocess.run(
            [
                "nix",
                "build",
                "--no-link",
                "--print-out-paths",
                "--no-write-lock-file",
                f"path:{args.root}?dir=flake#opnsense-config",
            ],
            text=True,
            stdout=subprocess.PIPE,
            check=True,
        )
        rendered = Path(result.stdout.strip())
    validate(json.loads((rendered / "inventory.json").read_text()))
    if args.validate_only:
        return 0
    changed = synchronize(rendered, args.root, check=args.check)
    if args.check and changed:
        parser.exit(
            1,
            "Stale OPNsense outputs: "
            + ", ".join(changed)
            + ". Run just opnsense-render.\n",
        )
    print(
        "OPNsense outputs match Tofunix source."
        if args.check
        else f"Updated {len(changed)} OPNsense output(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
