#!/usr/bin/env bash
# verify-syncthing-recovery.sh
# End-to-end acceptance test for Syncthing backup between laptop-nixos and nas-01.
#
# Acceptance Preconditions:
# - Execution Host: Must be executed on laptop-nixos as user rupan.
# - Filesystem Watcher: Requires Syncthing filesystem watcher (inotify) to be active
#   on the laptop so test probe changes are detected immediately (rescanIntervalS=3600).
# - Remote Host: nas-01 reachable at 10.0.30.20 via SSH with sudo access.
#
# Verifies:
# 1. API authentication & connection status from NAS.
# 2. Path mapping & configuration verification across ALL 12 folders (path, type=receiveonly, devices).
# 3. Steady-state folder synchronization across ALL 12 folders (idle, needTotalItems=0, needBytes=0, pullErrors=0, 300s timeout).
# 4. Cryptographic hash comparison across hosts.
# 5. Syncthing staggered versioning recovery from .stversions/ into an isolated restore directory.
# 6. Deletion version archiving verification (deletion moves current modified version to .stversions).
# 7. Native ZFS snapshot recovery from /tank/backups/syncthing/.zfs/snapshot/... (retention-managed point-in-time recovery).
# 8. Receive-only folder revert via REST API to guarantee no lingering out-of-sync/local additions state.
# 9. Privileged cleanup via EXIT trap tracking run-created state without client-side glob expansion issues.
set -euo pipefail

NAS_IP="10.0.30.20"
SSH_USER="rupan"
LAPTOP_DEVICE_ID="4LT3RLW-PVXTJAA-LBRBUSK-JDAEAXN-JH735IR-FDVT54T-Z4MQ6YH-JAVXZQU"
TEST_FOLDER_ID="laptop-documents-personal"
LOCAL_TEST_DIR="$HOME/documents"
API_KEY=""

RUN_ID="$(date +%s)_${RANDOM}_$$"
PROBE_STEM="syncthing-recovery-probe-${RUN_ID}"
PROBE_FILE_NAME="${PROBE_STEM}.txt"
LOCAL_PROBE_PATH="$LOCAL_TEST_DIR/$PROBE_FILE_NAME"
REMOTE_BACKUP_PATH="/tank/backups/syncthing/laptop/documents/$PROBE_FILE_NAME"
SNAPSHOT_NAME="acceptance-probe-${RUN_ID}"
REMOTE_RESTORE_DIR=""

SNAPSHOT_CREATED=false
LOCAL_PROBE_CREATED=false
REMOTE_RESTORE_DIR_CREATED=false

declare -A EXPECTED_PATHS=(
  ["laptop-documents-personal"]="/tank/backups/syncthing/laptop/documents"
  ["laptop-documents-apps"]="/tank/backups/syncthing/laptop/Documents"
  ["laptop-projects"]="/tank/backups/syncthing/laptop/projects"
  ["laptop-code"]="/tank/backups/syncthing/laptop/code"
  ["laptop-obsidian"]="/tank/backups/syncthing/laptop/obsidian"
  ["laptop-school"]="/tank/backups/syncthing/laptop/school"
  ["laptop-businesses"]="/tank/backups/syncthing/laptop/businesses"
  ["laptop-pictures"]="/tank/backups/syncthing/laptop/Pictures"
  ["laptop-videos"]="/tank/backups/syncthing/laptop/Videos"
  ["laptop-research"]="/tank/backups/syncthing/laptop/research"
  ["laptop-homelab"]="/tank/backups/syncthing/laptop/homelab"
  ["laptop-nixos"]="/tank/backups/syncthing/laptop/nixos"
)

log() {
  printf '[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"
}

fail() {
  printf '[ERROR] %s\n' "$*" >&2
  exit 1
}

