"""Render typed switch/Cloudflare declarations and reject stale generated files."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from scripts.switches.model import validate_inventory

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = (
    "switches/inventory.json",
    "switches/main.tf.json",
    "cloudflare/main.tf.json",
    "cloudflare-access/applications.tf.json",
    "cloudflare-access/policies.tf.json",
    "cloudflare-access/imports.tf.json",
)


def synchronize(rendered: Path, root: Path, *, check: bool) -> list[str]:
    # Validate everything before writing anything, including the independent
    # runtime switch identity and membership contract.
    values = {name: json.loads((rendered / name).read_text()) for name in OUTPUTS}
    validate_inventory(values["switches/inventory.json"])
    changed = []
    for name, value in values.items():
        target = root / "tofu" / name
        if target.exists() and json.loads(target.read_text()) == value:
            continue
        changed.append("tofu/" + name)
    if not check:
        for name in changed:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(values[name.removeprefix("tofu/")], indent=2, sort_keys=True)
                + "\n"
            )
    return changed


def provider_options(root: Path, *, check: bool) -> list[str]:
    definitions = json.loads(
        subprocess.check_output(
            ["nix", "eval", "--json", "--file", str(root / "tofu/providers.nix")],
            text=True,
        )
    )
    changed = []
    for name, definition in definitions.items():
        with tempfile.TemporaryDirectory(prefix="tofu-provider-") as temporary:
            directory = Path(temporary)
            # Schema discovery uses only the locked provider, never production
            # backend, imports, encryption keys or runtime credentials.
            (directory / "providers.tf.json").write_text(
                json.dumps(
                    {
                        "terraform": {
                            "required_providers": {
                                definition["name"]: {
                                    "source": definition["source"],
                                    "version": definition["version"],
                                }
                            }
                        }
                    }
                )
            )
            shutil.copyfile(
                root / "tofu" / name / ".terraform.lock.hcl",
                directory / ".terraform.lock.hcl",
            )
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
                    ["tofu", f"-chdir={directory}", "providers", "schema", "-json"],
                    stdout=handle,
                    check=True,
                )
            result = subprocess.check_output(
                [
                    "nix",
                    "build",
                    "--impure",
                    "--no-link",
                    "--print-out-paths",
                    "--no-write-lock-file",
                    "--file",
                    str(root / "tofu/provider-options.nix"),
                    "--argstr",
                    "flakePath",
                    str(root / "flake"),
                    "--argstr",
                    "schemaPath",
                    str(schema),
                    "--argstr",
                    "providerName",
                    name,
                ],
                text=True,
            )
            generated = directory / "provider.nix"
            shutil.copyfile(result.strip(), generated)
            subprocess.run(["nixfmt", str(generated)], check=True)
            target = root / "tofu" / name / "generated/provider.nix"
            content = (
                "\n".join(line.rstrip() for line in generated.read_text().splitlines())
                + "\n"
            )
            if target.exists() and target.read_text() == content:
                continue
            changed.append(str(target.relative_to(root)))
            if not check:
                target.write_text(content)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--rendered", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--provider-options", action="store_true")
    args = parser.parse_args(argv)
    if args.provider_options:
        changed = provider_options(args.root, check=args.check)
        command = "just tofu-provider-options"
    else:
        rendered = args.rendered
        if rendered is None:
            rendered = Path(
                subprocess.check_output(
                    [
                        "nix",
                        "build",
                        "--no-link",
                        "--print-out-paths",
                        "--no-write-lock-file",
                        f"path:{args.root}?dir=flake#tofu-config",
                    ],
                    text=True,
                ).strip()
            )
        changed = synchronize(rendered, args.root, check=args.check)
        command = "just tofu-render"
    if args.check and changed:
        parser.exit(
            1, "Stale Tofunix outputs: " + ", ".join(changed) + f". Run {command}.\n"
        )
    print(
        "Tofunix outputs match Nix source."
        if args.check
        else f"Updated {len(changed)} output(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
