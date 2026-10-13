# Bookshelf (Readarr) sovereign supply

This directory tracks how to rebuild the Bookshelf container from retained
inputs. The application source lives in `JEFF7712/bookshelf` (develop);
everything needed to build it offline is retained in Forgejo generic
packages and verified by sha256 manifests:

- `nuget-manifest.json` — 301 NuGet packages (`JEFF7712/bookshelf-nuget` v1)
  scripts/fetch-nuget.sh populates `nuget-feed/`
- `yarn-manifest.json` — 961 yarn tarballs (`JEFF7712/bookshelf-yarn` v1)
  scripts/fetch-yarn.sh populates `yarn-mirror/`
- .NET SDK 6.0.428 (`JEFF7712/dotnet-sdk-6`), published as the toolchain
  image `registry.rupan.dev/apps/dotnet-sdk-6:0.0.1000004`
- .NET 6.0.36 shared framework (from the retained runtime packages)
- libsqlite3 3.40.1 (`JEFF7712/bookshelf-native`)

Build outline (all steps run without public network access):

1. `yarn install --offline` + `yarn run build --env production` (Node 22)
   produces the `UI/` frontend tree.
2. `dotnet publish` the Console, Update, and Mono projects for
   `linux-x64` with `/p:RestoreConfigFile` pointing at the local feed
   (see the feed config pattern: a single local source so package source
   mapping cannot reject retained inputs).
3. Assemble the runtime image from the publish outputs, the shared
   framework, and libsqlite3 via the sovereign container release lane.

Known build constraints (do not regress):

- Restore must run with the upstream `packages.lock.json` files REMOVED on
  the scratch tree; with lock files present, restore misresolves floating
  transitive versions against a local folder feed (NU1102 phantoms for
  packages that are present).
- `TreatWarningsAsErrors` is on upstream: the feed must contain EXACT
  minimum versions (no NU1603 approximations).
- The runtime image needs `DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1`
  (slim base has no ICU) and a writable `HOME` (uid 1000).
- `Readarr.Mono.dll` must be in the publish output (loaded at startup
  on Linux).

## Server-owned release lane

The `dotnet` release kind downloads digest-pinned build inputs from Forgejo,
compiles the frontend and three .NET projects without registry lookups, and
assembles the runtime through the shared OCI publisher. The assemble step
has no publishing or package credentials. Build inputs stay outside the
runtime layers; `/app/.sovereign-build.json` records their hashes and the
source commit. The SDK is pinned in `config/ci/applications.json`.

The compiler uses assembly version `10.0.0.0` to replace the upstream
wildcard during deterministic compilation. Package lock files are removed
only from its disposable source copy. Production state is never mounted
into the builder. `Readarr.dll`, `Readarr.Mono.dll`, `Readarr.Update.dll`, and
the UI are required outputs before publication.

A clean container build of Forgejo source
`98a5a1d23ad2d0ce3bc291afea1624d27934787b` passed on 2026-10-12 with
`--network=none`, empty HOME and NuGet caches, and only verified retained
inputs mounted. The resulting runtime started as uid 1000 on the pinned
Python runtime base and returned HTTP 200 with networking disabled. This
build proof does not establish a deployed CI release or state recovery.

The server-owned compiler supplies the verified source commit explicitly and
disables SourceLink repository probing. The retained SourceLink 1.1.1 Git
reader cannot parse Woodpecker's repository-format-1 parent clone. Source
provenance remains in the image labels and retained build receipt.