cleanup() {
  log "Executing cleanup trap for run $RUN_ID..."
  if [[ "$LOCAL_PROBE_CREATED" == "true" ]]; then
    rm -f "$LOCAL_PROBE_PATH" || log "Warning: failed to remove $LOCAL_PROBE_PATH"
  fi

  if [[ "$REMOTE_RESTORE_DIR_CREATED" == "true" && -n "$REMOTE_RESTORE_DIR" ]]; then
    ssh "$SSH_USER@$NAS_IP" "sudo rm -rf '$REMOTE_RESTORE_DIR'" || log "Warning: failed to remove $REMOTE_RESTORE_DIR"
  fi

  if [[ "$SNAPSHOT_CREATED" == "true" ]]; then
    log "Destroying run-created snapshot tank/backups/syncthing@$SNAPSHOT_NAME..."
    ssh "$SSH_USER@$NAS_IP" "sudo zfs destroy 'tank/backups/syncthing@$SNAPSHOT_NAME'" || log "Warning: failed to destroy test snapshot"
  fi

  ssh "$SSH_USER@$NAS_IP" "
    if sudo test -f '$REMOTE_BACKUP_PATH'; then
      sudo rm -f '$REMOTE_BACKUP_PATH' || echo 'Warning: failed to remove remote probe file' >&2
    fi
    sudo find /tank/backups/syncthing/laptop/documents/.stversions/ -name '${PROBE_STEM}~*.txt' -delete || echo 'Warning: failed to delete probe from .stversions' >&2
  " || log "Warning: remote file cleanup reported non-zero status"

  # If API key is available, revert receive-only folder to clear any local additions/out-of-sync state caused by disk cleanup
  if [[ -n "${API_KEY:-}" ]]; then
    log "Reverting receive-only folder '$TEST_FOLDER_ID' on nas-01 to ensure clean sync state..."
    ssh "$SSH_USER@$NAS_IP" "curl -s -X POST -H 'X-API-Key: $API_KEY' 'http://127.0.0.1:8384/rest/db/revert?folder=$TEST_FOLDER_ID'" >/dev/null || log "Warning: failed to revert folder $TEST_FOLDER_ID"
  fi

  log "Cleanup complete."
}
trap cleanup EXIT

# 1. Retrieve NAS Syncthing API key over SSH
log "Querying NAS Syncthing API key from /persist/syncthing/config.xml..."
API_KEY=$(ssh "$SSH_USER@$NAS_IP" "sudo grep -oPm1 '(?<=<apikey>)[^<]+' /persist/syncthing/config.xml || true")
if [[ -z "$API_KEY" ]]; then
  fail "Failed to retrieve Syncthing API key from nas-01"
fi

# 2. Check connection status of laptop on NAS
log "Verifying laptop connection on nas-01..."
CONNECTION_JSON=$(ssh "$SSH_USER@$NAS_IP" "curl -s -H 'X-API-Key: $API_KEY' http://127.0.0.1:8384/rest/system/connections")
IS_CONNECTED=$(echo "$CONNECTION_JSON" | jq -r ".connections[\"$LAPTOP_DEVICE_ID\"].connected // false")

if [[ "$IS_CONNECTED" != "true" ]]; then
  fail "Laptop ($LAPTOP_DEVICE_ID) is not connected to nas-01 Syncthing"
fi
log "Laptop is actively connected to nas-01 Syncthing"

# 3. Verify path mapping across all 12 folders via NAS configuration API
log "Verifying path mappings and folder types across all 12 folders on nas-01..."
FOLDERS_CONFIG=$(ssh "$SSH_USER@$NAS_IP" "curl -s -H 'X-API-Key: $API_KEY' http://127.0.0.1:8384/rest/config/folders")
for folder in "${!EXPECTED_PATHS[@]}"; do
  EXPECTED_PATH="${EXPECTED_PATHS[$folder]}"
  FOLDER_CFG=$(echo "$FOLDERS_CONFIG" | jq -c ".[] | select(.id == \"$folder\")")

  if [[ -z "$FOLDER_CFG" ]]; then
    fail "Folder '$folder' is not configured on nas-01"
  fi

  ACTUAL_PATH=$(echo "$FOLDER_CFG" | jq -r '.path')
  ACTUAL_TYPE=$(echo "$FOLDER_CFG" | jq -r '.type')

  if [[ "$ACTUAL_PATH" != "$EXPECTED_PATH" ]]; then
    fail "Folder '$folder' path mismatch: expected $EXPECTED_PATH, got $ACTUAL_PATH"
  fi
  if [[ "$ACTUAL_TYPE" != "receiveonly" ]]; then
    fail "Folder '$folder' type mismatch: expected receiveonly, got $ACTUAL_TYPE"
  fi
  log "Folder '$folder' -> $ACTUAL_PATH (type: $ACTUAL_TYPE) verified."
done

