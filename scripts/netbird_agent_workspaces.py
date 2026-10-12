from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "config/netbird/agent-workspaces.json"


class ReconcileError(RuntimeError):
    pass


def _read_token(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReconcileError(f"cannot open API token file: {path}") from exc
    with os.fdopen(descriptor, "rb") as token_file:
        metadata = os.fstat(token_file.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise ReconcileError("API token path must be a regular file")
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ReconcileError(
                "API token file must be owned by the current user with mode 0600"
            )
        token = token_file.read().decode("utf-8").strip()
    if not token:
        raise ReconcileError("API token file is empty")
    return token


class NetBirdApi:
    def __init__(
        self, token: str, base_url: str = "https://api.netbird.io/api"
    ) -> None:
        self._headers = {
            "Accept": "application/json",
            "Authorization": f"Token {token}",
            "Content-Type": "application/json",
        }
        self._base_url = base_url.rstrip("/")

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(
            f"{self._base_url}/{path.lstrip('/')}",
            data=data,
            headers=self._headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=20) as response:
                payload = response.read()
        except HTTPError as exc:
            raise ReconcileError(
                f"NetBird API {method} {path} returned HTTP {exc.code}"
            ) from exc
        except URLError as exc:
            raise ReconcileError(
                f"NetBird API request failed for {path}: {exc.reason}"
            ) from exc
        if not payload:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ReconcileError(
                f"NetBird API returned invalid JSON for {path}"
            ) from exc


def _one_named(rows: list[dict[str, Any]], name: str, kind: str) -> dict[str, Any]:
    matches = [row for row in rows if row.get("name") == name]
    if len(matches) != 1:
        raise ReconcileError(
            f"expected exactly one {kind} named {name}, found {len(matches)}"
        )
    return matches[0]


def _normalized_group_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ReconcileError("resource groups have an unexpected shape")
    return sorted(item["id"] if isinstance(item, dict) else item for item in value)


def _policy_body(
    workspace: dict[str, Any], peer_id: str, resource_id: str
) -> dict[str, Any]:
    policy_name = workspace["policy"]
    return {
        "name": policy_name,
        "description": "agent-workspaces",
        "enabled": True,
        "rules": [
            {
                "name": policy_name,
                "description": "agent-workspaces",
                "enabled": True,
                "action": "accept",
                "bidirectional": False,
                "protocol": "tcp",
                "ports": workspace["ports"],
                "sourceResource": {"id": peer_id, "type": "peer"},
                "destinationResource": {"id": resource_id, "type": "host"},
            }
        ],
    }


def _policy_matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    if any(actual.get(key) != expected[key] for key in ("name", "enabled")):
        return False
    actual_rules = actual.get("rules", [])
    if len(actual_rules) != 1:
        return False
    actual_rule = actual_rules[0]
    expected_rule = expected["rules"][0]
    keys = (
        "name",
        "enabled",
        "action",
        "bidirectional",
        "protocol",
        "ports",
        "sourceResource",
        "destinationResource",
    )
    return all(actual_rule.get(key) == expected_rule[key] for key in keys)


def build_plan(
    manifest: dict[str, Any],
    networks: list[dict[str, Any]],
    peers: list[dict[str, Any]],
    routers: list[dict[str, Any]],
    resources: list[dict[str, Any]],
    policies: list[dict[str, Any]],
) -> list[tuple[str, str, dict[str, Any]]]:
    _one_named(networks, manifest["network"], "network")
    router_peer = _one_named(peers, manifest["router_peer"], "routing peer")
    if len(routers) != 1:
        raise ReconcileError(f"expected exactly one router for {manifest['network']}")
    expected_router = manifest["router"]
    actual_router = routers[0]
    if (
        actual_router.get("peer") != router_peer["id"]
        or actual_router.get("enabled") is not expected_router["enabled"]
        or actual_router.get("masquerade") is not expected_router["masquerade"]
        or actual_router.get("metric") != expected_router["metric"]
    ):
        raise ReconcileError(
            "existing NetBird router does not match the declared routing peer settings"
        )

    actions: list[tuple[str, str, dict[str, Any]]] = []
    for workspace in manifest["workspaces"]:
        peer = _one_named(peers, workspace["peer"], "workspace peer")
        expected_resource = {
            "name": f"{workspace['id']}-workspace",
            "description": "agent-workspaces",
            "address": workspace["address"],
            "enabled": True,
            "groups": [],
        }
        matching_resources = [
            row for row in resources if row.get("name") == expected_resource["name"]
        ]
        if len(matching_resources) > 1:
            raise ReconcileError(
                f"duplicate resource named {expected_resource['name']}"
            )
        if not matching_resources:
            actions.append(
                ("create-resource", expected_resource["name"], expected_resource)
            )
            resource_id = f"planned:{expected_resource['name']}"
        else:
            actual_resource = matching_resources[0]
            groups = _normalized_group_ids(actual_resource.get("groups"))
            if (
                actual_resource.get("address") != expected_resource["address"]
                or actual_resource.get("enabled") is not True
                or groups
            ):
                raise ReconcileError(
                    f"resource {expected_resource['name']} differs from the manifest"
                )
            resource_id = actual_resource["id"]

        expected_policy = _policy_body(workspace, peer["id"], resource_id)
        matching_policies = [
            row for row in policies if row.get("name") == workspace["policy"]
        ]
        if len(matching_policies) > 1:
            raise ReconcileError(f"duplicate policy named {workspace['policy']}")
        if not matching_policies:
            actions.append(("create-policy", workspace["policy"], expected_policy))
        elif resource_id.startswith("planned:") or not _policy_matches(
            matching_policies[0], expected_policy
        ):
            raise ReconcileError(
                f"policy {workspace['policy']} differs from the manifest"
            )
    return actions


def _get_list(api: NetBirdApi, path: str) -> list[dict[str, Any]]:
    payload = api.request("GET", path)
    if not isinstance(payload, list) or not all(
        isinstance(row, dict) for row in payload
    ):
        raise ReconcileError(
            f"NetBird API returned an unexpected collection for {path}"
        )
    return payload


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ReconcileError(f"cannot read manifest {path}") from exc
    if value.get("schema_version") != 1:
        raise ReconcileError("unsupported NetBird workspace manifest version")
    return value


def reconcile(manifest: dict[str, Any], api: NetBirdApi, apply: bool) -> list[str]:
    networks = _get_list(api, "networks")
    network = _one_named(networks, manifest["network"], "network")
    peers = _get_list(api, "peers")
    network_id = network["id"]
    routers = _get_list(api, f"networks/{network_id}/routers")
    resources = _get_list(api, f"networks/{network_id}/resources")
    policies = _get_list(api, "policies")
    actions = build_plan(manifest, networks, peers, routers, resources, policies)
    created_resource_ids: dict[str, str] = {}
    for operation, name, body in actions:
        print(f"{operation}: {name}")
        if apply and operation == "create-resource":
            created = api.request("POST", f"networks/{network_id}/resources", body)
            if not isinstance(created, dict) or not isinstance(created.get("id"), str):
                raise ReconcileError(
                    f"NetBird API did not return an ID for resource {name}"
                )
            created_resource_ids[name] = created["id"]
        elif apply and operation == "create-policy":
            policy_body = json.loads(json.dumps(body))
            destination = policy_body["rules"][0]["destinationResource"]
            if destination["id"].startswith("planned:"):
                planned_name = destination["id"].removeprefix("planned:")
                if planned_name not in created_resource_ids:
                    raise ReconcileError(
                        f"resource {planned_name} must be created before policy {name}"
                    )
                destination["id"] = created_resource_ids[planned_name]
            api.request("POST", "policies", policy_body)
    if apply:
        networks = _get_list(api, "networks")
        resources = _get_list(api, f"networks/{network_id}/resources")
        policies = _get_list(api, "policies")
        remaining = build_plan(manifest, networks, peers, routers, resources, policies)
        if remaining:
            raise ReconcileError(
                "NetBird API did not converge to the declared workspace resources"
            )
        print("NetBird agent workspace resources and policies are in sync")
    elif not actions:
        print("NetBird agent workspace resources and policies are in sync")
    return [f"{operation}:{name}" for operation, name, _ in actions]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile the NetBird agent workspace resources and policies"
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--token-file",
        type=Path,
        default=Path(f"/run/user/{os.getuid()}/netbird-api-token"),
    )
    parser.add_argument(
        "--apply", action="store_true", help="create missing resources and policies"
    )
    args = parser.parse_args()
    try:
        token = _read_token(args.token_file)
        reconcile(_load_manifest(args.manifest), NetBirdApi(token), args.apply)
    except ReconcileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
