"""Online SQLite backup, config/plugin archive, and retention for Jellyfin."""

import datetime
import glob
import os
import sqlite3
import sys
import tarfile

CONFIG_ROOT = "/config"
DB_PATH = "/config/data/data/jellyfin.db"
BACKUP_DIR = "/backups/SQLiteBackups"
CONFIG_BACKUP_DIR = "/backups/ConfigBackups"
# Server settings, users, and installed plugins with their settings.
# metadata/ and cache/ are regenerable and excluded.
CONFIG_PATHS = ["*.xml", "users", "data/plugins"]
RETENTION_DAYS = 7


def prune(pattern: str, now: datetime.datetime) -> None:
    for f in glob.glob(pattern):
        try:
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(f))
            if (now - mtime).days > RETENTION_DAYS:
                os.remove(f)
                print(f"Pruned old backup: {f}")
        except OSError as e:
            print(f"Warning: failed to remove {f}: {e}", file=sys.stderr)


def run_config_backup(today: str) -> None:
    members = []
    for pattern in CONFIG_PATHS:
        matches = sorted(glob.glob(os.path.join(CONFIG_ROOT, pattern)))
        if not matches:
            print(f"Error: no config files match {pattern}", file=sys.stderr)
            sys.exit(1)
        members.extend(matches)

    os.makedirs(CONFIG_BACKUP_DIR, exist_ok=True)
    archive = os.path.join(CONFIG_BACKUP_DIR, f"jellyfin-config-{today}.tar.gz")
    tmp_archive = archive + ".tmp"
    with tarfile.open(tmp_archive, "w:gz") as tar:
        for path in members:
            tar.add(path, arcname=os.path.relpath(path, CONFIG_ROOT))

    with tarfile.open(tmp_archive, "r:gz") as tar:
        names = tar.getnames()
    if "system.xml" not in names:
        raise RuntimeError("Config archive is missing system.xml")

    os.replace(tmp_archive, archive)
    print(f"Successfully saved {archive} ({len(names)} entries, {os.path.getsize(archive)} bytes)")


def run_backup() -> None:
    if not os.path.isfile(DB_PATH):
        print(f"Error: Database not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(BACKUP_DIR, exist_ok=True)
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    backup_file = os.path.join(BACKUP_DIR, f"jellyfin-{today}.db")
    tmp_backup_file = backup_file + ".tmp"

    if os.path.exists(tmp_backup_file):
        os.remove(tmp_backup_file)

    print(f"Starting online backup of {DB_PATH} -> {backup_file}...")
    source = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    dest = sqlite3.connect(tmp_backup_file)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()

    # Validate integrity of the newly created backup
    check = sqlite3.connect(tmp_backup_file)
    try:
        cursor = check.cursor()
        cursor.execute("PRAGMA integrity_check;")
        result = cursor.fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"Backup integrity check failed: {result}")
        print("Backup integrity verified: ok")
    finally:
        check.close()

    os.replace(tmp_backup_file, backup_file)
    print(f"Successfully saved {backup_file} ({os.path.getsize(backup_file)} bytes)")

    run_config_backup(today)

    now = datetime.datetime.now()
    prune(os.path.join(BACKUP_DIR, "jellyfin-*.db"), now)
    prune(os.path.join(CONFIG_BACKUP_DIR, "jellyfin-config-*.tar.gz"), now)


if __name__ == "__main__":
    run_backup()
