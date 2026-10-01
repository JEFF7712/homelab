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

findmnt --noheadings --mountpoint /mnt/backup-2tb --types xfs \
    --source /dev/disk/by-id/ata-ST2000DM008-2FR102_ZFL60NJG-part1 >/dev/null || {
    printf 'Backup refused: /mnt/backup-2tb is not the expected XFS disk\n' >&2
    exit 1
}
