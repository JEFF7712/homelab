# Doomsday guide

What to do when the internet is gone for a long time, and how to keep the
offline knowledge archive usable as things break further. Read it now, while
everything works: the last section is preparation that cannot be done later.
Keep a printed or downloaded copy somewhere that does not need this homelab
to read it.

The technical procedure behind this guide is the
[offline library runbook](runbooks/offline-library.md).

## What is in the archive

Everything lives on nas-01, dataset `media2/offline` (the 2 TB Seagate disk),
mounted at `/tank/offline`. About 660 GB once the first full update finishes.

| Area | Contents |
| --- | --- |
| Reference | Wikipedia (English, with images), Wiktionary, Wikibooks, Wikivoyage (travel) |
| Medicine | mdwiki (WikiProject Medicine), LibreTexts Medicine |
| Repair and making | iFixit repair guides, DIY Stack Exchange, Gardening Stack Exchange, Electronics Stack Exchange |
| Science and maths | LibreTexts Chemistry, Physics, Mathematics, Engineering, Biology; Chemistry, Physics and Math Stack Exchange |
| Computing | Stack Overflow, Super User, Ask Ubuntu, Unix & Linux, Server Fault, Arch Wiki; DevDocs for Nix, Python, NumPy, pandas, Matplotlib, PyTorch, Kubernetes, Docker, Bash, Git, C, C++, Rust, Go, JavaScript, TypeScript, PostgreSQL, SQLite, nginx, Ansible, Terraform |
| Books | Project Gutenberg (about 70,000 public-domain books in English) |
| Maps | The whole world's street map from OpenStreetMap (Protomaps build) |

The exact list is `gitops/offline/kiwix-update/books.json`. The map has no
place search and no turn-by-turn directions; plan routes with it by eye, or
use Organic Maps on a phone (see Preparation).

## Scenario 1: the internet is down, the house has power

Nothing to do. Everything works on the home network:

- Library: <http://kiwix.homelab>
- Map: <http://maps.homelab>

Both are plain `http://` on purpose: no certificate can expire and no
outside service is involved. Browse the library's home page or search inside
a book. Bookmarks that use a book's short name, such as
`http://kiwix.homelab/content/wikipedia_en_all_maxi`, keep working.

If the names stop resolving but the network is up, the library also answers
at <http://10.0.40.18>. The map only answers to the name `maps.homelab`; add
`10.0.40.18 maps.homelab` to the device's hosts file to reach it without DNS.

Expect these, and ignore them:

- The Sunday 03:00 update fails while the internet is down and raises a
  `JobFailed` alert. Nothing served is removed: a failed update never takes a
  book or map away. It catches up on its own when the internet returns.
- Anything reached through `*.rupan.dev` from outside the house stops working,
  since that goes through Cloudflare.

## Scenario 2: the cluster is down, the NAS still runs

The web pages come from the k3s cluster, but the files are ordinary files on
the NAS. A laptop on the management network (10.0.10.0/24) can read them
directly; the NAS exports them read-only to that network.

```sh
sudo mkdir -p /mnt/offline
sudo mount -t nfs -o ro 10.0.30.20:/tank/offline /mnt/offline
```

Library: serve every book from the laptop, then open <http://localhost:8080>.

```sh
nix shell nixpkgs#kiwix-tools -c kiwix-serve --port 8080 /mnt/offline/kiwix/zim/*.zim
```

Without Nix, install the Kiwix desktop app (Linux, macOS, Windows) and open
the `.zim` files in `/mnt/offline/kiwix/zim/` with it.

Map: the viewer needs its page next to the map files. Copy the page from this
repository into a folder with links to the files, then serve that folder and
open <http://localhost:8081>.

```sh
mkdir -p ~/offline-map
cp gitops/offline/web/index.html ~/offline-map/
ln -s /mnt/offline/maps/assets ~/offline-map/assets
ln -s "/mnt/offline/maps/$(readlink /mnt/offline/maps/planet.pmtiles)" ~/offline-map/planet.pmtiles
nix shell nixpkgs#caddy -c caddy file-server --listen :8081 --root ~/offline-map
```

The server has to support range requests; Caddy and nginx do, Python's
`http.server` does not.

## Scenario 3: no power, or the NAS is gone

Only a copy made in advance helps now: the portable copy and the phone apps
from Preparation below. With the portable drive:

- Library: open the `.zim` files with the Kiwix desktop app, or run the
  `kiwix-serve` command from Scenario 2 pointed at the drive's `kiwix/zim/`.
- Map: run the Scenario 2 map commands with the drive's `maps/` folder in
  place of `/mnt/offline/maps/`.

The NAS itself keeps three daily ZFS snapshots of the archive, but those live
on the same disk and do not survive losing it.

## Preparation (do this while things work)

1. Portable copy on an external drive of at least 1 TB. From a laptop on the
   management network, with the NAS mounted as in Scenario 2:

   ```sh
   rsync -a --info=progress2 /mnt/offline/kiwix/zim/ /media/<drive>/offline/kiwix/zim/
   rsync -a --copy-links --exclude staging /mnt/offline/maps/ /media/<drive>/offline/maps/
   cp docs/DOOMSDAY.md gitops/offline/web/index.html /media/<drive>/offline/
   ```

   `--copy-links` turns `planet.pmtiles` into a real file, so the drive holds
   two copies of the map unless you delete the dated `planet_*.pmtiles` after
   copying. Rerun the same commands every month or two; rsync only copies
   what changed.
2. Put a Kiwix reader and a map reader on the drive or laptop so nothing has
   to be downloaded later: the Kiwix desktop app installer, or a laptop with
   `kiwix-tools` and `caddy` already in its Nix store
   (`nix profile install nixpkgs#kiwix-tools nixpkgs#caddy`).
3. Phone: install Kiwix and copy a few smaller books onto it (mdwiki, iFixit,
   Wikivoyage, Wikipedia if it fits). Install Organic Maps and download the
   regions you might need; it searches and routes offline, which this map
   cannot.
4. Keep a copy of this guide on the drive, on the phone and on paper.
5. Now and then, with the WAN unplugged, open a book, run a search and pan the
   map, to prove the copy in the house works without the internet.

## Checking it works

- Library: the home page of <http://kiwix.homelab> lists every book.
- Map: <http://maps.homelab> shows streets when zoomed in.
- Latest update: `kubectl -n offline get cronjob,job` and the newest Job's
  `finish` log end with "verified ... 0 problems".
- Where each piece lives and how to change it:
  [offline library runbook](runbooks/offline-library.md) and
  [NFS access](runbooks/nfs-access.md#offline-archive).
