from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.switches.model import (
    addresses,
    canonical,
    digest,
    encryption_configuration,
    review_plan,
    validate_inventory,
)
from scripts.switches.runner import BLUEPRINT_FILES, Engine, blueprint_digest

ROOT = Path(__file__).resolve().parents[1]
DESIRED_INVENTORY = json.loads((ROOT / "tofu/switches/inventory.json").read_text())
# Network adoption is tested before optional description management.
INVENTORY = copy.deepcopy(DESIRED_INVENTORY)
for switch in INVENTORY["switches"].values():
    switch["description"] = None


def plan_document(
    name: str = "upstairs", imports: bool = False, inventory: dict = INVENTORY
) -> dict:
    items = []
    for address in sorted(addresses(inventory, name)):
        change = {"actions": ["no-op"], "after": {"allow_disruptive": False}}
        if imports:
            change["importing"] = {"id": "1"}
        items.append(
            {
                "address": address,
                "type": address.split(".")[0],
                "mode": "managed",
                "change": change,
            }
        )
    return {
        "format_version": "1.2",
        "resource_changes": items,
        "planned_values": {"root_module": {"resources": items}},
    }


class InventoryTest(unittest.TestCase):
    def test_snapshot_covers_all_physical_resources(self) -> None:
        validate_inventory(DESIRED_INVENTORY)
        validate_inventory(INVENTORY)
        self.assertEqual(len(addresses(INVENTORY, "upstairs")), 25)
        self.assertEqual(len(addresses(INVENTORY, "downstairs")), 16)

    def test_rejects_target_membership_and_pvid_errors(self) -> None:
        for field, value in [
            ("host", "10.0.10.4"),
            ("hardware", "TL-SG108E 5.0"),
            ("firmware", "unverified"),
        ]:
            candidate = copy.deepcopy(INVENTORY)
            candidate["switches"]["upstairs"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_inventory(candidate)
        mutations = [
            lambda s: s["ports"].pop("8"),
            lambda s: s["ports"]["1"].update(pvid=4094),
            lambda s: s["vlans"]["1"].update(tagged_ports=[{}]),
            lambda s: s["vlans"]["1"].update(tagged_ports=[1, 1]),
            lambda s: s["vlans"]["1"].update(tagged_ports=[3]),
        ]
        for mutate in mutations:
            candidate = copy.deepcopy(INVENTORY)
            mutate(candidate["switches"]["upstairs"])
            with self.assertRaises(ValueError):
                validate_inventory(candidate)

    def test_cosmetic_description_is_adopted_after_the_network_baseline(self) -> None:
        candidate = copy.deepcopy(INVENTORY)
        for invalid in ("Upstairs_TL-SG108E", "", "bad name", "a" * 33):
            candidate["switches"]["upstairs"]["description"] = invalid
            with self.assertRaises(ValueError):
                validate_inventory(candidate)
        candidate["switches"]["upstairs"]["description"] = "Upstairs-TL-SG108E"
        validate_inventory(candidate)
        self.assertEqual(len(addresses(candidate, "upstairs")), 26)
        document = plan_document(inventory=candidate)
        item = next(
            x
            for x in document["resource_changes"]
            if x["type"] == "tplinkeasysmart_system"
        )
        item["change"].update(actions=["update"], importing={"id": "system"})
        review = review_plan(document, candidate, "upstairs")
        self.assertEqual(review["imports"], 1)
        self.assertEqual(len(review["changes"]), 1)
        with self.assertRaisesRegex(ValueError, "Adoption"):
            review_plan(document, candidate, "upstairs", adoption=True)

    def test_adoption_requires_only_noop_imports(self) -> None:
        document = plan_document(imports=True)
        reviewed = review_plan(document, INVENTORY, "upstairs", adoption=True)
        self.assertEqual(reviewed["imports"], 25)
        self.assertFalse(reviewed["zero_change"])
        document["resource_changes"][0]["change"]["actions"] = ["update"]
        with self.assertRaisesRegex(ValueError, "Adoption"):
            review_plan(document, INVENTORY, "upstairs", adoption=True)

    def test_rejects_deletion_replacement_unknown_and_mode_changes(self) -> None:
        for actions in [["delete"], ["delete", "create"], ["create", "delete"]]:
            document = plan_document()
            document["resource_changes"][0]["change"]["actions"] = actions
            with self.assertRaisesRegex(ValueError, "Deleting"):
                review_plan(document, INVENTORY, "upstairs")
        document = plan_document()
        document["planned_values"]["root_module"]["resources"] = []
        with self.assertRaisesRegex(ValueError, "ownership"):
            review_plan(document, INVENTORY, "upstairs")
        document = plan_document()
        item = next(
            x
            for x in document["resource_changes"]
            if x["type"] == "tplinkeasysmart_vlan_mode"
        )
        item["change"]["actions"] = ["update"]
        with self.assertRaisesRegex(ValueError, "VLAN mode"):
            review_plan(document, INVENTORY, "upstairs")

    def test_zero_change_after_import(self) -> None:
        self.assertTrue(
            review_plan(plan_document(), INVENTORY, "upstairs")["zero_change"]
        )


class SavedPlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.blueprint = self.root / "blueprint"
        self.blueprint.mkdir()
        for name in BLUEPRINT_FILES:
            shutil.copy2(ROOT / "tofu/switches" / name, self.blueprint / name)
        self.engine = Engine(
            self.blueprint,
            self.root / "plans",
            "tofu",
            {"upstairs": "x" * 48},
            local_state_dir=self.root / "states",
        )
        self.payload = {
            "switch": "upstairs",
            "source_revision": "a" * 40,
            "inventory": INVENTORY,
            "blueprint_sha256": blueprint_digest(self.blueprint),
        }
        self.directory = self.engine.work_root / ("b" * 32)
        self.directory.mkdir()
        (self.directory / "inventory.json").write_bytes(canonical(INVENTORY))
        (self.directory / "plan.tfplan").write_bytes(b"encrypted-plan")
        self.receipt = {
            "schema_version": 1,
            "plan_id": self.directory.name,
            "switch": "upstairs",
            "source_revision": "a" * 40,
            "blueprint_sha256": blueprint_digest(self.blueprint),
            "inventory_sha256": digest(canonical(INVENTORY)),
            "state_sha256": digest(b""),
            "plan_sha256": digest(b"encrypted-plan"),
            "created_at": int(time.time()),
            "adoption": True,
            "review": review_plan(
                plan_document(imports=True), INVENTORY, "upstairs", adoption=True
            ),
        }
        self.save_receipt()

    def save_receipt(self) -> None:
        (self.directory / "receipt.json").write_bytes(canonical(self.receipt))
        self.payload["receipt"] = copy.deepcopy(self.receipt)

    def test_rejects_stale_state_before_running_tofu(self) -> None:
        (self.engine.local_state_dir / "upstairs.tfstate").write_bytes(b"changed-state")
        with (
            patch.object(self.engine, "run") as run,
            self.assertRaisesRegex(ValueError, "State changed"),
        ):
            self.engine.apply(self.payload)
        run.assert_not_called()

    def test_rejects_changed_receipt_plan_source_and_expired_plan(self) -> None:
        for mutation in ["receipt", "plan", "source", "expired"]:
            with self.subTest(mutation=mutation):
                self.payload["source_revision"] = "a" * 40
                (self.directory / "plan.tfplan").write_bytes(b"encrypted-plan")
                self.receipt["created_at"] = int(time.time())
                self.save_receipt()
                if mutation == "receipt":
                    self.payload["receipt"]["review"]["changes"] = ["forged"]
                elif mutation == "plan":
                    (self.directory / "plan.tfplan").write_bytes(b"replaced")
                elif mutation == "source":
                    self.payload["source_revision"] = "c" * 40
                else:
                    self.receipt["created_at"] -= 86401
                    self.save_receipt()
                with (
                    patch.object(self.engine, "run") as run,
                    self.assertRaises(ValueError),
                ):
                    self.engine.apply(self.payload)
                run.assert_not_called()

    def test_readonly_environment_omits_real_credentials_and_ambient_tofu_flags(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {
                "TF_CLI_ARGS_apply": "-auto-approve",
                "TPLINK_EASYSMART_PASSWORD": "ambient",
            },
        ):
            env = self.engine.environment("upstairs", self.directory, {})
        self.assertNotIn("TF_CLI_ARGS_apply", env)
        self.assertEqual(env["TPLINK_EASYSMART_PASSWORD"], "readonly")
        self.assertNotIn("TF_VAR_password", env)


class CiVerificationTest(unittest.TestCase):
    def test_apply_requires_a_fresh_zero_change_plan_for_each_switch(self) -> None:
        from scripts.switches import ci

        for drift in (False, True):
            with self.subTest(drift=drift), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "tofu/switches").mkdir(parents=True)
                (root / "tofu/switches/inventory.json").write_bytes(
                    canonical(INVENTORY)
                )
                artifacts = root / "artifacts/switches"
                artifacts.mkdir(parents=True)
                for name in ("upstairs", "downstairs"):
                    (artifacts / (name + "-plan.json")).write_text(
                        '{"plan_id":"original"}'
                    )
                ca = root / "ca.pem"
                ca.write_text("public-ca")

                def request(
                    action: str,
                    name: str,
                    revision: str,
                    inventory: dict,
                    backend: dict,
                    receipt: dict | None = None,
                ) -> dict:
                    if action == "apply":
                        self.assertEqual(receipt, {"plan_id": "original"})
                    return {"review": {"zero_change": not drift}}

                environment = {
                    "CI_COMMIT_SHA": "a" * 40,
                    "TF_HTTP_USERNAME": "deploy",
                    "TF_HTTP_PASSWORD": "backend-test",
                    "STATE_CA_FILE": str(ca),
                }
                with (
                    patch.object(ci, "ROOT", root),
                    patch.object(ci, "request", side_effect=request) as remote,
                    patch.dict(os.environ, environment),
                    patch("sys.argv", ["switch-ci", "apply"]),
                ):
                    if drift:
                        with self.assertRaisesRegex(ValueError, "Post-apply"):
                            ci.main()
                    else:
                        ci.main()
                expected = [("apply", "downstairs"), ("plan", "downstairs")]
                if not drift:
                    expected += [("apply", "upstairs"), ("plan", "upstairs")]
                self.assertEqual(
                    [(call.args[0], call.args[1]) for call in remote.call_args_list],
                    expected,
                )


