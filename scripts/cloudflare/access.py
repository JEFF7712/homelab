"""Validate evaluated Cloudflare Access plans without printing identities or secrets."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

ACCOUNT = "5d99d63dea23dde67ccb07e5dfb31107"
OWNER = "rupanpandyan@gmail.com"
APPLICATION = "cloudflare_zero_trust_access_application"
POLICY = "cloudflare_zero_trust_access_policy"
OWNER_DESTINATIONS = {
    "*.rupan.dev",
    "tally.rupan.dev",
    "approvals.rupan.dev",
    "sovereign.rupan.dev",
}
TRACKER_PATHS = {"tally.rupan.dev/t.js", "tally.rupan.dev/api/send"}


def owner_only(attached: list[dict[str, Any]]) -> bool:
    return bool(attached) and all(
        p.get("decision") == "allow"
        and without_nulls(p.get("include")) == [{"email": {"email": OWNER}}]
        for p in attached
    )


def resources(module: dict[str, Any]) -> list[dict[str, Any]]:
    result = list(module.get("resources", []))
    for child in module.get("child_modules", []):
        result.extend(resources(child))
    return result


def without_nulls(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: without_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [without_nulls(v) for v in value]
    return value


def validate_plan(plan: dict[str, Any], mode: str = "plan") -> list[str]:
    errors: list[str] = []
    changes = plan.get("resource_changes")
    if (
        plan.get("errored")
        or plan.get("complete") is False
        or not isinstance(changes, list)
    ):
        return ["Invalid or incomplete Access plan"]
    importing = any(r.get("change", {}).get("importing") for r in changes)
    evaluated = resources(plan.get("planned_values", {}).get("root_module", {}))
    policies = {
        r["values"].get("id"): r["values"]
        for r in evaluated
        if r.get("type") == POLICY and r["values"].get("id")
    }
    destinations: dict[str, list[dict[str, Any]]] = {}
    for resource in evaluated:
        if resource.get("mode") == "data":
            continue
        kind = resource.get("type")
        address = resource["address"]
        value = resource.get("values", {})
        if kind not in {APPLICATION, POLICY}:
            errors.append(f"Unexpected resource: {address}")
            continue
        if value.get("account_id") != ACCOUNT:
            errors.append(f"Wrong Access account: {address}")
        if kind != APPLICATION:
            continue
        attached = value.get("policies") or []
        effective = [policies.get(p.get("id"), p) for p in attached]
        if not effective or any(not p.get("decision") for p in effective):
            errors.append(f"Missing or unresolved Access policy: {address}")
        if value.get("type") in {"warp", "saas"} and not owner_only(effective):
            errors.append(f"Owner-only SSO/enrollment policy required: {address}")
        uris = [d.get("uri", "") for d in value.get("destinations") or []]
        if not uris:
            uris = value.get("self_hosted_domains") or [value.get("domain", "")]
        for uri in uris:
            if not uri:
                errors.append(f"Unresolved Access destination: {address}")
                continue
            if uri in destinations:
                errors.append(f"Duplicate Access destination: {uri}")
            destinations[uri] = effective
            hostname = uri.split("/", 1)[0]
            if (
                hostname in OWNER_DESTINATIONS | {"pod.rupan.dev"}
                and uri not in TRACKER_PATHS
                and not owner_only(effective)
            ):
                errors.append(f"Owner-only policy required: {uri}")
            if any(p.get("decision") == "bypass" for p in effective):
                if "*" in hostname:
                    errors.append(f"Wildcard hostname may not bypass Access: {uri}")
                if (
                    hostname
                    in {
                        "tally.rupan.dev",
                        "approvals.rupan.dev",
                        "sovereign.rupan.dev",
                        "pod.rupan.dev",
                    }
                    and uri not in TRACKER_PATHS
                ):
                    errors.append(f"Owner destination may not bypass Access: {uri}")
    for uri in OWNER_DESTINATIONS:
        attached = destinations.get(uri, [])
        if not owner_only(attached):
            errors.append(f"Owner-only policy required: {uri}")
    for uri in TRACKER_PATHS:
        attached = destinations.get(uri, [])
        if not attached or any(
            p.get("decision") != "bypass"
            or without_nulls(p.get("include")) != [{"everyone": {}}]
            or p.get("require")
            or p.get("exclude")
            for p in attached
        ):
            errors.append(f"Public tracker policy required: {uri}")
    if not any(r.get("type") == APPLICATION for r in evaluated):
        errors.append("Access plan contains no applications")
    for resource in changes:
        if resource.get("mode") == "data":
            continue
        change = resource.get("change", {})
        actions = change.get("actions", [])
        if actions not in [
            ["no-op"],
            ["create"],
            ["update"],
            ["delete"],
            ["delete", "create"],
            ["create", "delete"],
        ]:
            errors.append(f"Unrecognized Access change: {resource['address']}")
        if "delete" in actions:
            errors.append(
                f"Access deletion or replacement forbidden: {resource['address']}"
            )
        if (mode in {"import", "drift"} or importing) and actions != ["no-op"]:
            errors.append(
                f"Access {mode} or bootstrap requires no resource changes: {resource['address']}"
            )
        if mode == "drift" and change.get("importing"):
            errors.append(f"Access state import outstanding: {resource['address']}")
    return errors


def inventory_errors(
    stack: Path,
    apps: list[dict[str, Any]],
    policies: list[dict[str, Any]],
    state: dict[str, Any] | None = None,
) -> list[str]:
    imports = json.loads((stack / "imports.tf.json").read_text())["import"]
    declared = {
        f"{kind}.{name}"
        for path in stack.glob("*.tf.json")
        for kind, instances in json.loads(path.read_text()).get("resource", {}).items()
        for name in instances
    }
    ids = {i["to"]: i["id"].split("/")[-1] for i in imports}
    # Imports bootstrap an empty backend. State supplies IDs for resources
    # subsequently created from source, which have no import blocks.
    ids.update(
        {
            r["address"]: r["values"]["id"]
            for r in resources((state or {}).get("values", {}).get("root_module", {}))
            if r.get("type") in {APPLICATION, POLICY} and r.get("values", {}).get("id")
        }
    )
    expected_apps = {
        identifier
        for address, identifier in ids.items()
        if address in declared and address.startswith(APPLICATION + ".")
    }
    expected_policies = {
        identifier
        for address, identifier in ids.items()
        if address in declared and address.startswith(POLICY + ".")
    }
    live_apps = {a["id"] for a in apps}
    # The policies collection lists reusable policies; application-specific
    # policies are visible in their application's policy attachments.
    live_policies = {p["id"] for p in policies} | {
        p["id"] for a in apps for p in a.get("policies", [])
    }
    errors = []
    for kind, expected, live in [
        ("application", expected_apps, live_apps),
        ("policy", expected_policies, live_policies),
    ]:
        for identifier in sorted(expected - live):
            errors.append(f"Missing declared Access {kind}: {identifier}")
        for identifier in sorted(live - expected):
            errors.append(f"Unmanaged Access {kind}: {identifier}")
    return errors


def list_access(collection: str) -> list[dict[str, Any]]:
    result = []
    page = 1
    while page <= 100:
        call = urllib.request.Request(
            f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/access/{collection}?per_page=100&page={page}",
            headers={"Authorization": "Bearer " + os.environ["CLOUDFLARE_API_TOKEN"]},
        )
        with urllib.request.urlopen(call, timeout=30) as response:
            body = json.load(response)
        if body.get("success") is not True or not isinstance(body.get("result"), list):
            raise ValueError(f"Cannot read Access {collection} inventory")
        result.extend(body["result"])
        info = body.get("result_info", {})
        if page >= info.get("total_pages", 1):
            if info.get("total_count", len(result)) != len(result):
                raise ValueError(f"Incomplete Access {collection} inventory")
            return result
        page += 1
    raise ValueError("Access inventory pagination did not terminate")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path, nargs="?")
    parser.add_argument("--inventory", action="store_true")
    parser.add_argument("--stack", type=Path, default=Path("tofu/cloudflare-access"))
    parser.add_argument("--mode", choices=["plan", "import", "drift"], default="plan")
    args = parser.parse_args()
    if args.inventory:
        state = json.loads(
            subprocess.check_output(
                ["tofu", f"-chdir={args.stack}", "show", "-json"], text=True
            )
        )
        errors = inventory_errors(
            args.stack, list_access("apps"), list_access("policies"), state
        )
    elif args.plan:
        errors = validate_plan(json.loads(args.plan.read_text()), args.mode)
    else:
        parser.error("provide a plan or --inventory")
    for error in errors:
        print(error)
    if not errors:
        print(f"Access {args.mode} invariants passed")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
