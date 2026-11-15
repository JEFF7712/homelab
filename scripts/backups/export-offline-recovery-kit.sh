#!/usr/bin/env bash
# export-offline-recovery-kit.sh: Export an independent offline recovery kit
# for the external 1 TB backup drive on adguard-netbird-01.
#
# This script creates a self-contained offline recovery bundle containing:
# 1. The external backup recovery runbook and restoration instructions.
# 2. Standalone restoration helper scripts for emergency bare-metal recovery.
# 3. Decrypted Restic encryption credentials extracted from SOPS.
# 4. Zero-dependency bootstrap checklist for recovering homelab infrastructure.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

TARGET_DIR="${1:-$REPO_ROOT/.offline-recovery-kit}"

echo "==> Exporting offline recovery kit to: $TARGET_DIR"
mkdir -p "$TARGET_DIR"
chmod 0700 "$TARGET_DIR"

# 1. Copy disaster recovery runbook
echo "==> Staging disaster recovery documentation..."
cp "$REPO_ROOT/docs/runbooks/external-backup-recovery.md" "$TARGET_DIR/RECOVERY_RUNBOOK.md"

# 2. Extract Restic repository password using SOPS
echo "==> Extracting Restic repository credentials via SOPS..."
SOPS_FILE="$REPO_ROOT/secrets/hosts/adguard-netbird-01.sops.yaml"
if [ ! -f "$SOPS_FILE" ]; then
  echo "ERROR: SOPS secrets file not found at $SOPS_FILE" >&2
  exit 1
fi

RESTIC_PASSWORD=$(sops -d --extract '["backup-restic-password"]' "$SOPS_FILE")
if [ -z "$RESTIC_PASSWORD" ]; then
  echo "ERROR: Failed to extract backup-restic-password from $SOPS_FILE" >&2
  exit 1
fi

echo -n "$RESTIC_PASSWORD" > "$TARGET_DIR/restic-password.txt"
chmod 0400 "$TARGET_DIR/restic-password.txt"

# 3. Create standalone emergency restoration script
echo "==> Generating standalone emergency restoration helper script..."
cat << 'EOF' > "$TARGET_DIR/emergency-restore.sh"
#!/usr/bin/env bash
# emergency-restore.sh: Standalone disaster recovery tool for 1TB external backup drive
set -euo pipefail

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASS_FILE="$KIT_DIR/restic-password.txt"

if [ "$EUID" -ne 0 ]; then
  echo "ERROR: Please run as root (sudo ./emergency-restore.sh) to restore files with correct ownership and permissions." >&2
  exit 1
fi

if ! command -v restic >/dev/null 2>&1; then
  echo "ERROR: 'restic' command not found. Please install restic (e.g. 'nix-shell -p restic' or 'apt install restic')." >&2
  exit 1
fi

REPO="${1:-}"
if [ -z "$REPO" ]; then
  echo "Usage: $0 /path/to/restic-repo [target-restore-dir]"
  echo "Example: $0 /mnt/usb/restic-repo /mnt/recovered"
  exit 1
fi

DEST="${2:-/tmp/homelab-recovered}"
mkdir -p "$DEST"
chmod 0700 "$DEST"

export RESTIC_REPOSITORY="$REPO"
export RESTIC_PASSWORD_FILE="$PASS_FILE"

echo "==> Verifying repository access..."
restic snapshots

echo "==> Restoring latest snapshot into $DEST..."
restic restore latest --target "$DEST"

echo "==> Enforcing restrictive permissions on restored secrets..."
find "$DEST" -type f -name "*_key" -exec chmod 0600 {} + 2>/dev/null || true
find "$DEST" -type f -name "*.key" -exec chmod 0600 {} + 2>/dev/null || true
find "$DEST" -type f -name "*token*" -exec chmod 0600 {} + 2>/dev/null || true
find "$DEST" -type f -name "*password*" -exec chmod 0600 {} + 2>/dev/null || true
find "$DEST" -type d -exec chmod 0700 {} + 2>/dev/null || true

echo "==> Restoration complete into: $DEST"
echo "==> See RECOVERY_RUNBOOK.md for instructions on re-importing state into ZFS, k3s, and Forgejo."
EOF
chmod 0700 "$TARGET_DIR/emergency-restore.sh"

# 4. Generate README checklist
cat << EOF > "$TARGET_DIR/README.md"
# Homelab Independent Offline Recovery Kit

This kit allows complete disaster recovery of the homelab from the 1 TB external backup drive, completely independent of all running servers or network services.

## Contents
- \`restic-password.txt\`: Decrypted master encryption password for \`/mnt/backups/restic-repo\`. Mode \`0400\`.
- \`emergency-restore.sh\`: Standalone restoration script (requires only \`restic\` and root access on any Linux machine).
- \`RECOVERY_RUNBOOK.md\`: Step-by-step restoration instructions for Forgejo, k3s, Woodpecker, OpenTofu, ZFS datasets, and apps.

## Usage on Any Clean Machine
1. Attach the 1 TB external backup drive (ext4 filesystem).
2. Mount it: \`sudo mount /dev/sdX1 /mnt/backup-drive\`
3. Run: \`sudo ./emergency-restore.sh /mnt/backup-drive/restic-repo /target/restore/dir\`
4. Follow \`RECOVERY_RUNBOOK.md\` to restore database dumps and restart cluster nodes.
EOF
chmod 0600 "$TARGET_DIR/README.md"

echo "==> Successfully created offline recovery kit at $TARGET_DIR."