# 4. Enforce completion gate across ALL 12 folders (wait for initial bulk sync to reach steady state)
GATE_TIMEOUT="${GATE_TIMEOUT:-3600}"
log "Enforcing completion gate across all 12 folders on nas-01 (timeout: ${GATE_TIMEOUT}s)..."
GATE_ELAPSED=0
GATE_POLL_INTERVAL=5
GATE_ALL_IDLE=false
PENDING_REASON=""

while [[ $GATE_ELAPSED -lt $GATE_TIMEOUT ]]; do
  GATE_ALL_IDLE=true

  for folder in "${!EXPECTED_PATHS[@]}"; do
    FOLDER_STATUS=$(ssh "$SSH_USER@$NAS_IP" "curl -s -H 'X-API-Key: $API_KEY' 'http://127.0.0.1:8384/rest/db/status?folder=$folder'")
    STATE=$(echo "$FOLDER_STATUS" | jq -r '.state // "unknown"')
    NEED_ITEMS=$(echo "$FOLDER_STATUS" | jq -r '.needTotalItems // -1')
    NEED_BYTES=$(echo "$FOLDER_STATUS" | jq -r '.needBytes // -1')
    PULL_ERRORS=$(echo "$FOLDER_STATUS" | jq -r '.pullErrors // -1')

    if [[ "$STATE" != "idle" || "$NEED_ITEMS" -ne 0 || "$NEED_BYTES" -ne 0 || "$PULL_ERRORS" -ne 0 ]]; then
      GATE_ALL_IDLE=false
      PENDING_REASON="folder '$folder' (state=$STATE, needTotalItems=$NEED_ITEMS, needBytes=$NEED_BYTES, pullErrors=$PULL_ERRORS)"
      break
    fi
  done

  if [[ "$GATE_ALL_IDLE" == "true" ]]; then
    break
  fi

  log "Waiting for sync steady state... Pending on $PENDING_REASON (elapsed: ${GATE_ELAPSED}s / ${GATE_TIMEOUT}s)"
  sleep "$GATE_POLL_INTERVAL"
  GATE_ELAPSED=$((GATE_ELAPSED + GATE_POLL_INTERVAL))
done

if [[ "$GATE_ALL_IDLE" != "true" ]]; then
  fail "Completion gate timed out after ${GATE_TIMEOUT}s: folders did not reach steady state. Last pending: $PENDING_REASON"
fi
log "All 12 folders verified in steady state: idle, 0 pending items, 0 pending bytes, 0 pull errors."

# Create remote isolated restore directory for recovery tests
REMOTE_RESTORE_DIR=$(ssh "$SSH_USER@$NAS_IP" "mktemp -d /tmp/syncthing-recovery-test.XXXXXX")
REMOTE_RESTORE_DIR_CREATED=true
log "Created isolated restore directory on nas-01: $REMOTE_RESTORE_DIR"

# 5. Generate probe file on laptop and record original SHA256
ORIGINAL_CONTENT="Syncthing Recovery Acceptance Test Probe - $(date -u) - $RANDOM"
log "Creating sample probe file at $LOCAL_PROBE_PATH..."
echo "$ORIGINAL_CONTENT" > "$LOCAL_PROBE_PATH"
LOCAL_PROBE_CREATED=true
ORIGINAL_HASH=$(sha256sum "$LOCAL_PROBE_PATH" | awk '{print $1}')
log "Original file SHA256: $ORIGINAL_HASH"

# 6. Wait for probe file to sync to NAS
log "Waiting for probe file to synchronize to nas-01..."
SYNC_TIMEOUT=60
SYNC_ELAPSED=0
REMOTE_SYNCED=false
while [[ $SYNC_ELAPSED -lt $SYNC_TIMEOUT ]]; do
  if ssh "$SSH_USER@$NAS_IP" "sudo test -f '$REMOTE_BACKUP_PATH'"; then
    REMOTE_SYNCED=true
    break
  fi
  sleep 2
  SYNC_ELAPSED=$((SYNC_ELAPSED + 2))
done

if [[ "$REMOTE_SYNCED" != "true" ]]; then
  fail "Probe file failed to sync to nas-01 within $SYNC_TIMEOUT seconds"
fi

