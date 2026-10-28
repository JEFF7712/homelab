{
  nixpkgs,
  system,
}:
let
  pkgs = nixpkgs.legacyPackages.${system};
  python = pkgs.python313.withPackages (packages: [ packages.pyyaml ]);
  manifest = pkgs.writeText "agent-workspace-libvirt.json" (
    builtins.toJSON {
      schema_version = 1;
      hosts.homelab-01 = {
        max_enabled_workspaces = 1;
        max_memory_mib = 4096;
        max_cpu_percent = 100;
        max_disk_gib = 64;
      };
      workspaces = [
        {
          id = "libvirt-test";
          owner = "test";
          trust_class = "owner";
          host = "homelab-01";
          enabled = true;
          image = {
            url = "https://example.invalid/image.qcow2";
            sha256 = builtins.concatStringsSep "" (builtins.genList (_: "0") 64);
          };
          resources = {
            vcpus = 1;
            memory_mib = 2048;
            cpu_limit_percent = 100;
            io_weight = 100;
          };
          network = {
            segment = "192.0.2.0/30";
            address = "192.0.2.2/30";
            gateway = "192.0.2.1";
            mac = "02:00:00:00:00:01";
            bridge = "aw-test-br";
            tap = "aw-test-tap";
            uplink = "wan-test";
            dns = [ "1.1.1.1" ];
            ntp = [ "1.1.1.1" ];
            ha_api = "198.51.100.10";
            ingress_sources = [ "198.51.100.0/24" ];
          };
          storage = {
            system_disk = "/persist/agent-workspaces/libvirt-test/disks/system.qcow2";
            system_disk_gib = 16;
            data_disk = "/persist/agent-workspaces/libvirt-test/disks/data.qcow2";
            data_disk_gib = 16;
          };
          ssh_authorized_keys = [ "ssh-ed25519 AAAATEST test" ];
          browser_identity = "test@example.invalid";
          guest_username = "agent";
          secret_references = [ ];
        }
      ];
    }
  );
  # Changes outside this package must not invalidate the libvirt VM result.
  workspaceSources = import ./agent-workspace-python-source.nix { inherit (pkgs) lib; };
  verify = pkgs.writeText "verify-libvirt-normalization.py" ''
    import pathlib
    import subprocess
    import xml.etree.ElementTree as ET

    from scripts.agent_workspaces.core import load_manifest, render_domain
    from scripts.agent_workspaces.lifecycle import _domain_projection

    workspace = load_manifest(pathlib.Path("${manifest}"))[0]
    expected_xml = render_domain(workspace)
    root = ET.fromstring(expected_xml)
    root.set("type", "qemu")
    expected_xml = ET.tostring(root, encoding="unicode")
    domain_path = pathlib.Path("/tmp/domain.xml")
    domain_path.write_text(expected_xml, encoding="utf-8")
    subprocess.run(["virsh", "define", str(domain_path)], check=True)
    actual_xml = subprocess.run(
        ["virsh", "dumpxml", "--inactive", workspace.domain_name],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "<emulator>" in actual_xml
    actual = _domain_projection(actual_xml)
    expected = _domain_projection(expected_xml)
    if actual != expected:
        raise AssertionError(f"actual={actual!r}\nexpected={expected!r}\nxml={actual_xml}")
  '';
in
pkgs.testers.nixosTest {
  name = "agent-workspace-libvirt-normalization";

  nodes.machine = {
    virtualisation.host.pkgs = pkgs.lib.mkForce (import ./vm-host-pkgs.nix { inherit pkgs; });
    imports = [ ../modules/agent-workspaces.nix ];
    networking.hostName = "homelab-01";
    services.agent-workspaces.enable = true;
    environment.systemPackages = [ python ];
    system.stateVersion = "26.05";
  };

  testScript = ''
    machine.start()
    machine.wait_for_unit("libvirtd.service")
    machine.succeed("mkdir -p /persist/agent-workspaces/libvirt-test/{disks,control}")
    machine.succeed("touch /persist/agent-workspaces/libvirt-test/disks/{system,data}.qcow2")
    machine.succeed("touch /persist/agent-workspaces/libvirt-test/control/seed.iso")
    machine.succeed("PYTHONPATH=${workspaceSources} python ${verify}")
  '';
}
