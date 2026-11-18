set -eu

for dataset in photos documents; do
    source=$(findmnt --noheadings --raw --output SOURCE --mountpoint "/tank/$dataset") || {
        printf 'Backup refused: /tank/%s is not mounted\n' "$dataset" >&2
        exit 1
    }
    if [ "$source" != "tank/$dataset" ]; then
        printf 'Backup refused: /tank/%s has unexpected source %s\n' "$dataset" "$source" >&2
        exit 1
    fi
done

findmnt --noheadings --mountpoint /mnt/backup-local --types xfs \
    --source /dev/disk/by-id/ata-WDC_WD1003FBYZ-010FB0_WD-WCAW37790774-part1 >/dev/null || {
    printf 'Backup refused: /mnt/backup-local is not the expected XFS disk\n' >&2
    exit 1
}