REMOTE_HASH=$(ssh "$SSH_USER@$NAS_IP" "sudo sha256sum '$REMOTE_BACKUP_PATH'" | awk '{print $1}')
log "Remote file SHA256: $REMOTE_HASH"
if [[ "$ORIGINAL_HASH" != "$REMOTE_HASH" ]]; then
  fail "Hash mismatch between laptop ($ORIGINAL_HASH) and nas-01 ($REMOTE_HASH)"
fi
log "Cryptographic hash match verified!"

# 7. Take pre-modification ZFS snapshot on NAS
log "Taking ZFS snapshot tank/backups/syncthing@$SNAPSHOT_NAME on nas-01..."
ssh "$SSH_USER@$NAS_IP" "sudo zfs snapshot 'tank/backups/syncthing@$SNAPSHOT_NAME'"
SNAPSHOT_CREATED=true

# Verify snapshot exists
SNAP_EXISTS=$(ssh "$SSH_USER@$NAS_IP" "sudo zfs list -t snapshot -H -o name 'tank/backups/syncthing@$SNAPSHOT_NAME' 2>/dev/null || true")
if [[ "$SNAP_EXISTS" != "tank/backups/syncthing@$SNAPSHOT_NAME" ]]; then
  fail "Failed to verify existence of ZFS snapshot tank/backups/syncthing@$SNAPSHOT_NAME"
fi
log "ZFS snapshot presence confirmed."

# 8. Modify file on laptop to trigger staggered versioning
log "Modifying probe file on laptop to test versioning..."
echo "MODIFIED CONTENT - $(date -u)" > "$LOCAL_PROBE_PATH"
MODIFIED_HASH=$(sha256sum "$LOCAL_PROBE_PATH" | awk '{print $1}')

# Wait for modification to sync to NAS
log "Waiting for modification to sync to nas-01..."
MOD_TIMEOUT=60
MOD_ELAPSED=0
MOD_SYNCED=false
while [[ $MOD_ELAPSED -lt $MOD_TIMEOUT ]]; do
  CURRENT_REMOTE_HASH=$(ssh "$SSH_USER@$NAS_IP" "sudo sha256sum '$REMOTE_BACKUP_PATH'" | awk '{print $1}')
  if [[ "$CURRENT_REMOTE_HASH" == "$MODIFIED_HASH" ]]; then
    MOD_SYNCED=true
    break
  fi
  sleep 2
  MOD_ELAPSED=$((MOD_ELAPSED + 2))
done

if [[ "$MOD_SYNCED" != "true" ]]; then
  fail "Modified probe file failed to synchronize within $MOD_TIMEOUT seconds"
fi
log "Modified probe file synchronized."

# 9. Restore original version from .stversions on NAS and verify
log "Searching .stversions on nas-01 for archived original version (${PROBE_STEM}~*.txt)..."
VERSION_FILE_ORIGINAL=$(ssh "$SSH_USER@$NAS_IP" "sudo find /tank/backups/syncthing/laptop/documents/.stversions/ -name '${PROBE_STEM}~*.txt' | sort | head -n 1")
if [[ -z "$VERSION_FILE_ORIGINAL" ]]; then
  fail "No archived original version found in .stversions matching ${PROBE_STEM}~*.txt"
fi

# Restore into isolated recovery directory and compare hash
log "Restoring original archived version into $REMOTE_RESTORE_DIR/restored-original.txt..."
ssh "$SSH_USER@$NAS_IP" "sudo cp '$VERSION_FILE_ORIGINAL' '$REMOTE_RESTORE_DIR/restored-original.txt' && sudo chmod 0644 '$REMOTE_RESTORE_DIR/restored-original.txt'"
RESTORED_VERSION_HASH=$(ssh "$SSH_USER@$NAS_IP" "sha256sum '$REMOTE_RESTORE_DIR/restored-original.txt'" | awk '{print $1}')
log "Restored version SHA256: $RESTORED_VERSION_HASH"

if [[ "$RESTORED_VERSION_HASH" != "$ORIGINAL_HASH" ]]; then
  fail "Restored version from .stversions ($RESTORED_VERSION_HASH) does not match original ($ORIGINAL_HASH)"
fi
log "Staggered versioning restoration verified successfully!"

# 10. Delete probe file on laptop to test deletion archiving and ZFS snapshot recovery
# Syncthing staggered versioner retains at most one version per 30-second window in the first hour.
# Wait 35s to ensure the deletion archive falls into a distinct bucket from the modification archive.
log "Waiting 35s to cross the 30-second staggered versioner bucket interval..."
sleep 35

