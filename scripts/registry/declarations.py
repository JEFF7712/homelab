"""Lock the upstream images a declaration file names but the lock does not.

Renovate bumps the tagged upstream references in the inventory-only
declaration files (`gitops/**/registry-sources.yaml` and
`gitops/producer-base-images/base-images.yaml`). A bump alone fails the
registry check, because every declared image must be an exact locked digest.
Run as a Renovate post-upgrade task, this appends the inventory and lock
records for the new references to the same branch, so merging it lets the
main push's `registry-lock-import` mirror the image. Workloads still move to
the new mirror digest in their own change.

Records are only appended: existing IDs and order stay put (a full
regeneration renamed first-party IDs once, #570). A tag the mirror already
holds on another digest is left off the new record, because the import never
repoints a destination tag; the retention tag still protects the digest.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from .core import (
    OciClient,
    RegistryError,
    discover_inventory,
    load_inventory,
    load_lock,
    resolve_inventory,
    validate_lock,
)

INVENTORY = pathlib.Path("registry/images.inventory.json")
LOCK = pathlib.Path("registry/images.lock.json")
BASE_IMAGES = "gitops/producer-base-images/base-images.yaml"


def is_declaration(consumer: str) -> bool:
    path = consumer.rsplit(":", 1)[0]
    return path == BASE_IMAGES or (
        path.startswith("gitops/") and path.endswith("/registry-sources.yaml")
    )


def pending_declarations(
    discovered: Mapping[str, Any],
    inventory: Mapping[str, Any],
    lock: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Discovered upstream records only declaration files name, not yet locked."""
    known = {item["source"]["reference"] for item in inventory["images"]}
    known |= {record["source"]["reference"] for record in lock["images"]}
    locked_ids = {record["id"] for record in lock["images"]}
    pending = []
    for item in discovered["images"]:
        consumers = item.get("consumers") or []
        if item.get("kind") != "upstream" or not consumers:
            continue
        if not all(is_declaration(consumer) for consumer in consumers):
            continue
        if item["source"]["reference"] in known or item["id"] in locked_ids:
            continue
        pending.append(item)
    return pending


def without_conflicting_tags(
    records: Sequence[Mapping[str, Any]], lock: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Drop destination tags the mirror already holds on a different digest."""
    owners: dict[tuple[str, str], str] = {}
    for record in lock["images"]:
        for tag in record["destination_tags"]:
            owners.setdefault((record["destination_repository"], tag), record["digest"])
    kept = []
    for record in records:
        tags = [
            tag
            for tag in record["destination_tags"]
            if owners.get((record["destination_repository"], tag), record["digest"])
            == record["digest"]
        ]
        kept.append({**record, "destination_tags": tags})
    return kept


def lock_declarations(root: pathlib.Path, client: OciClient) -> list[str]:
    """Append records for newly declared images; return their references."""
    inventory_path, lock_path = root / INVENTORY, root / LOCK
    inventory = load_inventory(inventory_path)
    lock = load_lock(lock_path)
    pending = pending_declarations(discover_inventory(root), inventory, lock)
    if not pending:
        return []
    candidate, unresolved = resolve_inventory(
        {"images": pending, "unresolved_inputs": []}, client
    )
    if unresolved:
        raise RegistryError(
            "could not resolve declared images: "
            + "; ".join(f"{item['input']}: {item['reason']}" for item in unresolved)
        )
    records = without_conflicting_tags(candidate["images"], lock)
    updated_lock = {**lock, "images": [*lock["images"], *records]}
    errors = validate_lock(updated_lock)
    if errors:
        raise RegistryError(
            "lock with new declarations is invalid: " + "; ".join(errors)
        )
    _append_json(inventory_path, inventory, pending)
    _append_json(lock_path, lock, records)
    return [item["source"]["reference"] for item in pending]


def _sorted(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sorted(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_sorted(item) for item in value]
    return value


def _append_json(
    path: pathlib.Path, document: Mapping[str, Any], new: Sequence[Mapping[str, Any]]
) -> None:
    """Append images without reordering existing keys, so the diff is only the new
    records (atomic_write_json sorts keys, which some committed records are not)."""
    updated = {**document, "images": [*document["images"], *map(_sorted, new)]}
    text = json.dumps(updated, indent=2) + "\n"
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.registry.declarations")
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args(argv)
    client = OciClient(timeout=args.timeout, retries=2, cache_dir=False)
    try:
        added = lock_declarations(args.root, client)
    except RegistryError as error:
        print(f"registry declarations: {error}", file=sys.stderr)
        return 1
    for reference in added:
        print(f"locked {reference}")
    if not added:
        print("no new declared images")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
