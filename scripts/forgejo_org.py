"""Reconcile the JEFF7712 organization's teams with config/forgejo/organization.json.

Each declared team has access to all of the organization's repositories,
including ones created later, so a new project needs no per-repository grant:
the agent, publisher, CI clone and monitor identities reach it through their
team.

`plan` reads only. `apply` creates or corrects each declared team, sets its
members exactly, then re-plans and fails on remaining drift. Teams absent from
config, including Owners, are never touched. The token must belong to an
organization owner or site administrator and needs `write:organization` and
`read:user`; it is a bootstrap credential, revoked after use
(docs/runbooks/forgejo-organization.md).
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/forgejo/organization.json"
SERVER = "http://git.internal:3000"

Change = tuple[str, str, str, dict[str, Any] | None]


def team_body(name: str, spec: dict[str, Any], units: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "description": spec["description"],
        "permission": spec["permission"],
        "includes_all_repositories": True,
        "can_create_org_repo": spec["can_create_org_repo"],
        "units": units,
        "units_map": {unit: spec["permission"] for unit in units},
    }


def team_drift(live: dict[str, Any], body: dict[str, Any]) -> bool:
    units = live.get("units_map") or {}
    return (
        live.get("permission") != body["permission"]
        or live.get("includes_all_repositories") is not True
        or bool(live.get("can_create_org_repo")) != body["can_create_org_repo"]
        or live.get("description", "") != body["description"]
        or {unit: units.get(unit) for unit in body["units"]} != body["units_map"]
        or any(
            level != "none"
            for unit, level in units.items()
            if unit not in body["units_map"]
        )
    )


def plan_teams(
    config: dict[str, Any],
    live: dict[str, dict[str, Any]],
    members: dict[str, set[str]],
) -> list[Change]:
    """Changes to the declared teams. Teams absent from config are left alone."""
    org = config["organization"]
    changes: list[Change] = []
    for name, spec in sorted(config["teams"].items()):
        body = team_body(name, spec, config["units"])
        current = live.get(name)
        if current is None:
            changes.append(("POST", f"/orgs/{org}/teams", f"create team {name}", body))
        elif team_drift(current, body):
            changes.append(
                ("PATCH", f"/teams/{current['id']}", f"correct team {name}", body)
            )
        team = f"{{team:{name}}}" if current is None else str(current["id"])
        have = members.get(name, set())
        for user in sorted(set(spec["members"]) - have):
            changes.append(
                ("PUT", f"/teams/{team}/members/{user}", f"add {user} to {name}", None)
            )
        for user in sorted(have - set(spec["members"])):
            changes.append(
                (
                    "DELETE",
                    f"/teams/{team}/members/{user}",
                    f"remove {user} from {name}",
                    None,
                )
            )
    return changes


@dataclass
class Forgejo:
    token: str
    server: str = SERVER

    def call(self, method: str, path: str, body: Any = None) -> Any:
        request = urllib.request.Request(
            f"{self.server}/api/v1{path}",
            data=None if body is None else json.dumps(body).encode(),
            method=method,
            headers={
                "Authorization": f"token {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read()[:300].decode(errors="replace")
            raise SystemExit(f"{method} {path}: HTTP {error.code} {detail}") from error
        return json.loads(payload) if payload else None


def plan(forgejo: Forgejo, config: dict[str, Any]) -> list[Change]:
    org = config["organization"]
    teams: dict[str, dict[str, Any]] = {}
    members: dict[str, set[str]] = {}
    for team in forgejo.call("GET", f"/orgs/{org}/teams?limit=50"):
        teams[team["name"]] = team
        if team["name"] in config["teams"]:
            listed = forgejo.call("GET", f"/teams/{team['id']}/members?limit=50")
            members[team["name"]] = {user["login"] for user in listed}
    return plan_teams(config, teams, members)


def apply(forgejo: Forgejo, changes: list[Change], log: Callable[[str], None]) -> None:
    created: dict[str, int] = {}
    for method, path, summary, body in changes:
        for name, team_id in created.items():
            path = path.replace(f"{{team:{name}}}", str(team_id))
        result = forgejo.call(method, path, body)
        if method == "POST" and body is not None:
            created[body["name"]] = result["id"]
        log(summary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["plan", "apply"])
    parser.add_argument("--token-file", type=Path, required=True)
    args = parser.parse_args(argv)
    config = json.loads(CONFIG.read_text())
    forgejo = Forgejo(args.token_file.read_text().strip())
    changes = plan(forgejo, config)
    for _, _, summary, _ in changes:
        print(f"{args.mode}: {summary}")
    if not changes:
        print("teams match config")
        return 0
    if args.mode == "apply":
        apply(forgejo, changes, lambda line: print("done:", line))
        remaining = plan(forgejo, config)
        if remaining:
            raise SystemExit(
                "still drifting: " + "; ".join(change[2] for change in remaining)
            )
        print("teams match config")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
