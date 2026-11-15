"""Render and check all typed infrastructure declarations and documentation."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from scripts import opnsense_config
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

TEXT_OUTPUTS = {"switches/port-map.md": "docs/network/switch-port-map.md"}


def synchronize(rendered: Path, root: Path, *, check: bool) -> list[str]:
    # Validate everything before writing anything, including the independent
    # runtime switch identity and membership contract.
    values = {name: json.loads((rendered / name).read_text()) for name in OUTPUTS}
    validate_inventory(values["switches/inventory.json"])
    texts = {name: (rendered / name).read_text() for name in TEXT_OUTPUTS}
    changed = []
    for name, value in values.items():
        target = root / "tofu" / name
        if target.exists() and json.loads(target.read_text()) == value:
            continue
        changed.append("tofu/" + name)
    for source, target in TEXT_OUTPUTS.items():
        path = root / target
        if not path.exists() or path.read_text() != texts[source]:
            changed.append(target)
    if not check:
        for name in changed:
            if name in TEXT_OUTPUTS.values():
                continue
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(values[name.removeprefix("tofu/")], indent=2, sort_keys=True)
                + "\n"
            )
        for source, target in TEXT_OUTPUTS.items():
            if target in changed:
                path = root / target
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(texts[source])
    return changed


def synchronize_all(rendered: Path, root: Path, *, check: bool) -> list[str]:
    # Plan both output sets before changing either. A failed inventory or a
    # missing output must not leave the other stack partially rendered.
    opnsense = rendered / "opnsense"
    opnsense_config.validate(json.loads((opnsense / "inventory.json").read_text()))
    changed = opnsense_config.synchronize(opnsense, root, check=True)
    changed += synchronize(rendered, root, check=True)
    if not check:
        opnsense_config.synchronize(opnsense, root, check=False)
        synchronize(rendered, root, check=False)
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
        changed = synchronize_all(rendered, args.root, check=args.check)
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
