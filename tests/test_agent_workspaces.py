from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from scripts.agent_workspaces.core import (
    WorkspaceError,
    deterministic_uuid,
    load_manifest,
    render_domain,
    render_plan,
    validate_provisioning_paths,
)


def enabled_workspace(workspace_id: str = "rupan-dev") -> dict[str, object]:
    return {
        "id": workspace_id,
        "owner": f"{workspace_id}@example.test",
        "trust_class": "owner",
        "host": "homelab-01",
        "enabled": True,
        "image": {
            "url": "https://images.example.test/ubuntu.qcow2",
            "sha256": "a" * 64,
        },
        "resources": {
            "vcpus": 2,
            "memory_mib": 4096,
            "cpu_limit_percent": 100,
            "io_weight": 100,
        },
        "network": {
            "segment": "192.0.2.0/30",
            "address": "192.0.2.2/30",
            "gateway": "192.0.2.1",
            "mac": "02:00:00:00:00:01",
            "bridge": "aw-rupan-br",
            "tap": "aw-rupan-tap",
            "uplink": "eno1",
            "dns": ["9.9.9.9"],
            "ntp": ["162.159.200.1"],
            "ha_api": "198.51.100.10",
            "ingress_sources": ["192.0.2.128/25"],
        },
        "storage": {
            "system_disk": f"/persist/agent-workspaces/{workspace_id}/disks/system.qcow2",
            "system_disk_gib": 32,
            "data_disk": f"/persist/agent-workspaces/{workspace_id}/disks/data.qcow2",
            "data_disk_gib": 100,
        },
        "ssh_authorized_keys": [
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest enrolled@example.test"
        ],
        "browser_identity": f"{workspace_id}@example.test",
        "guest_username": "developer",
        "secret_references": [
            f"/run/credentials/agent-workspaces/{workspace_id}/browser-password"
        ],
    }


def test_manifest(workspaces: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "hosts": {
            "homelab-01": {
                "max_enabled_workspaces": 4,
                "max_memory_mib": 65536,
                "max_cpu_percent": 3200,
                "max_disk_gib": 4096,
            }
        },
        "workspaces": workspaces,
    }


