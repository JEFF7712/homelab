# Media download-chain credentials

The qBittorrent WebUI password and the Sonarr/Radarr/Lidarr/Seerr API keys
live in app databases on PVCs, not in Git. `arr-credential-sync`
(`gitops/media/arr-secret-sync.yaml`, every 15 min) enforces them from the
`media-app` Secret, which ExternalSecrets fills from GitLab project
variables (see `gitops/media/secrets.yaml`).

## What is synced

| GitLab variable | Pushed to |
|---|---|
| `QBITTORRENT_USERNAME` / `QBITTORRENT_PASSWORD` | Radarr, Sonarr, Lidarr qBittorrent download clients |
| `SONARR_API_KEY`, `RADARR_API_KEY` | Seerr server settings |
| `JELLYFIN_SEERR_API_KEY` | Seerr Jellyfin settings |
| `LIDARR_API_KEY`, `SEERR_API_KEY` | Used by the sync job for API auth |

The job is test-first: it runs each download client's own connection test
and only rewrites the password when the test fails. Any failure exits
nonzero, so a failing CronJob means credentials drifted somewhere the job
cannot write (see below).

## Rotate the qBittorrent password

The password itself is NOT set by automation (changing it requires a
qBittorrent restart behind the VPN tunnel, so it stays a manual step):

1. qBittorrent WebUI (torrent.rupan.dev) -> Tools -> Options -> Web UI ->
   change password, Apply.
2. Update the `QBITTORRENT_PASSWORD` GitLab project variable (same value).
3. Within 15 minutes the sync job pushes it to Radarr/Sonarr/Lidarr.
   Verify: `kubectl -n media logs -l job-name --tail=20` shows
   `OK: all credentials in sync`, or check the *arr UIs' download clients.

## Rotate an *arr API key

API keys live in each app's `config.xml` on its config PVC and need an app
restart, so this is also manual:

1. Stop the app (`kubectl scale -n media deploy/<app> --replicas=0`).
2. Edit `/config/config.xml` on the PVC (or via a debug pod), set `<ApiKey>`.
3. Scale back up, update the matching GitLab variable
   (`SONARR_API_KEY`, `RADARR_API_KEY`, `LIDARR_API_KEY`, `SEERR_API_KEY`).
4. The sync job restores Seerr's stored server keys automatically.

## Rotate the Jellyfin key for Seerr

1. Jellyfin dashboard -> Administration -> API Keys -> New (name `seerr`).
2. Update the `JELLYFIN_SEERR_API_KEY` GitLab variable.
3. The sync job applies it to Seerr; the next Jellyfin scan in Seerr's logs
   should complete without `401`s.

## Lidarr extended downloader (Deezer)

Lidarr downloads music via the hotio extended Audio script (deemix +
Deezer ARL), not just indexers. The scripts execute
`/config/extended.conf` as bash, so the `lidarr-extended-config`
ConfigMap must stay in shell `KEY="value"` format; ini section headers
break every assignment and silently exit all extended services at boot
(no downloads, empty queue, indexer searches find nothing).

Secrets never live in that ConfigMap: the `lidarr-boot.sh` wrapper
(Deployment command) renders `/config/extended.conf` at container start
from the `DEEZER_ARL` and `LIDARR_API_KEY` env vars (media-app Secret),
installs the extended toolchain via `setup.bash`, then starts the Audio
and ARLChecker daemons before execing `/init`. A missing var fails
container start loudly.

Rotate the Deezer ARL:

1. Get a fresh ARL (Deezer login cookie) and update the
   `LIDARR_DEEZER_ARL` GitLab project variable.
2. Wait for ExternalSecrets to sync `media-app` (hourly at most), then
   `kubectl -n media rollout restart deploy/lidarr`.
3. Verify: `custom-services.d/python/ARLStatus.txt` in the pod reports a
   valid ARL, an `Arr-Extended` download client appears in Lidarr, and
   the Audio log starts processing the missing list.

## Symptoms of drift (before the job existed, these were all silent)

- Seerr requests stuck in Pending/Requested: check Seerr request status,
  then the *arr queue for the title, then qBittorrent.
- *arr Health: "Unable to communicate with qBittorrent" -> wrong download
  client password.
- Seerr logs `Jellyfin API ... status code 401` every 5 min -> dead
  Jellyfin key.
