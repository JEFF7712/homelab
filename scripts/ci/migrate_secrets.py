import argparse
import base64
import json
import subprocess
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


def variables() -> dict[str, str]:
    result: dict[str, str] = {}
    production: dict[str, str] = {}
    for page in range(1, 100):
        response: list[dict[str, Any]] = json.loads(
            subprocess.check_output(
                [
                    "glab",
                    "api",
                    f"projects/85910419/variables?per_page=100&page={page}",
                ],
                text=True,
            )
        )
        for entry in response:
            if entry["environment_scope"] == "*":
                result[entry["key"]] = entry["value"]
            elif entry["environment_scope"] == "production":
                production[entry["key"]] = entry["value"]
        if len(response) < 100:
            return result | production
    raise ValueError("Variable pagination did not terminate")


def encrypted_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    document = json.loads(
        subprocess.check_output(
            ["sops", "--decrypt", "--output-type", "json", str(path)]
        )
    )
    if document.get("kind") != "Secret":
        raise ValueError("Encrypted values must be a Kubernetes Secret")
    values = {
        key: base64.b64decode(value, validate=True).decode()
        for key, value in document.get("data", {}).items()
    }
    values.update(document.get("stringData", {}))
    if not all(isinstance(value, str) for value in values.values()):
        raise ValueError("Encrypted secret values must be strings")
    return values


def references() -> set[str]:
    keys: set[str] = set()
    for path in (ROOT / "gitops").rglob("*.yaml"):
        if path.name.endswith(".sops.yaml"):
            continue
        for document in yaml.safe_load_all(path.read_text()):
            if not document or document.get("kind") != "ExternalSecret":
                continue
            spec = document["spec"]
            if spec["secretStoreRef"]["name"] not in {
                "gitlab-project",
                "homelab-secrets",
            }:
                continue
            for item in spec.get("data", []):
                remote = item["remoteRef"]
                keys.add(remote.get("property", remote["key"]))
    return keys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--overrides", type=Path)
    parser.add_argument("--import-gitlab", action="store_true")
    args = parser.parse_args()
    target = ROOT / "gitops/secrets/homelab-values.sops.yaml"
    source = encrypted_values(target)
    if args.import_gitlab:
        source.update(variables())
    if args.overrides:
        source.update(json.loads(args.overrides.read_text()))
    keys = references()
    missing = keys - source.keys()
    if missing:
        raise ValueError(f"Missing secret values: {sorted(missing)}")
    document = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "homelab-values", "namespace": "secret-source"},
        "type": "Opaque",
        "stringData": {key: source[key] for key in sorted(keys)},
    }
    encrypted = subprocess.check_output(
        [
            "sops",
            "--encrypt",
            "--input-type",
            "yaml",
            "--output-type",
            "yaml",
            "--filename-override",
            str(target),
            "/dev/stdin",
        ],
        input=yaml.safe_dump(document).encode(),
    )
    target.write_bytes(encrypted)
    print(f"Encrypted {len(keys)} referenced values; no plaintext export created.")


if __name__ == "__main__":
    main()
