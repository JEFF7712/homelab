# Bookshelf state backup and recovery

Bookshelf owns the `media/bookshelf-config` PVC, backed by NAS directory
`/tank/cluster/media-bookshelf-config-pvc-ba9f64f7-e62f-4d69-9f48-a0715b54d396`.
The NAS `platform-backup` service includes this directory in its encrypted
hourly offsite Restic snapshots under `applications/bookshelf`. Its source
binding is `homelab.platformBackup.bookshelfConfigPath` on `nas-01`.

The service snapshots `tank/cluster`, copies only this PVC from that stable
snapshot, and uses SQLite's backup API to retain committed WAL data in each
database. Stale WAL/SHM sidecars and process IDs are removed from the staged
copy. Missing databases or config files, symlinks, upload errors and integrity
failures stop the backup. Temporary staging and ZFS snapshots are cleaned up
after success or failure. The application keeps running during this process.

The copied state includes `readarr.db`, `cache.db`, `logs.db`, `config.xml`,
cover images and application backup archives. The shared book library is a
separate storage and recovery obligation. Registry artifacts and Forgejo
source are retained by their respective platform procedures.

For a restore drill, select a successful NAS snapshot with the
`sovereign-platform` tag, restore `applications/bookshelf` into a private,
independent directory, and run database integrity checks on that restored
copy. Start the pinned Bookshelf image using only the copied configuration,
a writable disposable volume and `--network=none`. Verify startup, the saved
library records and HTTP behavior without reaching production services.
Never mount the live configuration PVC into the restored application.

Production restoration requires stopping the single state writer and
preserving the current PVC before copying accepted restored state back.
Restore offsite credentials from independent encrypted custody as described
in `forgejo-disaster-recovery.md`. Retain both current and previous image
pins for rollback. A backup timer or live-PVC integrity check alone does not
establish restoration acceptance. Record the chosen snapshot, image digest,
restore duration and observed data counts for each drill; RTO remains
unmeasured until that drill completes.
