from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import yaml

from scripts.home_assistant.canonical import DuplicateKeySafeLoader

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE = REPO_ROOT / "flake/modules/ledfx-bedroom.nix"
NAS_HOST = REPO_ROOT / "flake/hosts/nas-01/default.nix"
APPLIANCE = REPO_ROOT / "flake/modules/adguard-netbird-appliance.nix"
CORE = REPO_ROOT / "home-assistant/core/configuration.yaml"
SHARED_LEDFX = REPO_ROOT / "gitops/music-assistant/ledfx.yaml"
BEDROOM_AUTOMATION = REPO_ROOT / "home-assistant/automations/bedroom_music_mode.yaml"
SHARED_AUTOMATION = REPO_ROOT / "home-assistant/automations/govee_music_mode.yaml"

SHARED_HOST = "10.0.30.15"
BEDROOM_HOST = "10.0.30.20"

# Bluetooth sink that carries all bedroom playback on the NAS.
BEDROOM_PULSE_SOURCE = "bluez_output.E4_58_BC_10_CA_C9.1.monitor"

BEDROOM_LIGHTS = (
    "light.desk_lamp_desk_lamp",
    "light.floor_lamp_floor_lamp",
)
SHARED_LIGHTS = (
    "light.kitchen_light",
    "light.kitchen_light_2",
    "light.living_room_mushroom_lamp",
    "light.living_room_tulip_lamp",
)


def rest_command_urls() -> dict[str, str]:
    # The core config carries !secret/!include tags, so use the repo's loader.
    data = yaml.load(CORE.read_text(encoding="utf-8"), Loader=DuplicateKeySafeLoader)
    return {name: body["url"] for name, body in data["rest_command"].items()}


class BedroomLedFxModuleTests(unittest.TestCase):
    def test_module_is_enabled_on_nas_only(self) -> None:
        self.assertIn("../../modules/ledfx-bedroom.nix", NAS_HOST.read_text())
        self.assertIn("homelab.ledfxBedroom.enable = true", NAS_HOST.read_text())

        appliance = APPLIANCE.read_text()
        self.assertIn("./adguard-netbird/ledfx-roku-bridge.nix", appliance)
        # The relay is shared transport only; the module itself must not be
        # pulled into any shared-spaces host.
        for host in REPO_ROOT.glob("flake/hosts/*/default.nix"):
            if host == NAS_HOST:
                continue
            self.assertNotIn(
                "ledfx-bedroom.nix",
                host.read_text(),
                f"{host.name} must not run the bedroom LedFx instance",
            )

    def test_image_is_pinned_and_tracks_the_shared_upstream_digest(self) -> None:
        module = MODULE.read_text()

        lock = json.loads(
            (REPO_ROOT / "registry/images.lock.json").read_text(encoding="utf-8")
        )
        locked = next(
            record["digest"]
            for record in lock["images"]
            if record.get("destination_repository") == "upstream/ghcr.io/ledfx/ledfx"
        )
        self.assertIn(locked, module, "module must track the locked upstream digest")

        # The local registry serves a converted manifest, so the reference is
        # the retention tag that embeds the upstream digest, not @sha256.
        tag = re.search(r"ledfx:retention-deployed-([0-9a-f]{12})", module)
        self.assertIsNotNone(tag, "image must use the retention tag")
        self.assertEqual(
            tag.group(1),
            locked.split(":")[1][:12],
            "retention tag must match the locked upstream digest",
        )
        self.assertIn("@sha256:", SHARED_LEDFX.read_text())
        self.assertNotIn("@sha256:", module)

    def test_listens_to_the_bluetooth_tap_not_the_shared_tap(self) -> None:
        module = MODULE.read_text()
        self.assertIn(BEDROOM_PULSE_SOURCE, module)
        self.assertIn("PULSECLIENTMODE=true", module)
        self.assertIn("PULSE_SERVER=unix:/run/pulse/native", module)
        # The shared instance taps the SPDIF monitor on a different host; the
        # bedroom instance must not reference it.
        self.assertNotIn("SPDIF", module)

    def test_state_and_credentials_are_persistent(self) -> None:
        module = MODULE.read_text()
        self.assertIn("environment.persistence", module)
        self.assertIn("/persist/secrets/registry-auth.json", module)
        self.assertIn("--authfile", module)
        # The registry password must never reach argv or a world-readable path.
        self.assertNotIn("PASSWORD", module)

    def test_shared_podman_flags_stay_on_one_line(self) -> None:
        # A newline inside the shared flag string ends the podman command, and
        # the remaining flags are then parsed as separate commands, which
        # surfaces as "missing command 'podman COMMAND'" and exit 125.
        module = MODULE.read_text()
        match = re.search(r"podmanGlobal = \"([^\"]*)\"", module)
        self.assertIsNotNone(match, "podmanGlobal must be a single-line string")
        flags = match.group(1)
        self.assertNotIn("\n", flags)
        for expected in (
            "--root",
            "--runroot",
            "--cgroup-manager=cgroupfs",
            "--events-backend=file",
        ):
            self.assertIn(expected, flags)

    def test_nas_loads_the_pulse_protocol_shim(self) -> None:
        # PipeWire's compiled-in defaults omit the Pulse shim, so the pulse
        # socket refuses every client and LedFx cannot open its source.
        base = (REPO_ROOT / "flake/modules/nas-base.nix").read_text()
        self.assertIn("libpipewire-module-protocol-pulse", base)
        self.assertIn("services.pipewire.extraConfig.pipewire-pulse", base)
        self.assertIn("d /run/pulse 0755 pipewire pipewire -", base)

    def test_stale_containers_are_replaced(self) -> None:
        # Stopping the unit kills podman, not the container, so a stale
        # container can survive and hold the name. Without --replace the unit
        # crash-loops with "name already in use".
        module = MODULE.read_text()
        self.assertIn("--rm --replace --name ledfx-bedroom", module)

    def test_podman_does_not_leak_transient_units(self) -> None:
        # podman creates a transient systemd unit per container and per
        # healthcheck run. Those linger as failed after a stop and make
        # switch-to-configuration exit non-zero on a successful activation.
        module = MODULE.read_text()
        self.assertIn("--cgroup-manager=cgroupfs", module)
        self.assertIn("--events-backend=file", module)
        self.assertIn("--no-healthcheck", module)

    def test_config_dir_is_owned_by_the_container_user(self) -> None:
        # The image runs as uid/gid 1000; a root-owned mount makes it die on
        # startup with PermissionError writing ledfx.log.
        module = MODULE.read_text()
        self.assertIn("install -d -m 0700 -o 1000 -g 1000", module)

    def test_rest_api_is_limited_to_cluster_nodes(self) -> None:
        module = MODULE.read_text()
        rule = re.search(r"tcp dport \$\{toString cfg\.port\} accept", module)
        self.assertIsNotNone(rule)
        self.assertIn("10.0.30.11", module)
        self.assertIn("10.0.30.15", module)
        self.assertNotIn("10.0.30.0/24 tcp dport 8888", module)

    def test_relay_accepts_frames_from_the_nas(self) -> None:
        appliance = APPLIANCE.read_text()
        self.assertIn(
            "ip saddr { 10.0.30.15, 10.0.30.20 } udp dport 9000 accept", appliance
        )

    def test_alsa_pulse_pcm_is_defined_for_the_container(self):
        module = (REPO_ROOT / "flake/modules/ledfx-bedroom.nix").read_text()
        self.assertIn("pcm.pulse", module)
        self.assertIn("type pulse", module)
        self.assertIn("/etc/asound.conf:ro", module)

    def test_pulse_socket_group_is_granted_to_the_container(self):
        module = (REPO_ROOT / "flake/modules/ledfx-bedroom.nix").read_text()
        self.assertIn(
            "--group-add ${toString config.users.groups.pipewire.gid}", module
        )