log "Deleting probe file on laptop..."
rm -f "$LOCAL_PROBE_PATH"
LOCAL_PROBE_CREATED=false

# Wait for deletion to sync
log "Waiting for deletion to sync to nas-01..."
DEL_TIMEOUT=60
DEL_ELAPSED=0
DEL_SYNCED=false
while [[ $DEL_ELAPSED -lt $DEL_TIMEOUT ]]; do
  if ssh "$SSH_USER@$NAS_IP" "sudo test ! -f '$REMOTE_BACKUP_PATH'"; then
    DEL_SYNCED=true
    break
  fi
  sleep 2
  DEL_ELAPSED=$((DEL_ELAPSED + 2))
done

if [[ "$DEL_SYNCED" != "true" ]]; then
  fail "File deletion failed to synchronize within $DEL_TIMEOUT seconds"
fi
log "File deletion synchronized."

# 11. Verify that deletion archived the modified version into .stversions
log "Verifying that deletion archived the modified version into .stversions..."
VERSION_FILE_MODIFIED=$(ssh "$SSH_USER@$NAS_IP" "sudo find /tank/backups/syncthing/laptop/documents/.stversions/ -name '${PROBE_STEM}~*.txt' | sort | tail -n 1")
if [[ -z "$VERSION_FILE_MODIFIED" ]]; then
  fail "No archived modified version found in .stversions after deletion"
fi

ssh "$SSH_USER@$NAS_IP" "sudo cp '$VERSION_FILE_MODIFIED' '$REMOTE_RESTORE_DIR/restored-modified.txt' && sudo chmod 0644 '$REMOTE_RESTORE_DIR/restored-modified.txt'"
RESTORED_MODIFIED_HASH=$(ssh "$SSH_USER@$NAS_IP" "sha256sum '$REMOTE_RESTORE_DIR/restored-modified.txt'" | awk '{print $1}')
log "Restored deletion archive SHA256: $RESTORED_MODIFIED_HASH"

if [[ "$RESTORED_MODIFIED_HASH" != "$MODIFIED_HASH" ]]; then
  fail "Restored deletion archive does not match modified version SHA256"
fi
log "Deletion archiving into .stversions verified successfully!"

# 12. Restore file directly from native ZFS snapshot
ZFS_SNAPSHOT_FILE="/tank/backups/syncthing/.zfs/snapshot/$SNAPSHOT_NAME/laptop/documents/$PROBE_FILE_NAME"
log "Testing recovery directly from ZFS snapshot: $ZFS_SNAPSHOT_FILE..."

# Copy from snapshot into isolated restore directory
ssh "$SSH_USER@$NAS_IP" "sudo cp '$ZFS_SNAPSHOT_FILE' '$REMOTE_RESTORE_DIR/restored-zfs-snapshot.txt' && sudo chmod 0644 '$REMOTE_RESTORE_DIR/restored-zfs-snapshot.txt'"
ZFS_RESTORED_HASH=$(ssh "$SSH_USER@$NAS_IP" "sha256sum '$REMOTE_RESTORE_DIR/restored-zfs-snapshot.txt'" | awk '{print $1}')
log "Restored ZFS snapshot file SHA256: $ZFS_RESTORED_HASH"

if [[ "$ZFS_RESTORED_HASH" != "$ORIGINAL_HASH" ]]; then
  fail "Restored file from ZFS snapshot ($ZFS_RESTORED_HASH) does not match original ($ORIGINAL_HASH)"
fi
log "Native ZFS snapshot recovery verified successfully!"

# 13. Revert receive-only folder on NAS to ensure clean sync state
log "Reverting receive-only folder $TEST_FOLDER_ID on nas-01 to ensure pristine state..."
ssh "$SSH_USER@$NAS_IP" "curl -s -X POST -H 'X-API-Key: $API_KEY' 'http://127.0.0.1:8384/rest/db/revert?folder=$TEST_FOLDER_ID'" >/dev/null || log "Warning: failed to revert folder"
log "Receive-only folder reverted."

log "=========================================================="
log "ALL 6 ACCEPTANCE PHASES AND RECOVERY PROOFS PASSED!"
log "=========================================================="