@unittest.skipUnless(shutil.which("tofu"), "pinned OpenTofu is required")
class NativeEncryptionTest(unittest.TestCase):
    def test_state_and_plan_are_encrypted_and_fail_without_correct_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "main.tf").write_text("""terraform {
 encryption {
  state { enforced = true }
  plan { enforced = true }
 }
}
resource "terraform_data" "proof" { input = "private-switch-marker" }
""")
            env = {k: v for k, v in os.environ.items() if not k.startswith("TF_")}

            def run(*args: str, key: str | None = None) -> subprocess.CompletedProcess:
                current = dict(env)
                if key:
                    current["TF_ENCRYPTION"] = encryption_configuration(key)
                return subprocess.run(
                    ["tofu", *args],
                    cwd=root,
                    env=current,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )

            self.assertEqual(run("init", "-backend=false").returncode, 0)
            self.assertNotEqual(run("plan", "-out=proof.tfplan").returncode, 0)
            key = "test-key-" + "x" * 48
            result = run("plan", "-out=proof.tfplan", key=key)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(
                b"private-switch-marker", (root / "proof.tfplan").read_bytes()
            )
            self.assertNotEqual(
                run("show", "-json", "proof.tfplan", key="wrong" * 12).returncode, 0
            )
            result = run("apply", "proof.tfplan", key=key)
            self.assertEqual(result.returncode, 0, result.stderr)
            envelope = json.loads((root / "terraform.tfstate").read_text())
            self.assertIn("encrypted_data", envelope)
            self.assertIn("serial", envelope)
            self.assertIn("lineage", envelope)
            self.assertNotIn("resources", envelope)
            self.assertNotIn("private-switch-marker", json.dumps(envelope))
            self.assertNotEqual(run("show", "-json").returncode, 0)
