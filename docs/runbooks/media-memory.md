# Media memory management

Subgen and ClamAV are retired. Existing subtitles, media and the ClamAV
signature PVC are preserved. Questarr must have ClamAV scanning disabled.

qBittorrent uses `Session\DiskIOType=SimplePreadPwrite`, enforced by its
init container in `gitops/media/download.yaml`. This avoids mmap retaining
large active file mappings. OS file cache still consumes reclaimable RAM.
The 4Gi limit remains as headroom; reducing requests alone does not save RAM.

Verify the API reports `disk_io_type: 3`, downloads remain healthy, and compare
`kubectl top pods` after sustained activity. A restart alone clears cache,
so an immediate drop does not establish the long-term saving. To recover,
remove the managed key enforcement and select the default disk I/O backend.
