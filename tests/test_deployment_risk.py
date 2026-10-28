from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.ci.deployment_risk import (
    ALL_PATHS,
    APPROVAL_REQUIRED,
    authority_digest,
    blob_id,
    changed_authority,
    host_operation,
    nix_authority_change,
    plan_risks,
    protected_paths,
    require_plan_approval,
    snapshot,
)
from scripts.ci.policy import HumanApprovalRequired, SourceMirror


class DeploymentRiskTest(unittest.TestCase):
    def test_baseline_detects_additions_deletions_and_mode_changes(self) -> None:
        baseline = {
            "scripts/ci/policy.py": "100644 a",
            "flake/modules/disko-old.nix": "100644 b",
        }
        candidate = {
            "scripts/ci/policy.py": "100755 a",
            "config/projectctl/new.json": "100644 c",
            "services/app.py": "100644 d",
        }
        self.assertEqual(
            changed_authority(baseline, candidate),
            [
                "config/projectctl/new.json",
                "flake/modules/disko-old.nix",
                "scripts/ci/policy.py",
            ],
        )

    def test_snapshot_matches_real_git_tree_identities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            script = root / "scripts/approvals/server.py"
            script.parent.mkdir(parents=True)
            script.write_text("print('policy')\n")
            script.chmod(0o755)
            link = root / "scripts/approvals/link"
            link.symlink_to("server.py")
            bytecode = root / "scripts/approvals/__pycache__/server.pyc"
            bytecode.parent.mkdir()
            bytecode.write_bytes(b"ignored local bytecode")
            subprocess.run(
                ["git", "-C", directory, "add", "scripts/approvals"], check=True
            )
            tree = subprocess.check_output(
                ["git", "-C", directory, "ls-files", "--stage"], text=True
            )
            baseline = snapshot(root)
            self.assertNotIn("scripts/approvals/__pycache__/server.pyc", baseline)
            for line in tree.splitlines():
                metadata, name = line.split("\t")
                if "__pycache__" in name:
                    continue
                mode, digest, _ = metadata.split()
                self.assertEqual(baseline[name], mode + " " + digest)

    def test_new_or_removed_nix_authority_requires_review(self) -> None:
        for source in [
            "security.sudo.wheelNeedsPassword = false;",
            "users.users.root.openssh.authorizedKeys.keys = [];",
            'system.activationScripts.reset = "wipefs /dev/sda";',
        ]:
            self.assertTrue(nix_authority_change("", source))
            self.assertTrue(nix_authority_change(source, ""))
        original = "services.openssh.enable = true;\nusers.users.worker.isSystemUser = true;\nservices.app.enable = false;"
        self.assertFalse(
            nix_authority_change(
                original, original.replace("app.enable = false", "app.enable = true")
            )
        )

    def test_unknown_baseline_fails_closed(self) -> None:
        mirror = SourceMirror(Path("/unused"))
        with (
            patch.object(mirror, "branch_changes", return_value=None),
            self.assertRaises(HumanApprovalRequired),
        ):
            mirror.authority_changes("a" * 40, {}, {}, ALL_PATHS, True)

    def test_server_reads_actual_source_not_a_caller_risk_flag(self) -> None:
        mirror = SourceMirror(Path("/unused"))
        source = "security.sudo.wheelNeedsPassword = false;\n"
        digest = blob_id(source.encode())
        import base64

        with (
            patch.object(mirror, "branch_changes", return_value=()),
            patch.object(
                mirror,
                "git",
                return_value=f"100644 blob {digest}\tflake/modules/new.nix\0".encode(),
            ),
            patch(
                "scripts.ci.policy.api",
                return_value={"content": base64.b64encode(source.encode()).decode()},
            ),
        ):
            self.assertEqual(
                mirror.authority_changes(
                    "a" * 40, {"password": "fixture"}, {}, ALL_PATHS, True
                )[0],
                ["flake/modules/new.nix"],
            )

    def plan(self, kind: str, actions: list[str]) -> dict:
        return {
            "resource_changes": [
                {
                    "type": kind,
                    "address": f"{kind}.example",
                    "change": {"actions": actions},
                }
            ]
        }

    def test_routine_network_changes_and_small_cleanup_are_autonomous(self) -> None:
        self.assertEqual(
            plan_risks(self.plan("opnsense_firewall_rule", ["delete"])), []
        )
        self.assertEqual(plan_risks(self.plan("cloudflare_dns_record", ["update"])), [])
        self.assertEqual(plan_risks({"resource_changes": []}), [])

    def test_authority_data_and_connectivity_destruction_require_approval(self) -> None:
        for kind, actions in [
            ("cloudflare_zero_trust_access_policy", ["update"]),
            ("cloudflare_r2_bucket", ["delete", "create"]),
            ("opnsense_interface", ["delete"]),
            ("opnsense_user", ["create"]),
        ]:
            with self.subTest(kind=kind):
                self.assertTrue(plan_risks(self.plan(kind, actions)))
        self.assertTrue(plan_risks({"resource_changes": [], "complete": False}))
        self.assertTrue(plan_risks({}))

    def test_saved_plan_is_checked_before_agent_apply_but_operator_can_execute(
        self,
    ) -> None:
        output = json.dumps(self.plan("cloudflare_r2_bucket", ["delete"]))
        with (
            patch("subprocess.check_output", return_value=output) as show,
            patch("subprocess.run") as initialize,
        ):
            with self.assertRaisesRegex(ValueError, APPROVAL_REQUIRED):
                require_plan_approval(Path("/repo"), "cloudflare-apply", False)
            self.assertEqual(show.call_args.args[0][-1], "desired.tfplan")
            initialize.assert_called_once()
            show.reset_mock()
            require_plan_approval(Path("/repo"), "cloudflare-apply", True)
            show.assert_not_called()

    def test_invalid_saved_plan_cannot_apply(self) -> None:
        for plan in (
            {},
            {"resource_changes": [], "complete": False},
            {"resource_changes": [], "errored": True},
        ):
            with (
                patch("subprocess.check_output", return_value=json.dumps(plan)),
                patch("subprocess.run"),
                self.assertRaisesRegex(ValueError, "incomplete"),
            ):
                require_plan_approval(Path("/repo"), "cloudflare-apply", False)

    def test_scope_follows_what_the_operation_activates(self) -> None:
        catalog = json.loads(
            (
                Path(__file__).resolve().parents[1] / "config/ci/operations.json"
            ).read_text()
        )
        self.assertTrue(host_operation(catalog["deploy-ci-host"]))
        self.assertTrue(host_operation(catalog["deploy-fleet"]))
        self.assertFalse(host_operation(catalog["matrix-publish"]))
        self.assertFalse(host_operation(catalog["opnsense-apply"]))
        baseline = {
            "scripts/ci/policy.py": "100644 a",
            "scripts/ci/operation.py": "100644 b",
            "opnsense_reconciler/agent_acl.py": "100644 c",
        }
        policy = dict(baseline, **{"scripts/ci/policy.py": "100644 z"})
        gate = dict(baseline, **{"scripts/ci/operation.py": "100644 z"})
        acl = dict(baseline, **{"opnsense_reconciler/agent_acl.py": "100644 z"})
        publish = protected_paths("matrix-publish", False)
        self.assertEqual(changed_authority(baseline, policy, publish), [])
        self.assertEqual(
            changed_authority(baseline, gate, publish), ["scripts/ci/operation.py"]
        )
        self.assertEqual(changed_authority(baseline, acl, publish), [])
        self.assertEqual(
            changed_authority(baseline, acl, protected_paths("opnsense-apply", False)),
            ["opnsense_reconciler/agent_acl.py"],
        )
        self.assertEqual(
            changed_authority(
                baseline, policy, protected_paths("deploy-ci-host", True)
            ),
            ["scripts/ci/policy.py"],
        )

    def test_ordinary_service_code_is_not_authority(self) -> None:
        for path in [
            "scripts/ci/service_release.py",
            "scripts/checks/python.sh",
            "config/ci/projects.json",
            "hooks/pre-tool-use",
            "flake/modules/kiosk.nix",
        ]:
            self.assertEqual(
                changed_authority({path: "100644 a"}, {path: "100644 b"}), [], path
            )
        self.assertFalse(
            nix_authority_change(
                "", 'users.users.svc = { isSystemUser = true; group = "svc"; };'
            )
        )
        self.assertTrue(
            nix_authority_change("", 'users.users.svc.extraGroups = [ "wheel" ];')
        )

    def test_digest_names_exact_versions_and_ignores_unrelated_files(self) -> None:
        candidate = {"scripts/ci/policy.py": "100644 a", "services/app.py": "100644 x"}
        digest = authority_digest(["scripts/ci/policy.py"], candidate)
        unrelated = dict(candidate, **{"services/app.py": "100644 y"})
        self.assertEqual(authority_digest(["scripts/ci/policy.py"], unrelated), digest)
        edited = dict(candidate, **{"scripts/ci/policy.py": "100644 b"})
        self.assertNotEqual(authority_digest(["scripts/ci/policy.py"], edited), digest)
        self.assertNotEqual(
            authority_digest(["scripts/ci/policy.py", ".sops.yaml"], candidate), digest
        )
        self.assertRegex(digest, r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