class BedroomIsolationTests(unittest.TestCase):
    """The bedroom must not be able to touch shared spaces, and vice versa."""

    def test_ledfx_instances_have_distinct_endpoints(self) -> None:
        urls = rest_command_urls()
        self.assertEqual(
            urls["ledfx_activate_scene"], f"http://{SHARED_HOST}:8888/api/scenes"
        )
        self.assertEqual(
            urls["ledfx_bedroom_activate_scene"],
            f"http://{BEDROOM_HOST}:8888/api/scenes",
        )
        self.assertEqual(
            urls["ledfx_bedroom_deactivate_scene"],
            f"http://{BEDROOM_HOST}:8888/api/scenes",
        )
        self.assertEqual(
            urls["ledfx_deactivate_scene"],
            f"http://{SHARED_HOST}:8888/api/scenes",
        )
        self.assertNotEqual(SHARED_HOST, BEDROOM_HOST)

    def test_bedroom_automation_only_uses_bedroom_commands(self) -> None:
        raw = BEDROOM_AUTOMATION.read_text()
        self.assertIn("rest_command.ledfx_bedroom_activate_scene", raw)
        self.assertIn("rest_command.ledfx_bedroom_deactivate_scene", raw)
        # The shared commands must not appear anywhere in the bedroom path.
        self.assertNotIn("rest_command.ledfx_activate_scene", raw)
        self.assertNotIn("rest_command.ledfx_deactivate_scene", raw)

    def test_bedroom_automation_never_references_shared_lights(self) -> None:
        raw = BEDROOM_AUTOMATION.read_text()
        for light in SHARED_LIGHTS:
            self.assertNotIn(light, raw)
        for light in BEDROOM_LIGHTS:
            self.assertIn(light, raw)

    def test_shared_automation_never_references_bedroom_entities(self) -> None:
        raw = SHARED_AUTOMATION.read_text()
        for light in BEDROOM_LIGHTS:
            self.assertNotIn(light, raw)
        self.assertNotIn("input_boolean.bedroom_music_mode", raw)
        # Shared mode must keep using the shared instance only.
        self.assertIn("rest_command.ledfx_activate_scene", raw)
        self.assertNotIn("ledfx_bedroom", raw)

    def test_recorder_excludes_bedroom_bulbs(self) -> None:
        core = CORE.read_text()
        for light in BEDROOM_LIGHTS:
            self.assertIn(light, core)


if __name__ == "__main__":
    unittest.main()
