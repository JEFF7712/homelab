# Game downloads and library

Owner: `gitops/media/games.yaml`. Images: `gitops/media/registry-sources.yaml`
and `registry/images.lock.json`. Tunnel rules: `gitops/cloudflare/ingress-config.yaml`.

```
questarr.rupan.dev -> Questarr -> Prowlarr (indexers) -> qBittorrent (AirVPN)
  -> /tank/media/downloads/games -> Questarr import (unpack, hardlink)
  -> /tank/media/games/pc -> Gameyfin (games.rupan.dev) and \\nas-01\games
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

Both hostnames sit behind the wildcard Cloudflare Access app (owner login).
Access for other people is not decided yet: either add their email to an
Access policy for `games.rupan.dev`, or reach Gameyfin over NetBird. Large
game downloads through Cloudflare Tunnel are subject to Cloudflare's terms on
serving large files.

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
- Questarr runs as uid 1000 with its upstream entrypoint bypassed (that
  script needs a writable root filesystem). Gameyfin's entrypoint starts as
  root and drops to uid 1337 with gosu.
- Upgrades: bump the digest in `registry-sources.yaml`, import it through the
  registry lane (`docs/runbooks/local-registry.md`), then update the digest in
  `games.yaml`.
