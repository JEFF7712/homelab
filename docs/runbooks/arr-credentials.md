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

## Lidarr Tidal source (tidaler shim)

Tidal downloads run through `tidaler` (a maintained tidal-dl-ng fork),
not the dead tidal-dl 2022 client the upstream scripts expect.
`lidarr-boot.sh` installs pinned `tidaler==0.1.8` at every boot and
shadows `tidal-dl` with `/usr/local/bin/tidal-dl`, which translates the
legacy flags (`-q/-o/-l`) and quality tiers. The upstream Audio script
is untouched. Trust note: tidaler is a small community fork that holds
a Tidal OAuth token, so treat releases with care and keep the pin.

Auth lives outside Git at `/config/xdg/tidaler/token.json` on the
lidarr-config PVC (survives restarts). When provisioned with a
`refresh_token`, `tidaler` automatically refreshes its access token in
the background. If provisioned without a refresh token (or if revoked),
the session expires after 24 hours. Symptoms of expiry: the Audio log shows
`tidal-dl-shim: TIDAL session invalid` and the Tidal client test fails,
which exits the Audio daemon until the next pod restart.

Authenticate via interactive OAuth (Recommended, auto-refreshes indefinitely):

1. Run interactive login inside the pod:
   `kubectl exec -it -n media deploy/lidarr -c lidarr -- tidaler login`
2. Open the printed `https://login.tidal.com/authorize?...` link in a browser,
   sign in, and copy the full redirect URL from the resulting 'Oops' page
   (`https://tidal.com/android/login/auth?code=...`).
3. Paste the redirect URL back into the terminal prompt. `tidaler` exchanges
   the code for an access token and persistent refresh token, automatically
   saving to `/config/xdg/tidaler/token.json`.
4. Ensure the legacy symlink exists:
   `kubectl exec -n media deploy/lidarr -c lidarr -- ln -sf /config/xdg/tidaler/token.json /config/xdg/.tidal-dl.token.json`

Fallback: browser-session token (Expires strictly in 24 hours):

1. Log into `listen.tidal.com`, copy the `Authorization: Bearer`
   token from any `api.tidal.com` request in devtools.
2. Write `/config/xdg/tidaler/token.json` on the PVC as
   `{"token_type":"Bearer","access_token":"...","refresh_token":null,
   "expiry_time":<epoch>}` (see `tidaler/model/cfg.py` Token).
3. `kubectl -n media rollout restart deploy/lidarr` and confirm the
   Tidal client test succeeds in the Audio log.

Revert to Deezer-only: set `dlClientSource` back to `deezer` in the
`lidarr-extended-config` ConfigMap; the shim stays installed but idle.

## Soularr (Lidarr wanted -> Soulseek via slskd)

Soularr (`soularr` CronJob, hourly) grabs up to 60 wanted albums per
run from Lidarr, downloads them through slskd, and tells Lidarr to
import from `/data/downloads/soulseek`. Config renders at job start
from `LIDARR_API_KEY` / `SLSKD_API_KEY` (media-app Secret); sources are
pinned to upstream commit `0700090e`.

`SLSKD_API_KEY` is also the slskd primary API key (plain string =
Administrator role, required for search/download/delete). Adding it for
the first time needs a `download` pod restart so slskd picks up the env:

1. Create the `SLSKD_API_KEY` GitLab variable (48+ chars), wait for the
   ExternalSecret sync, then delete the `download` pod (restarts
   qBittorrent too, briefly).
2. Verify: trigger a manual run
   (`kubectl -n media create job --from=cronjob/soularr soularr-manual`)
   and check its logs for `Soularr finished` plus slskd
   `/api/v0/transfers/downloads` going non-empty.

Tune grabs per run via `number_of_albums_to_grab` in the
`config.ini.template` (`gitops/media/soularr.yaml`).

Keep `SLSKD_SHARE_CACHE_WORKERS` pinned to eight. Soulseek includes this count
in its saved share-cache options; using a host-dependent default invalidates
the cache when the pod moves to a node with a different CPU count. The ensuing
startup scan keeps the shared download pod unready, including qBittorrent.

## Symptoms of drift (before the job existed, these were all silent)

- Seerr requests stuck in Pending/Requested: check Seerr request status,
  then the *arr queue for the title, then qBittorrent.
- *arr Health: "Unable to communicate with qBittorrent" -> wrong download
  client password.
- Seerr logs `Jellyfin API ... status code 401` every 5 min -> dead
  Jellyfin key.
