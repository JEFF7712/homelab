from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from scripts.agent_workspaces.core import (
    WorkspaceError,
    deterministic_uuid,
    load_manifest,
    render_domain,
)
from scripts.agent_workspaces.lifecycle import (
    _cloud_init,
    _domain_projection,
    _manifest_hash,
    deprovision_workspace,
    provision_status,
    provision_workspace,
)
from tests.test_agent_workspaces import enabled_workspace, test_manifest


class FakeRunner:
    def __init__(
        self,
        domain_uuid: str | None = None,
        domain_xml: str = "",
        *,
        active_domain_xml: str | None = None,
        domain_state: str = "running",
    ) -> None:
        self.domain_uuid = domain_uuid
        self.domain_xml = domain_xml
        self.active_domain_xml = active_domain_xml
        self.domain_state = domain_state
        self.commands: list[list[str]] = []

    def __call__(self, command, **kwargs):
        del kwargs
        command = list(command)
        self.commands.append(command)
        if command[:2] == ["virsh", "domuuid"]:
            if self.domain_uuid is None:
                return subprocess.CompletedProcess(
                    command, 1, "", "failed to get domain"
                )
            return subprocess.CompletedProcess(command, 0, self.domain_uuid + "\n", "")
        if command[:2] == ["virsh", "domstate"]:
            return subprocess.CompletedProcess(command, 0, self.domain_state + "\n", "")
        if command[:3] == ["virsh", "dumpxml", "--inactive"]:
            return subprocess.CompletedProcess(command, 0, self.domain_xml, "")
        if command[:2] == ["virsh", "dumpxml"]:
            active_xml = self.active_domain_xml or self.domain_xml
            return subprocess.CompletedProcess(command, 0, active_xml, "")
        if command[:2] == ["virsh", "undefine"]:
            self.domain_uuid = None
        return subprocess.CompletedProcess(command, 0, "", "")


class ProvisionRunner(FakeRunner):
    def __init__(self, expected_uuid: str) -> None:
        super().__init__()
        self.expected_uuid = expected_uuid

    def __call__(self, command, **kwargs):
        command = list(command)
        if command[:2] == ["qemu-img", "create"]:
            pathlib.Path(command[-2]).touch()
        elif command[0] == "cloud-localds":
            pathlib.Path(command[-3]).touch()
        elif command[:2] == ["virsh", "define"]:
            self.domain_xml = pathlib.Path(command[2]).read_text(encoding="utf-8")
            self.domain_uuid = self.expected_uuid
        return super().__call__(command, **kwargs)


