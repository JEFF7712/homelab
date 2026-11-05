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
        # Database freshness and cycle manifest checks
        self.assertIn("CURRENT_CYCLE=", content)
        self.assertIn("backup-manifest.json", content)
        self.assertIn("MAX_MANIFEST_AGE=", content)
        self.assertIn("immich-*.dump.gz", content)
        self.assertIn("home-assistant-*.dump.gz", content)
        # ZFS frozen snapshot mount for consistent PVC backups
        self.assertIn("tank/cluster/.zfs/snapshot/backup-nightly", content)
        self.assertIn("NAS_CLUSTER_MOUNT=", content)
        # Timing coordination with k3s
        self.assertIn("k3s-backup-prep cycle", content)
        # Permissions isolation
        self.assertIn("chmod 0700 /mnt/backups", content)
        self.assertIn('"d /mnt/backups 0700 root root -"', content)
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
        self.assertIn("--group-by host", content)
        # Source validations for expanded datasets
        self.assertIn("etcd-snapshot-latest", content)
        self.assertIn("WP_DB_FILE", content)
        self.assertIn("tofu-state/state.sqlite", content)
        self.assertIn("AdGuardHome.yaml", content)
        self.assertIn("media-bookshelf-config-pvc-*", content)
        self.assertIn("media-sonarr-config-pvc-*", content)
        self.assertIn("media-radarr-config-pvc-*", content)
        self.assertIn("media-prowlarr-config-pvc-*", content)
        self.assertIn("media-calibre-config-pvc-*", content)
        self.assertIn("media-navidrome-data-pvc-*", content)
        self.assertIn("obsidian-couchdb-data-pvc-*", content)
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
        self.assertIn("backup-manifest.json", content)
        self.assertIn('OnCalendar = "02:50:00"', content)
        self.assertIn("systemd.services.platform-state-backup-dump", content)
        self.assertIn('OnCalendar = "02:55:00"', content)
        self.assertIn("tank/cluster@backup-nightly", content)
        self.assertIn("/tank/backups/platform-state", content)

    def test_k3s_backup_source_contract(self) -> None:
        content = (REPO_ROOT / "flake/modules/k3s-backup-source.nix").read_text()
        self.assertIn("users.users.backup-reader", content)
        self.assertIn("ForceCommand internal-sftp -R", content)
        self.assertIn("systemd.services.k3s-backup-prep", content)
        self.assertIn("backup-manifest.json", content)
        self.assertIn('OnCalendar = "03:15:00"', content)
        host_content = (REPO_ROOT / "flake/hosts/homelab-01/default.nix").read_text()
        self.assertIn("k3s-backup-source.nix", host_content)

    def test_woodpecker_backup_source_contract(self) -> None:
        content = (REPO_ROOT / "flake/modules/woodpecker-backup-source.nix").read_text()
        self.assertIn("users.users.backup-reader", content)
        self.assertIn("ForceCommand internal-sftp -R", content)
        self.assertIn("systemd.services.woodpecker-backup-dump", content)
        self.assertIn("backup-manifest.json", content)
        self.assertIn('OnCalendar = "02:40:00"', content)
        host_content = (REPO_ROOT / "flake/hosts/homelab-04/default.nix").read_text()
        self.assertIn("woodpecker-backup-source.nix", host_content)

    def test_nas_sanoid_includes_forgejo(self) -> None:
        content = (REPO_ROOT / "flake/modules/nas-data.nix").read_text()
        self.assertIn('"tank/forgejo".useTemplate = [ "operational" ];', content)

    def test_offline_recovery_kit_script_exists(self) -> None:
        script = REPO_ROOT / "scripts/backups/export-offline-recovery-kit.sh"
        self.assertTrue(script.is_file())
        content = script.read_text()
        self.assertIn("backup-restic-password", content)
        self.assertIn("emergency-restore.sh", content)
        self.assertIn("chmod 0600", content)

    def test_runbook_exists(self) -> None:
        runbook = REPO_ROOT / "docs/runbooks/external-backup-recovery.md"
        self.assertTrue(runbook.is_file())
        text = runbook.read_text()
        self.assertIn("00d3f54f-28f8-454c-929a-8c8191ab3624", text)
        self.assertIn("restic restore", text)
        self.assertIn("Cycle Verification Protocol", text)
        self.assertIn("export-offline-recovery-kit.sh", text)


if __name__ == "__main__":
    unittest.main()
