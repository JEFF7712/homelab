from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLAKE = f"path:{ROOT}?dir=flake#nixosConfigurations"


def evaluate(attribute: str, apply: str) -> dict:
    return json.loads(
        subprocess.run(
            ["nix", "eval", "--json", f"{FLAKE}.{attribute}", "--apply", apply],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )


def built_script(host: str, unit: str, script_name: str) -> str:
    """Build a (tiny) unit file and return the text of the script it runs."""
    out = subprocess.run(
        [
            "nix",
            "build",
            "--no-link",
            "--print-out-paths",
            f'{FLAKE}.{host}.config.systemd.units."{unit}".unit',
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    unit_text = (Path(out) / unit).read_text()
    match = re.search(rf"/nix/store/[^\s=]+-{re.escape(script_name)}\b", unit_text)
    assert match, f"{script_name} not referenced by {unit}"
    return Path(match.group(0)).read_text()


@unittest.skipUnless(
    shutil.which("nix"), "nix is required for configuration evaluation"
)
class OffsiteBackupTest(unittest.TestCase):
    def test_pc_serves_an_append_only_repository_only_over_netbird(self) -> None:
        server = evaluate(
            "home-pc-01.config",
            "c: {"
            " inherit (c.services.restic.server) enable appendOnly dataDir extraFlags;"
            " wt0 = c.networking.firewall.interfaces.wt0.allowedTCPPorts;"
            " global = c.networking.firewall.allowedTCPPorts;"
            " unit = c.systemd.services.restic-rest-server.unitConfig;"
            " service = c.systemd.services.restic-rest-server.serviceConfig; }",
        )
        self.assertTrue(server["enable"])
        # A compromised homelab can add snapshots but never delete them.
        self.assertTrue(server["appendOnly"])
        self.assertIn("--no-auth", server["extraFlags"])
        # Unauthenticated, so reachable only on the mesh interface.
        self.assertEqual(server["wt0"], [8000])
        self.assertNotIn(8000, server["global"])
        # /home is nofail; a missing mount must not fill the root filesystem.
        self.assertEqual(server["unit"]["RequiresMountsFor"], [server["dataDir"]])
        self.assertTrue(server["dataDir"].startswith("/home/"))
        # The service sees only its repository, never the family's homes.
        self.assertEqual(server["service"]["ProtectHome"], "tmpfs")
        self.assertEqual(server["service"]["BindPaths"], [server["dataDir"]])

    def test_adguard_copies_the_external_repository_weekly(self) -> None:
        copy = evaluate(
            "adguard-netbird-01.config",
            "c: {"
            " timer = c.systemd.timers.immich-offsite-copy.timerConfig.OnCalendar;"
            " exec = c.systemd.services.immich-offsite-copy.serviceConfig.ExecStart;"
            " credentials = c.systemd.services.immich-offsite-copy"
            ".serviceConfig.LoadCredential;"
            " mounts = c.systemd.services.immich-offsite-copy"
            ".unitConfig.RequiresMountsFor; }",
        )
        # Weekly, after the Sunday prune and check; stops itself before the next.
        self.assertEqual(copy["timer"], "Mon *-*-* 09:30:00")
        self.assertEqual(copy["mounts"], ["/mnt/backups"])
        self.assertEqual(len(copy["credentials"]), 1)
        self.assertTrue(copy["credentials"][0].startswith("password:"))
        self.assertTrue(copy["exec"].endswith("run-immich-offsite-copy"))
        script = built_script(
            "adguard-netbird-01",
            "immich-offsite-copy.service",
            "run-immich-offsite-copy",
        )
        self.assertIn(
            'RESTIC_REPOSITORY="rest:http://100.94.53.129:8000/homelab/"', script
        )
        self.assertIn('restic init --from-repo "$SOURCE" --copy-chunker-params', script)
        self.assertIn('restic copy --from-repo "$SOURCE"', script)
        self.assertIn('date -d "sunday 05:30"', script)
        # Never prune or forget offsite: the server is append-only anyway.
        self.assertNotIn("restic forget", script)
        self.assertNotIn("restic prune", script)


if __name__ == "__main__":
    unittest.main()