class WorkspaceLifecycleTests(unittest.TestCase):
    def workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = pathlib.Path(directory) / "workspaces.json"
            manifest.write_text(
                json.dumps(test_manifest([enabled_workspace()])),
                encoding="utf-8",
            )
            return load_manifest(manifest)[0]

    def prepare_status_assets(
        self, workspace, directory: str
    ) -> tuple[str, pathlib.Path]:
        workspace_dir = pathlib.Path(directory) / "workspace"
        disk_directory = workspace_dir / "disks"
        control_directory = workspace_dir / "control"
        disk_directory.mkdir(parents=True)
        control_directory.mkdir()
        workspace.raw["storage"]["system_disk"] = str(disk_directory / "system.qcow2")
        workspace.raw["storage"]["data_disk"] = str(disk_directory / "data.qcow2")
        for path in (
            workspace.raw["storage"]["system_disk"],
            workspace.raw["storage"]["data_disk"],
        ):
            pathlib.Path(path).touch()
        expected_xml = render_domain(workspace)
        domain_path = control_directory / "domain.xml"
        domain_path.write_text(expected_xml, encoding="utf-8")
        (control_directory / "provisioning.json").write_text(
            json.dumps(
                {
                    "domain_uuid": deterministic_uuid(workspace.id),
                    "manifest_sha256": _manifest_hash(workspace),
                    "domain_xml_sha256": hashlib.sha256(
                        expected_xml.encode()
                    ).hexdigest(),
                }
            )
        )
        return expected_xml, control_directory

    def test_status_reports_partial_disk_without_adopting_it(self) -> None:
        workspace = self.workspace()
        with tempfile.TemporaryDirectory() as directory:
            disks = pathlib.Path(directory) / "workspace" / "disks"
            disks.mkdir(parents=True)
            system_disk = disks / "system.qcow2"
            system_disk.touch()
            workspace.raw["storage"]["system_disk"] = str(system_disk)
            workspace.raw["storage"]["data_disk"] = str(disks / "data.qcow2")
            status = provision_status(workspace, FakeRunner())
        self.assertEqual(status["disks"], {"system_disk": True, "data_disk": False})
        self.assertFalse(status["consistent"])

    def test_domain_projection_accepts_only_expected_qemu_emulator_paths(self) -> None:
        workspace = self.workspace()
        domain = ET.fromstring(render_domain(workspace))
        devices = domain.find("devices")
        assert devices is not None
        emulator = ET.Element("emulator")
        emulator.text = "/run/libvirt/nix-emulators/qemu-system-x86_64"
        devices.insert(0, emulator)
        self.assertEqual(
            _domain_projection(ET.tostring(domain, encoding="unicode")),
            _domain_projection(render_domain(workspace)),
        )
        emulator.text = "/persist/qemu-system-x86_64"
        with self.assertRaisesRegex(WorkspaceError, "unsafe emulator path"):
            _domain_projection(ET.tostring(domain, encoding="unicode"))

    def test_status_rejects_live_domain_configuration_drift(self) -> None:
        workspace = self.workspace()
        with tempfile.TemporaryDirectory() as directory:
            workspace_dir = pathlib.Path(directory) / "workspace"
            disk_directory = workspace_dir / "disks"
            control_directory = workspace_dir / "control"
            disk_directory.mkdir(parents=True)
            control_directory.mkdir()
            workspace.raw["storage"]["system_disk"] = str(
                disk_directory / "system.qcow2"
            )
            workspace.raw["storage"]["data_disk"] = str(disk_directory / "data.qcow2")
            for path in (
                workspace.raw["storage"]["system_disk"],
                workspace.raw["storage"]["data_disk"],
            ):
                pathlib.Path(path).touch()
            expected_xml = render_domain(workspace)
            domain_path = control_directory / "domain.xml"
            domain_path.write_text(expected_xml, encoding="utf-8")
            (control_directory / "provisioning.json").write_text(
                json.dumps(
                    {
                        "domain_uuid": deterministic_uuid(workspace.id),
                        "manifest_sha256": _manifest_hash(workspace),
                        "domain_xml_sha256": hashlib.sha256(
                            expected_xml.encode()
                        ).hexdigest(),
                    }
                )
            )
            drifted = ET.fromstring(expected_xml)
            source = drifted.find("./devices/interface/source")
            assert source is not None
            source.set("bridge", "wrong-bridge")
            status = provision_status(
                workspace,
                FakeRunner(
                    deterministic_uuid(workspace.id),
                    ET.tostring(drifted, encoding="unicode"),
                ),
            )
        self.assertFalse(status["configuration_match"])
        self.assertFalse(status["consistent"])

    def test_status_rejects_extra_interface_and_host_filesystem(self) -> None:
        for device_kind in ("interface", "filesystem"):
            with self.subTest(device_kind=device_kind):
                workspace = self.workspace()
                with tempfile.TemporaryDirectory() as directory:
                    expected_xml, _ = self.prepare_status_assets(workspace, directory)
                    drifted = ET.fromstring(expected_xml)
                    devices = drifted.find("devices")
                    assert devices is not None
                    if device_kind == "interface":
                        interface = ET.SubElement(
                            devices, "interface", {"type": "bridge"}
                        )
                        ET.SubElement(interface, "source", {"bridge": "management"})
                        ET.SubElement(interface, "model", {"type": "virtio"})
                    else:
                        filesystem = ET.SubElement(
                            devices, "filesystem", {"type": "mount"}
                        )
                        ET.SubElement(filesystem, "source", {"dir": "/persist"})
                        ET.SubElement(filesystem, "target", {"dir": "host"})
                    drifted_xml = ET.tostring(drifted, encoding="unicode")
                    status = provision_status(
                        workspace,
                        FakeRunner(deterministic_uuid(workspace.id), drifted_xml),
                    )
                self.assertFalse(status["configuration_match"])
                self.assertFalse(status["consistent"])

    def test_status_rejects_active_only_configuration_drift(self) -> None:
        workspace = self.workspace()
        with tempfile.TemporaryDirectory() as directory:
            expected_xml, _ = self.prepare_status_assets(workspace, directory)
            active = ET.fromstring(expected_xml)
            quota = active.find("./cputune/global_quota")
            assert quota is not None
            quota.text = "200000"
            runner = FakeRunner(
                deterministic_uuid(workspace.id),
                expected_xml,
                active_domain_xml=ET.tostring(active, encoding="unicode"),
            )
            status = provision_status(
                workspace,
                runner,
            )
        self.assertIn(["virsh", "dumpxml", workspace.domain_name], runner.commands)
        self.assertFalse(status["configuration_match"])
        self.assertFalse(status["consistent"])

    def test_successful_provisioning_separates_disks_and_control_files(self) -> None:
        workspace = self.workspace()
        with tempfile.TemporaryDirectory() as directory:
            storage_root = pathlib.Path(directory)
            workspace_dir = storage_root / workspace.id
            disk_directory = workspace_dir / "disks"
            workspace.raw["storage"]["system_disk"] = str(disk_directory / "domain.xml")
            workspace.raw["storage"]["data_disk"] = str(disk_directory / "seed.iso")
            expected_uuid = deterministic_uuid(workspace.id)
            runner = ProvisionRunner(expected_uuid)
            identity = SimpleNamespace(pw_uid=os.getuid())
            group = SimpleNamespace(gr_gid=os.getgid())

            def fake_download(_url, _digest, destination):
                destination.write_bytes(b"verified-image")

            with (
                patch(
                    "scripts.agent_workspaces.lifecycle.pwd.getpwnam",
                    return_value=identity,
                ),
                patch(
                    "scripts.agent_workspaces.lifecycle.grp.getgrnam",
                    return_value=group,
                ),
                patch("scripts.agent_workspaces.lifecycle.validate_provisioning_paths"),
                patch("scripts.agent_workspaces.lifecycle.os.geteuid", return_value=0),
                patch(
                    "scripts.agent_workspaces.lifecycle.socket.gethostname",
                    return_value="homelab-01",
                ),
                patch(
                    "scripts.agent_workspaces.lifecycle._download_verified",
                    side_effect=fake_download,
                ),
            ):
                result = provision_workspace(
                    workspace,
                    authorized=True,
                    runner=runner,
                    storage_root=storage_root,
                )

            control_directory = workspace_dir / "control"
            self.assertEqual(result["status"], "provisioned")
            self.assertTrue(result["consistent"])
            self.assertTrue((disk_directory / "domain.xml").exists())
            self.assertTrue((disk_directory / "seed.iso").exists())
            self.assertTrue((control_directory / "domain.xml").exists())
            self.assertTrue((control_directory / "seed.iso").exists())
            self.assertTrue((control_directory / "provisioning.json").exists())

    def test_provision_requires_explicit_authorization_before_inspection(self) -> None:
        runner = FakeRunner()
        with self.assertRaisesRegex(WorkspaceError, "explicit --authorized"):
            provision_workspace(self.workspace(), authorized=False, runner=runner)
        self.assertEqual(runner.commands, [])

    def test_provision_refuses_existing_disk_without_receipt(self) -> None:
        workspace = self.workspace()
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            workspace_dir = root / workspace.id
            disks = workspace_dir / "disks"
            disks.mkdir(mode=0o700, parents=True)
            system_disk = disks / "system.qcow2"
            system_disk.touch(mode=0o600)
            workspace.raw["storage"]["system_disk"] = str(system_disk)
            workspace.raw["storage"]["data_disk"] = str(disks / "data.qcow2")
            identity = SimpleNamespace(pw_uid=os.getuid())
            group = SimpleNamespace(gr_gid=os.getgid())
            with (
                patch(
                    "scripts.agent_workspaces.lifecycle.pwd.getpwnam",
                    return_value=identity,
                ),
                patch(
                    "scripts.agent_workspaces.lifecycle.grp.getgrnam",
                    return_value=group,
                ),
                patch("scripts.agent_workspaces.lifecycle.validate_provisioning_paths"),
                patch("scripts.agent_workspaces.lifecycle.os.geteuid", return_value=0),
                patch(
                    "scripts.agent_workspaces.lifecycle.socket.gethostname",
                    return_value="homelab-01",
                ),
                self.assertRaisesRegex(WorkspaceError, "refusing to adopt or replace"),
            ):
                provision_workspace(
                    workspace,
                    authorized=True,
                    runner=runner,
                    storage_root=root,
                )
        self.assertEqual(runner.commands[0][:2], ["virsh", "domuuid"])
        self.assertFalse(any(command[0] == "qemu-img" for command in runner.commands))

    def test_cloud_init_has_static_network_no_password_or_forwarded_secret(
        self,
    ) -> None:
        workspace = self.workspace()
        user_data, metadata, network_data = _cloud_init(workspace)
        user = yaml.safe_load(user_data.removeprefix("#cloud-config\n"))
        network = yaml.safe_load(network_data)
        self.assertFalse(user["ssh_pwauth"])
        self.assertTrue(user["disable_root"])
        self.assertNotIn("secret_references", json.dumps(user))
        interface = network["ethernets"]["workspace"]
        self.assertEqual(interface["addresses"], ["192.0.2.2/30"])
        self.assertFalse(interface["dhcp4"])
        self.assertFalse(interface["dhcp6"])
        self.assertIn("agent-rupan-dev", metadata)

    def test_cloud_init_enables_periodic_trim(self) -> None:
        user_data, _, _ = _cloud_init(self.workspace())
        user = yaml.safe_load(user_data.removeprefix("#cloud-config\n"))
        self.assertIn(["systemctl", "enable", "--now", "fstrim.timer"], user["runcmd"])

    def test_deprovision_preserves_disks_and_receipt(self) -> None:
        workspace = self.workspace()
        with tempfile.TemporaryDirectory() as directory:
            workspace_dir = pathlib.Path(directory) / "workspace"
            disk_directory = workspace_dir / "disks"
            control_directory = workspace_dir / "control"
            disk_directory.mkdir(parents=True)
            control_directory.mkdir()
            for name in ("system.qcow2", "data.qcow2"):
                (disk_directory / name).touch()
            workspace.raw["storage"]["system_disk"] = str(
                disk_directory / "system.qcow2"
            )
            workspace.raw["storage"]["data_disk"] = str(disk_directory / "data.qcow2")
            expected_uuid = deterministic_uuid(workspace.id)
            domain_xml = render_domain(workspace)
            domain_path = control_directory / "domain.xml"
            domain_path.write_text(domain_xml)
            (control_directory / "provisioning.json").write_text(
                json.dumps(
                    {
                        "domain_uuid": expected_uuid,
                        "manifest_sha256": _manifest_hash(workspace),
                        "domain_xml_sha256": hashlib.sha256(
                            domain_xml.encode()
                        ).hexdigest(),
                    }
                )
            )
            runner = FakeRunner(expected_uuid, domain_xml)
            result = deprovision_workspace(workspace, authorized=True, runner=runner)
            self.assertEqual(result["status"], "deprovisioned")
            self.assertTrue((disk_directory / "system.qcow2").exists())
            self.assertTrue((disk_directory / "data.qcow2").exists())
            self.assertTrue((control_directory / "provisioning.json").exists())
            self.assertIn(["virsh", "destroy", workspace.domain_name], runner.commands)


if __name__ == "__main__":
    unittest.main()
