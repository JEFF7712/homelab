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
