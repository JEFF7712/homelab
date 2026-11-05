import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class ExternalBackupContractTests(unittest.TestCase):
    def test_adguard_mount_declared(self) -> None:
        content = (REPO_ROOT / "flake/hosts/adguard-netbird-01/default.nix").read_text()
        self.assertIn('fileSystems."/mnt/backups"', content)
        self.assertIn("00d3f54f-28f8-454c-929a-8c8191ab3624", content)
        self.assertIn('"ext4"', content)
        self.assertIn('"nofail"', content)

    def test_adguard_backup_module_guards(self) -> None:
        content = (REPO_ROOT / "flake/modules/adguard-netbird/backups.nix").read_text()
        # Fail-closed checks
        self.assertIn(".backup-target-canary", content)
        self.assertIn("mountpoint -q /mnt/backups", content)
        self.assertIn("immich_backup_last_status", content)
        self.assertIn("immich-external-ops.lock", content)
        # Database freshness checks
        self.assertIn("immich-*.dump.gz", content)
        self.assertIn("home-assistant-*.dump.gz", content)
        self.assertIn("forgejo-*.sqlite", content)
        # Timers and safety
        self.assertIn('OnCalendar = "03:30:00"', content)
        self.assertIn("systemd.timers.immich-external-backup-prune = {", content)
        self.assertNotIn("enable = false;", content)
        self.assertIn("--keep-last 3", content)
        self.assertIn("--keep-daily 14", content)
        self.assertIn("--keep-weekly 8", content)
        self.assertIn("--keep-monthly 3", content)
        # Consistent host and tags
        self.assertIn("--host adguard-netbird-01", content)
        self.assertIn("--group-by host,tags", content)
        # SMART monitoring
        self.assertIn("services.smartd", content)

    def test_backup_prometheus_alerts_declared(self) -> None:
        content = (
            REPO_ROOT / "gitops/observability/kube-prometheus-stack/cluster-rules.yaml"
        ).read_text()
        self.assertIn("- alert: ExternalBackupFailed", content)
        self.assertIn("- alert: ExternalBackupStale", content)
        self.assertIn("- alert: ExternalBackupMissing", content)
        self.assertIn(
            'immich_backup_last_status{job="adguard-netbird-01-node"} == 0', content
        )
        self.assertIn(
            'immich_backup_last_success_timestamp_seconds{job="adguard-netbird-01-node"} > 129600',
            content,
        )
        self.assertIn(
            'absent(immich_backup_last_success_timestamp_seconds{job="adguard-netbird-01-node"})',
            content,
        )

    def test_nas_backup_source_contract(self) -> None:
        content = (REPO_ROOT / "flake/modules/nas-backup-source.nix").read_text()
        self.assertIn("users.users.backup-reader", content)
        self.assertIn("ForceCommand internal-sftp -R", content)
        self.assertIn("AllowTcpForwarding no", content)
        self.assertIn("systemd.services.forgejo-backup-dump", content)
        self.assertIn('OnCalendar = "02:50:00"', content)

    def test_nas_sanoid_includes_forgejo(self) -> None:
        content = (REPO_ROOT / "flake/modules/nas-data.nix").read_text()
        self.assertIn('"tank/forgejo".useTemplate = [ "operational" ];', content)

    def test_runbook_exists(self) -> None:
        runbook = REPO_ROOT / "docs/runbooks/external-backup-recovery.md"
        self.assertTrue(runbook.is_file())
        text = runbook.read_text()
        self.assertIn("00d3f54f-28f8-454c-929a-8c8191ab3624", text)
        self.assertIn("restic restore", text)


if __name__ == "__main__":
    unittest.main()
