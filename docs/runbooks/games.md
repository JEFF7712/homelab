# Game downloads and library

Owner: `gitops/media/games.yaml`. Images: `gitops/media/registry-sources.yaml`
and `registry/images.lock.json`. Tunnel rules: `gitops/cloudflare/ingress-config.yaml`.

```
questarr.rupan.dev -> Questarr -> Prowlarr (indexers) -> qBittorrent (AirVPN)
  -> /tank/media/downloads/games -> Questarr import (unpack, hardlink)
  -> /tank/media/games/pc -> Gameyfin (games.rupan.dev) and \\nas-01\games
                         -> RomM (romm.rupan.dev), console folders only
```

- **Questarr** (`questarr.media:80`, port 5000): pick games with IGDB metadata,
  search the Prowlarr indexers and send releases to qBittorrent. It mounts
  `media-library` at `/data`, the same path qBittorrent uses, so no remote path
  mapping is needed. State: `questarr-data` (SQLite, generated JWT and
  credential-encryption keys).
- **Gameyfin** (`gameyfin.media:80`, port 8080; actuator on 8081): web library
  over `/tank/media/games`, mounted read-only at `/library`. Users log in and
  download games from the browser. State: `gameyfin-data` (H2 database,
  backups, plugins, logs). `APP_KEY` is the in-cluster generated
  `gameyfin-app-key` Secret.

`questarr.rupan.dev` sits behind the wildcard Cloudflare Access app (owner
login). `games.rupan.dev` has its own Access app `games`, declared in `tofu/cloudflare-access/`: an
Allow policy on named emails with one-time PIN login, so invited users get an
emailed code and then log in to Gameyfin. Never give it a Bypass policy.

To invite someone: inspect the `games` application in
`tofu/cloudflare-access/applications.tf.json`. It currently attaches the reusable
`friends` policy in `policies.tf.json`, so adding an email there affects every
application using that policy. Edit the owning declaration with the intended
scope and ship through [Access management](cloudflare-access.md), including its
authorization-change approval gate. Then create the Gameyfin account under
Administration > Users. Do not make an untracked dashboard policy edit. Large game downloads through Cloudflare Tunnel are subject to
Cloudflare's terms on serving large files.

## First-run setup

Settings live in each app's database, not in Git.

1. **IGDB credentials** (both apps): register an application at
   <https://dev.twitch.tv/console> and note the Client ID and Client Secret.
2. **Questarr** at `https://questarr.rupan.dev`:
   1. Create the admin account and enter the IGDB credentials.
   2. Indexers > Sync from Prowlarr: URL `http://prowlarr.media`, API key from
      Prowlarr Settings > General. Select **Allow insecure LAN connection**
      (in-cluster HTTP).
   3. Downloaders > qBittorrent: host `qbittorrent.media`, port `80`, no SSL,
      the qBittorrent WebUI credentials, **Allow insecure LAN** on. Use
      category `games` with download path `/data/downloads/games`.
   4. Settings > Import: enable **Post-Processing**, Library Root
      `/data/games/pc`, Transfer Mode **Hardlink** (keeps seeding; both paths
      are on one NFS export), **Auto-Unpack Archives** on. No path mapping is
      needed because Questarr and qBittorrent share the `/data` mount.
   5. Settings > Import > Security: enable VirusTotal (API key from
      virustotal.com) only; leave ClamAV disabled (the scanner has been retired).
      Questarr v1.5.0's VirusTotal **Test** always fails with
      `VirusTotal responded with 400` because of a truncated test hash
      (Doezer/Questarr#1256); real scans are unaffected.
3. **Gameyfin** at `https://games.rupan.dev`:
   1. Complete the setup wizard (admin account).
   2. Enable the IGDB metadata plugin with the same Twitch credentials.
   3. Add a library with path `/library/pc`.
   4. Create accounts for other users under Administration > Users.

## Operations

```sh
kubectl -n media get deploy questarr gameyfin
kubectl -n media logs deploy/questarr
kubectl -n media logs deploy/gameyfin
```

- Never rotate `gameyfin-app-key`: a new key cannot decrypt stored settings or
  restore backups taken with the old one. Deleting the ExternalSecret's target
  Secret regenerates a different key.
- Questarr runs as 911:1001 (plus group `users`), the effective user of
  qBittorrent and the *arrs, so hardlink imports work under
  `fs.protected_hardlinks`. Its upstream entrypoint is bypassed (that
  script needs a writable root filesystem). Its logger always writes
  `/app/server.log`; that single file is mounted from a 256Mi emptyDir, so the
  log resets on each pod restart and the pod is evicted if it outgrows the
  limit. Gameyfin's entrypoint starts as
  root and drops to uid 1337 with gosu.
- `/data/downloads/games` and `/data/games/pc` are mode 0777, set by the
  qBittorrent init container, like the other download folders.
- ClamAV is retired. Its `clamav-db` PVC is retained for recovery; no scanner
  runs. Disable ClamAV in Questarr Import > Security before importing games.
- Upgrades: bump the digest in `registry-sources.yaml`, import it through the
  registry lane (`docs/runbooks/local-registry.md`), then update the digest in
  `games.yaml`.

## RomM integration

Questarr 1.6.0 uses its existing import root `/data/games/pc` for all platforms
(the `pc` component is historical). Settings > RomM enables platform folders,
post-import scans and six-hour ownership sync. Console imports now use RomM
slugs such as `nes`, `snes`, `gba`, and `psx`; select the game's target platform
before searching so a multi-platform title is imported for the right console.
New Windows imports use `win`, while existing `PC` and `Linux` directories stay
in place. Gameyfin continues scanning the same parent and serves both layouts.

RomM mounts the existing `/tank/media` NFS export with `subPath: games/pc`
at `/romm/library/roms`. Use the same export root as Questarr: mounting
`/tank/media/games/pc` directly returned stale root attributes and omitted newly
created console directories from scans, even while direct file access worked.
`gitops/romm/library-config.yml` excludes PC folders and maps the legacy
`Switch` directory. Its Git-managed config is read-only in the UI; edit the
source for scanning exclusions. The init container copies existing PICO-8
cartridges into the shared library with byte verification, leaving the retained
state copy intact. Restoring the previous app manifest reveals the original
library again. Saves, database, BIOS and resources retain their existing paths
and backups; ROM files use the existing media dataset snapshots.

Questarr connects to `http://romm.romm.svc.cluster.local` with a dedicated RomM
client token (`platforms.read`, `roms.read`, `tasks.run`) encrypted in Questarr's
database. RomM admits only Questarr pods from the media namespace to port 8080.
The existing downloader/indexer credentials, VPN and hardlink transfer mode are
unchanged. The shared root is setgid group 1000 and Questarr starts with umask
0002 so newly created platform directories allow RomM uploads too. Hardlinked
payloads retain their original mode; RomM can read and play those files.

To check: use Settings > RomM > Test Connection, then Sync; a completed import
should log `Asked RomM to rescan its library after an import`. Check the RomM
scan task and confirm the target game appears. PC games should remain absent
from RomM. Availability depends on indexers and selected platform; browser
emulation supports only some systems. A pending or failed download is not an
import verification. Disable RomM integration to stop scan/sync calls; changing
folder naming back affects future imports and never moves existing files.
