# Node supply

Node application releases install dependencies offline from a retained npm
cache snapshot, `<app>-supply/<version>/npm-cache.tar.gz` in Forgejo generic
packages, pinned by sha256 in `config/ci/applications.json`. A release whose
lockfile needs a package that is missing from the snapshot fails with
`ENOTCACHED` and `Offline dependency install failed`. Application PR checks do
not run this install, so the failure first appears in the release pipeline.

## Refreshing a snapshot

When an application's lockfile changes, add a request to
`config/ci/node-supply-requests.json` and ship it:

```json
{
  "schema_version": 1,
  "requests": [
    {
      "application": "rupan-dev",
      "version": "4",
      "source": "<full 40-character application commit>"
    }
  ]
}
```

`version` must be greater than the pinned version. `source` is the application
commit whose lockfile the snapshot must cover, normally the release that
failed. The request file routes to the protected `node-supply-publish`
operation, so `just ship` runs it after merge. The operation:

1. Downloads the pinned glibc toolchain and checks its digest. It then requires
   the flake's `nodejs_24` to be the same node release, because the toolchain
   itself needs an FHS loader that the Nix CI image lacks.
2. Fetches the application at `source` and runs `node_supply.py pack`. Pack
   installs online, adds the musl optional natives (`npm ci --libc=musl`), and
   then proves an offline reinstall for both libcs and an offline build.
3. Uploads the snapshot and reads it back to verify the digest.
4. Prints a promotion patch that pins the new version and digest and empties
   the request. `just ship` ships that patch as its own PR. The patch changes
   `applications.json`, which routes `deploy-ci-host` so the release policy
   loads the new pin.

Re-run the failed application release after the promotion deploys.

Snapshots are not reproducible: npm's cache index records fetch times and
response headers. That is why CI emits the pin after it packs, rather than
checking a digest that was written beforehand. If an attempt uploaded the file
but its pin never merged, the next run finds the retained version and pins it
without repacking. Generic package files are immutable, so a bad upload is
fixed with a new version number, never by replacing the file.

Applications with seed snapshots (`node_seeds`, including Quartz) reuse their
retained archives. The operation verifies every seed against its catalog digest
and restores it at the same workspace destination used by the release build
before proving the offline install and build.

## Credential

The operation receives the Woodpecker secret `supply_write_token` as
`FORGEJO_PACKAGE_TOKEN`. It is a `JEFF7712` Forgejo token with
`write:package` and `read:repository` scopes: package write for the upload,
and repository read to fetch application sources. Only this operation is given
it, on deployment events of the homelab repository. Agents never hold it.

To provision or rotate it, sign in to Forgejo as `JEFF7712` and create the
token under Settings, Applications, with access to all repositories, package
Read and write, and repository Read. Store it as the homelab repository secret
`supply_write_token` with only the Deployment event and no image filter,
matching `supply_read_password`. Then revoke the previous token. The release
pipelines keep using the read-only `supply_read_password`.

The shared node toolchain (`node-toolchain/*`) is still uploaded by hand with
`node_supply.py upload`.

## Upgrading the Node major

Node application releases run on Node 24, which is supported until
2028-04-30; move to the next LTS major before then. A release image is
assembled from the `node-toolchain` supply and the catalog pins in
`config/ci/applications.json`, not from the producer Dockerfiles, so a
base-image bump alone changes nothing and fails the registry gate. Renovate
therefore does not propose Node majors for
`gitops/producer-base-images/base-images.yaml`. The 2026-11 attempt (#602,
#670) stopped at step 2: the recipe for `node-musl.tar.gz` and
`node-libs.tar.gz` is not recorded, so write it down with the next upload.

1. Mirror the new `node:<major>-slim` and `node:<major>-alpine` images next to
   the current ones: append them to `base-images.yaml`, and merge only their
   two new records into `registry/images.inventory.json` and
   `registry/images.lock.json` (a full regeneration renames unrelated IDs).
   Confirm `registry-lock-import` copied both.
2. Operator: build `node-toolchain/<n+1>` (`node.tar.gz`, `node-musl.tar.gz`,
   `node-libs.tar.gz`) from the Node release that the pinned nixpkgs ships as
   `nodejs_<major>`, and upload it with `node_supply.py upload`. Agents hold
   no package write token.
3. Point `nix_toolchain` in `scripts/ci/node_supply.py` at `nodejs_<major>`,
   then refresh every Node application's npm cache through
   `config/ci/node-supply-requests.json` (see above).
4. Pin the new toolchain in `applications.json` and move the
   `release_base_image` entries that use the Node runtime to the new alpine
   mirror. This deploys the CI host.
5. Update each producer Dockerfile (rupan.dev, rupanism, distrojeff-site,
   obsidian-vault `quartz/Dockerfile`), release and promote each application.
6. Move the `config/ci/projectctl-contracts.json` images, then remove the old
   major's entries from `base-images.yaml`, the inventory and the lock.