class ManifestTests(unittest.TestCase):
    def load(self, workspaces: list[dict[str, object]]):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "workspaces.json"
            path.write_text(json.dumps(test_manifest(workspaces)))
            return load_manifest(path)

    def test_rejects_aggregate_host_budget_overcommit(self) -> None:
        document = test_manifest([enabled_workspace()])
        hosts = document["hosts"]
        assert isinstance(hosts, dict)
        host = hosts["homelab-01"]
        assert isinstance(host, dict)
        host["max_memory_mib"] = 2048
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "workspaces.json"
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(WorkspaceError, "exceeds memory MiB limit"):
                load_manifest(path)

    def test_empty_inventory_is_valid_until_real_enrollment_exists(self) -> None:
        self.assertEqual(self.load([]), [])

    def test_disabled_workspace_cannot_contain_invented_enrollment(self) -> None:
        item = {
            "id": "invited-user",
            "trust_class": "invited",
            "host": "homelab-01",
            "enabled": False,
            "browser_identity": "unknown@example.test",
        }
        with self.assertRaisesRegex(
            WorkspaceError, "disabled but has enrollment fields"
        ):
            self.load([item])

    def test_rejects_unknown_fields_and_plaintext_secret_fields(self) -> None:
        item = enabled_workspace()
        item["password"] = "secret"
        with self.assertRaisesRegex(WorkspaceError, "unknown fields: password"):
            self.load([item])

    def test_rejects_duplicate_host_controlled_identities(self) -> None:
        first = enabled_workspace("rupan-dev")
        second = enabled_workspace("other-dev")
        second["owner"] = "other@example.test"
        with self.assertRaisesRegex(
            WorkspaceError, "duplicate workspace network address"
        ):
            self.load([first, second])

    def test_rejects_duplicate_browser_identity(self) -> None:
        first = enabled_workspace("rupan-dev")
        first["browser_identity"] = "rupan@example.test"
        second = enabled_workspace("other-dev")
        second["owner"] = "other@example.test"
        second["network"] = {
            "segment": "198.51.100.0/30",
            "address": "198.51.100.2/30",
            "gateway": "198.51.100.1",
            "mac": "02:00:00:00:00:02",
            "bridge": "aw-other-br",
            "tap": "aw-other-tap",
            "uplink": "eno1",
            "dns": ["9.9.9.9"],
            "ntp": ["162.159.200.1"],
            "ha_api": "198.51.100.10",
            "ingress_sources": ["192.0.2.128/25"],
        }
        second["storage"] = {
            "system_disk": "/persist/agent-workspaces/other-dev/disks/system.qcow2",
            "system_disk_gib": 32,
            "data_disk": "/persist/agent-workspaces/other-dev/disks/data.qcow2",
            "data_disk_gib": 100,
        }
        second["browser_identity"] = "RUPAN@example.test"
        with self.assertRaisesRegex(
            WorkspaceError, "duplicate workspace browser identity"
        ):
            self.load([first, second])

    def test_rejects_overlapping_network_segments(self) -> None:
        first = enabled_workspace("rupan-dev")
        first["network"] = {
            "segment": "192.0.2.0/29",
            "address": "192.0.2.6/29",
            "gateway": "192.0.2.1",
            "mac": "02:00:00:00:00:01",
            "bridge": "aw-rupan-br",
            "tap": "aw-rupan-tap",
            "uplink": "eno1",
            "dns": ["9.9.9.9"],
            "ntp": ["162.159.200.1"],
            "ha_api": "198.51.100.10",
            "ingress_sources": ["192.0.2.128/25"],
        }
        second = enabled_workspace("other-dev")
        second["owner"] = "other@example.test"
        second["browser_identity"] = "other@example.test"
        second["network"] = {
            "segment": "192.0.2.0/30",
            "address": "192.0.2.2/30",
            "gateway": "192.0.2.1",
            "mac": "02:00:00:00:00:02",
            "bridge": "aw-other-br",
            "tap": "aw-other-tap",
            "uplink": "eno1",
            "dns": ["9.9.9.9"],
            "ntp": ["162.159.200.1"],
            "ha_api": "198.51.100.10",
            "ingress_sources": ["192.0.2.128/25"],
        }
        second["storage"] = {
            "system_disk": "/persist/agent-workspaces/other-dev/disks/system.qcow2",
            "system_disk_gib": 32,
            "data_disk": "/persist/agent-workspaces/other-dev/disks/data.qcow2",
            "data_disk_gib": 100,
        }
        with self.assertRaisesRegex(WorkspaceError, "network segments overlap"):
            self.load([first, second])

    def test_rejects_cross_role_disk_reuse(self) -> None:
        item = enabled_workspace()
        storage = item["storage"]
        assert isinstance(storage, dict)
        storage["data_disk"] = storage["system_disk"]
        with self.assertRaisesRegex(WorkspaceError, "disk path is reused"):
            self.load([item])

    def test_rejects_non_normalized_and_out_of_root_paths(self) -> None:
        item = enabled_workspace()
        item["secret_references"] = [
            "/run/credentials/agent-workspaces/rupan-dev/../../../../etc/shadow"
        ]
        with self.assertRaisesRegex(WorkspaceError, "normalized path"):
            self.load([item])
        item = enabled_workspace()
        storage = item["storage"]
        assert isinstance(storage, dict)
        storage["system_disk"] = "/etc/shadow"
        with self.assertRaisesRegex(WorkspaceError, "must be beneath"):
            self.load([item])
        for alias in (
            "/persist/agent-workspaces/rupan-dev/disks/./system.qcow2",
            "/persist/agent-workspaces/rupan-dev/disks//system.qcow2",
            "//persist/agent-workspaces/rupan-dev/disks/system.qcow2",
        ):
            item = enabled_workspace()
            storage = item["storage"]
            assert isinstance(storage, dict)
            storage["system_disk"] = alias
            with self.assertRaisesRegex(WorkspaceError, "normalized path"):
                self.load([item])

    def test_rejects_invalid_trust_class_and_non_runtime_secret_reference(self) -> None:
        item = enabled_workspace()
        item["trust_class"] = "admin"
        with self.assertRaisesRegex(WorkspaceError, "trust_class"):
            self.load([item])
        item = enabled_workspace()
        item["secret_references"] = ["/persist/plaintext-token"]
        with self.assertRaisesRegex(WorkspaceError, "must be beneath"):
            self.load([item])

    def test_render_is_deterministic_and_has_no_host_sockets(self) -> None:
        workspace = self.load([enabled_workspace()])[0]
        first = render_domain(workspace)
        second = render_domain(workspace)
        self.assertEqual(first, second)
        root = ET.fromstring(first)
        self.assertEqual(root.findtext("name"), "agent-rupan-dev")
        self.assertEqual(root.findtext("uuid"), deterministic_uuid("rupan-dev"))
        self.assertEqual(root.findtext("./cputune/global_period"), "100000")
        self.assertEqual(root.findtext("./cputune/global_quota"), "100000")
        self.assertEqual(root.findtext("./blkiotune/weight"), "100")
        self.assertEqual(
            root.findtext("./resource/partition"), "/machine/agent-workspaces"
        )
        sources = [
            node.find("source").attrib
            for node in root.findall("./devices/disk")
            if node.attrib["device"] == "disk"
        ]
        self.assertEqual(
            sources,
            [
                {"file": "/persist/agent-workspaces/rupan-dev/disks/system.qcow2"},
                {"file": "/persist/agent-workspaces/rupan-dev/disks/data.qcow2"},
            ],
        )
        self.assertEqual(root.findall(".//filesystem"), [])
        self.assertEqual(root.findall(".//hostdev"), [])
        self.assertEqual(root.findall(".//channel"), [])
        self.assertEqual(root.findall(".//graphics"), [])

    def test_render_passes_pinned_libvirt_domain_schema(self) -> None:
        validator = shutil.which("virt-xml-validate")
        if validator is None:
            self.skipTest("virt-xml-validate is unavailable")
        workspace = self.load([enabled_workspace()])[0]
        with tempfile.TemporaryDirectory() as directory:
            domain = pathlib.Path(directory) / "domain.xml"
            domain.write_text(render_domain(workspace), encoding="utf-8")
            result = subprocess.run(
                [validator, str(domain), "domain"],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_provisioning_path_checks_reject_symlink_and_wrong_owner(self) -> None:
        workspace = self.load([enabled_workspace()])[0]
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            owned = root / "owned"
            owned.mkdir()
            link = root / "link"
            link.symlink_to(owned, target_is_directory=True)
            workspace.raw["storage"] = {
                "system_disk": str(link / "system.qcow2"),
                "data_disk": str(link / "data.qcow2"),
            }
            with self.assertRaisesRegex(WorkspaceError, "traverses symlink"):
                validate_provisioning_paths(
                    workspace,
                    expected_uid=os.getuid(),
                    expected_gid=os.getgid(),
                    trusted_root_uid=os.getuid(),
                    trusted_root_gid=os.getgid(),
                    trusted_root=root,
                )
            workspace.raw["storage"] = {
                "system_disk": str(owned / "missing" / "system.qcow2"),
                "data_disk": str(owned / "missing" / "data.qcow2"),
            }
            with self.assertRaisesRegex(WorkspaceError, "unexpected ownership"):
                validate_provisioning_paths(
                    workspace,
                    expected_uid=os.getuid() + 1,
                    expected_gid=os.getgid(),
                    trusted_root_uid=os.getuid(),
                    trusted_root_gid=os.getgid(),
                    trusted_root=root,
                )

    def test_plan_sorts_workspaces_and_never_renders_disabled_entries(self) -> None:
        first = enabled_workspace("z-user")
        second = enabled_workspace("a-user")
        second["owner"] = "a@example.test"
        second["network"] = {
            "segment": "198.51.100.0/30",
            "address": "198.51.100.2/30",
            "gateway": "198.51.100.1",
            "mac": "02:00:00:00:00:02",
            "bridge": "aw-a-br",
            "tap": "aw-a-tap",
            "uplink": "eno1",
            "dns": ["9.9.9.9"],
            "ntp": ["162.159.200.1"],
            "ha_api": "198.51.100.10",
            "ingress_sources": ["192.0.2.128/25"],
        }
        second["storage"] = {
            "system_disk": "/persist/agent-workspaces/a-user/disks/system.qcow2",
            "system_disk_gib": 32,
            "data_disk": "/persist/agent-workspaces/a-user/disks/data.qcow2",
            "data_disk_gib": 100,
        }
        disabled = {
            "id": "future-user",
            "trust_class": "invited",
            "host": "homelab-01",
            "enabled": False,
        }
        plan = render_plan(self.load([first, disabled, second]))
        self.assertEqual(
            [item["id"] for item in plan["workspaces"]], ["a-user", "z-user"]
        )


class WorkspaceHostDeploymentTest(unittest.TestCase):
    def test_deploy_operations_target_the_host_running_the_workspaces(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        hosts = sorted(
            path.parent.name
            for path in (root / "flake/hosts").glob("*/default.nix")
            if "modules/agent-workspaces.nix" in path.read_text()
        )
        self.assertEqual(len(hosts), 1, hosts)
        catalog = json.loads((root / "config/ci/operations.json").read_text())
        for name in (
            "deploy-agent-workspace-host-dry-run",
            "deploy-agent-workspace-host",
        ):
            targets = {
                command.split("--target ", 1)[1].split()[0]
                for command in catalog[name]["commands"]
                if "--target " in command
            }
            with self.subTest(operation=name):
                self.assertEqual(targets, set(hosts))


if __name__ == "__main__":
    unittest.main()
