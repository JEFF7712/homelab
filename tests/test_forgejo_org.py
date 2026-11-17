import json
import unittest
from pathlib import Path
from typing import Any

from scripts import forgejo_org

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config/forgejo/organization.json").read_text())


def live_team(team_id: int, name: str) -> dict[str, Any]:
    body = forgejo_org.team_body(name, CONFIG["teams"][name], CONFIG["units"])
    return {
        "id": team_id,
        "name": name,
        "description": body["description"],
        "permission": body["permission"],
        "includes_all_repositories": True,
        "can_create_org_repo": body["can_create_org_repo"],
        "units_map": dict(body["units_map"]),
    }


class ConfigTest(unittest.TestCase):
    def test_every_identity_reaches_every_repository(self) -> None:
        members = {
            user: name
            for name, spec in CONFIG["teams"].items()
            for user in spec["members"]
        }
        self.assertEqual(
            members,
            {
                "homelab-agent": "agents",
                "homelab-publisher": "publishers",
                "homelab-ci-source": "ci-readers",
                "repo-monitor": "monitors",
            },
        )
        for name in CONFIG["teams"]:
            body = forgejo_org.team_body(name, CONFIG["teams"][name], CONFIG["units"])
            self.assertTrue(body["includes_all_repositories"])

    def test_only_the_agent_team_creates_repositories(self) -> None:
        creators = [
            name
            for name, spec in CONFIG["teams"].items()
            if spec["can_create_org_repo"]
        ]
        self.assertEqual(creators, ["agents"])
        self.assertEqual(CONFIG["teams"]["ci-readers"]["permission"], "read")
        self.assertEqual(CONFIG["teams"]["monitors"]["permission"], "read")


class PlanTest(unittest.TestCase):
    def converged(self) -> tuple[dict[str, Any], dict[str, set[str]]]:
        teams = {
            name: live_team(index, name)
            for index, name in enumerate(sorted(CONFIG["teams"]), start=10)
        }
        members = {name: set(spec["members"]) for name, spec in CONFIG["teams"].items()}
        return teams, members

    def test_a_converged_organization_plans_nothing(self) -> None:
        teams, members = self.converged()
        self.assertEqual(forgejo_org.plan_teams(CONFIG, teams, members), [])

    def test_new_teams_are_created_with_their_members(self) -> None:
        changes = forgejo_org.plan_teams(CONFIG, {}, {})
        self.assertIn(
            (
                "PUT",
                "/teams/{team:agents}/members/homelab-agent",
                "add homelab-agent to agents",
                None,
            ),
            changes,
        )
        created = [body for method, _, _, body in changes if method == "POST"]
        self.assertEqual(len(created), len(CONFIG["teams"]))

    def test_a_team_that_lost_all_repository_access_is_corrected(self) -> None:
        teams, members = self.converged()
        teams["publishers"]["includes_all_repositories"] = False
        changes = forgejo_org.plan_teams(CONFIG, teams, members)
        self.assertEqual(
            [(method, summary) for method, _, summary, _ in changes],
            [("PATCH", "correct team publishers")],
        )

    def test_an_extra_unit_is_drift(self) -> None:
        teams, members = self.converged()
        teams["monitors"]["units_map"]["repo.wiki"] = "read"
        changes = forgejo_org.plan_teams(CONFIG, teams, members)
        self.assertEqual(
            [summary for *_, summary, _ in changes], ["correct team monitors"]
        )

    def test_members_are_set_exactly(self) -> None:
        teams, members = self.converged()
        members["publishers"] = {"intruder"}
        changes = forgejo_org.plan_teams(CONFIG, teams, members)
        team = teams["publishers"]["id"]
        self.assertEqual(
            [(method, path) for method, path, _, _ in changes],
            [
                ("PUT", f"/teams/{team}/members/homelab-publisher"),
                ("DELETE", f"/teams/{team}/members/intruder"),
            ],
        )

    def test_owners_and_other_undeclared_teams_are_left_alone(self) -> None:
        teams, members = self.converged()
        teams["Owners"] = {"id": 1, "name": "Owners", "permission": "owner"}
        members["Owners"] = {"rupan"}
        self.assertEqual(forgejo_org.plan_teams(CONFIG, teams, members), [])


class ApplyTest(unittest.TestCase):
    def test_members_of_a_new_team_use_its_created_id(self) -> None:
        calls: list[tuple[str, str]] = []

        class Fake:
            def call(self, method: str, path: str, body: Any = None) -> Any:
                calls.append((method, path))
                return {"id": 42} if method == "POST" else None

        changes = forgejo_org.plan_teams(
            {**CONFIG, "teams": {"agents": CONFIG["teams"]["agents"]}}, {}, {}
        )
        forgejo_org.apply(Fake(), changes, lambda line: None)
        self.assertEqual(
            calls,
            [
                ("POST", "/orgs/JEFF7712/teams"),
                ("PUT", "/teams/42/members/homelab-agent"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
