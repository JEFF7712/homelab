from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.cloudflare.access import (
    ACCOUNT,
    APPLICATION,
    OWNER,
    OWNER_DESTINATIONS,
    POLICY,
    TRACKER_PATHS,
    inventory_errors,
    list_access,
    validate_plan,
)

ROOT = Path(__file__).resolve().parents[1]


def fixture() -> dict:
    policies = [
        {
            "id": "owner",
            "decision": "allow",
            "include": [{"email": {"email": OWNER}, "everyone": None}],
        },
        {"id": "public", "decision": "bypass", "include": [{"everyone": {}}]},
    ]
    resources = [
        {
            "address": f"policy.{p['id']}",
            "type": POLICY,
            "values": p | {"account_id": ACCOUNT},
        }
        for p in policies
    ]
    for i, uri in enumerate(sorted(OWNER_DESTINATIONS | TRACKER_PATHS)):
        resources.append(
            {
                "address": f"application.app{i}",
                "type": APPLICATION,
                "values": {
                    "account_id": ACCOUNT,
                    "destinations": [{"type": "public", "uri": uri}],
                    "policies": [{"id": "public" if uri in TRACKER_PATHS else "owner"}],
                },
            }
        )
    return {
        "planned_values": {"root_module": {"resources": resources}},
        "resource_changes": [
            {"address": r["address"], "change": {"actions": ["no-op"]}}
            for r in resources
        ],
    }


class AccessPlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = fixture()
        self.resources = self.plan["planned_values"]["root_module"]["resources"]

    def test_import_plan_preserves_policies_with_provider_null_selectors(self) -> None:
        self.plan["resource_changes"][0]["change"]["importing"] = {"id": "existing"}
        self.assertEqual(validate_plan(self.plan, "import"), [])
        self.assertTrue(validate_plan(self.plan, "drift"))

    def test_import_and_drift_reject_any_mutation(self) -> None:
        for actions in [["update"], ["create"], ["delete"], ["delete", "create"]]:
            self.plan["resource_changes"][0]["change"]["actions"] = actions
            with self.subTest(actions=actions):
                self.assertTrue(validate_plan(self.plan, "import"))
                self.assertTrue(validate_plan(self.plan, "drift"))

    def test_normal_plan_still_rejects_deletion_and_replacement(self) -> None:
        self.plan["resource_changes"][0]["change"]["actions"] = ["delete", "create"]
        self.assertTrue(validate_plan(self.plan))

    def test_automatic_bootstrap_cannot_mix_imports_with_live_updates(self) -> None:
        self.plan["resource_changes"][0]["change"]["importing"] = {"id": "existing"}
        self.plan["resource_changes"][1]["change"]["actions"] = ["update"]
        self.assertTrue(validate_plan(self.plan))

    def test_empty_unknown_and_wrong_account_fail_closed(self) -> None:
        for mutation in ["missing", "unknown", "account"]:
            p = fixture()
            app = p["planned_values"]["root_module"]["resources"][2]["values"]
            if mutation == "missing":
                app["policies"] = []
            elif mutation == "unknown":
                app["policies"] = [{"id": None}]
            else:
                app["account_id"] = "other"
            with self.subTest(mutation=mutation):
                self.assertTrue(validate_plan(p))

    def test_owner_policy_cannot_expand_to_everyone_or_an_email_domain(self) -> None:
        for selector in [{"everyone": {}}, {"email_domain": {"domain": "gmail.com"}}]:
            self.resources[0]["values"]["include"] = [selector]
            self.assertTrue(validate_plan(self.plan))

    def test_owner_host_or_child_path_may_not_gain_bypass(self) -> None:
        for uri in [
            "*.rupan.dev",
            "tally.rupan.dev",
            "approvals.rupan.dev/api",
            "pod.rupan.dev/admin",
        ]:
            p = fixture()
            p["planned_values"]["root_module"]["resources"].append(
                {
                    "address": "application.bad",
                    "type": APPLICATION,
                    "values": {
                        "account_id": ACCOUNT,
                        "destinations": [{"uri": uri}],
                        "policies": [{"id": "public"}],
                    },
                }
            )
            with self.subTest(uri=uri):
                self.assertTrue(validate_plan(p))

    def test_tracker_must_remain_public_and_path_scoped(self) -> None:
        for r in self.resources:
            value = r["values"]
            if any(d["uri"] in TRACKER_PATHS for d in value.get("destinations", [])):
                value["policies"] = [{"id": "owner"}]
        self.assertTrue(validate_plan(self.plan))

    def test_duplicate_destinations_rejected(self) -> None:
        self.resources.append(copy.deepcopy(self.resources[2]))
        self.assertTrue(validate_plan(self.plan))

    def test_incomplete_or_missing_plan_rejected(self) -> None:
        for p in [
            {},
            {"resource_changes": [], "complete": False},
            {"resource_changes": []},
        ]:
            self.assertTrue(validate_plan(p))

    def test_repository_declarations_preserve_the_access_contract(self) -> None:
        stack = ROOT / "tofu/cloudflare-access"
        imports = json.loads((stack / "imports.tf.json").read_text())["import"]
        ids = {i["to"]: i["id"].split("/")[-1] for i in imports}
        evaluated = []
        for filename in ["policies.tf.json", "applications.tf.json"]:
            for kind, instances in json.loads((stack / filename).read_text())[
                "resource"
            ].items():
                for name, config in instances.items():
                    address = f"{kind}.{name}"
                    values = copy.deepcopy(config)
                    values.pop("lifecycle")
                    values["id"] = ids[address]
                    for policy in values.get("policies", []):
                        reference = policy["id"]
                        policy["id"] = ids[
                            reference.removeprefix("${").removesuffix(".id}")
                        ]
                    evaluated.append(
                        {"address": address, "type": kind, "values": values}
                    )
        p = {
            "planned_values": {"root_module": {"resources": evaluated}},
            "resource_changes": [
                {"address": r["address"], "change": {"actions": ["no-op"]}}
                for r in evaluated
            ],
        }
        self.assertEqual(validate_plan(p), [])


class AccessInventoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ROOT / "tofu/cloudflare-access"
        imports = json.loads((self.stack / "imports.tf.json").read_text())["import"]
        self.apps = [
            {"id": i["id"].split("/")[-1]}
            for i in imports
            if i["to"].startswith(APPLICATION + ".")
        ]
        self.policies = [
            {"id": i["id"].split("/")[-1]}
            for i in imports
            if i["to"].startswith(POLICY + ".")
        ]

    def test_unknown_application_can_shadow_access_and_is_drift(self) -> None:
        self.assertEqual(inventory_errors(self.stack, self.apps, self.policies), [])
        self.apps.append({"id": "unmanaged-path-override"})
        self.assertTrue(
            any(
                "Unmanaged Access application" in e
                for e in inventory_errors(self.stack, self.apps, self.policies)
            )
        )

    def test_missing_application_and_extra_reusable_policy_are_drift(self) -> None:
        self.apps.pop()
        self.policies.append({"id": "unmanaged-policy"})
        errors = inventory_errors(self.stack, self.apps, self.policies)
        self.assertTrue(any("Missing declared Access application" in e for e in errors))
        self.assertTrue(any("Unmanaged Access policy" in e for e in errors))

    def test_new_declared_resource_uses_state_id_without_an_import_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stack = Path(directory)
            (stack / "imports.tf.json").write_text('{"import": []}')
            (stack / "new.tf.json").write_text(
                json.dumps({"resource": {APPLICATION: {"new": {}}}})
            )
            state = {
                "values": {
                    "root_module": {
                        "resources": [
                            {
                                "address": APPLICATION + ".new",
                                "type": APPLICATION,
                                "values": {"id": "new-id"},
                            }
                        ]
                    }
                }
            }
            self.assertEqual(inventory_errors(stack, [{"id": "new-id"}], [], state), [])

    def test_application_specific_policies_are_counted_from_attachments(self) -> None:
        p = self.policies.pop()
        self.apps[0]["policies"] = [p]
        self.assertEqual(inventory_errors(self.stack, self.apps, self.policies), [])

    def test_reader_paginates_and_rejects_partial_inventory(self) -> None:
        pages = [
            {
                "success": True,
                "result": [{"id": "a"}],
                "result_info": {"total_pages": 2, "total_count": 2},
            },
            {
                "success": True,
                "result": [{"id": "b"}],
                "result_info": {"total_pages": 2, "total_count": 2},
            },
        ]
        with (
            patch.dict("os.environ", {"CLOUDFLARE_API_TOKEN": "test"}),
            patch(
                "urllib.request.urlopen",
                side_effect=[io.BytesIO(json.dumps(p).encode()) for p in pages],
            ),
        ):
            self.assertEqual(list_access("apps"), [{"id": "a"}, {"id": "b"}])
        with (
            patch.dict("os.environ", {"CLOUDFLARE_API_TOKEN": "test"}),
            patch(
                "urllib.request.urlopen",
                return_value=io.BytesIO(json.dumps(pages[1]).encode()),
            ),
            self.assertRaises(ValueError),
        ):
            list_access("apps")


if __name__ == "__main__":
    unittest.main()
