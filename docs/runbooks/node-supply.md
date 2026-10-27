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

Applications with seed snapshots (`node_seeds`, currently quartz) are rejected;
pack those by hand.

## Credential

The operation receives the Woodpecker secret `supply_write_token` as
`FORGEJO_PACKAGE_TOKEN`. It is a `JEFF7712` Forgejo token with
`write:package` and `read:repository` scopes: package write for the upload,
and repository read to fetch application sources. Only this operation is given
it, on deployment events of the homelab repository. Agents never hold it.

To provision or rotate it, create the token in Forgejo (Settings, Applications)
and store it as a homelab repository secret named `supply_write_token`, limited
to the protected deployment workflow, with the same event and image filters as
`registry_jarvis_publisher_auth_file`. Then revoke the previous token. The release pipelines
keep using the read-only `supply_read_password`.

The shared node toolchain (`node-toolchain/*`) is still uploaded by hand with
`node_supply.py upload`.
