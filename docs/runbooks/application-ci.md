# Application CI

Application CI uses the existing exclusive Woodpecker policy extension. The
server owns both repository admission and validation commands. Homelab's
infrastructure workflows remain a separate policy branch.

## Catalog and permissions

`config/ci/applications.json` records application identity, default branch,
validation contract/image, artifact namespace, deployment ownership and state.
The current contract supports stateless sites on main with `static-site-v1`.
Validation images must be digest-pinned references to the local upstream mirror.

Darkbit is enrolled as Woodpecker repository 2, mapped to Forgejo repository 3.
A null `woodpecker_repository_id` means deny all execution. After authorized
Forgejo migration and Woodpecker activation, record the actual Woodpecker
repository ID in a protected homelab change. IDs
are verified with exact owner/name and cannot reuse homelab's ID. A service
restart loads a newly deployed catalog; repository YAML cannot modify it.

Application validation uses only the sandbox agent and receives no configured
publisher, signing, promotion, infrastructure or deployment secrets. It runs an
immutable server-owned Python validator with isolated interpreter startup,
without executing application scripts or installing dependencies. Checkout still
uses the server's existing read-only clone identity. Verify that identity has
only the intended repository read access before live enrollment.

Push, PR and manual events validate. PR metadata/closure events have a separate
lifecycle workflow. Tags, releases, cron and deployment events are rejected.
Application variable overrides and operation requests are rejected before
homelab API lookups. Repository-selected workflow commands are ignored.

Forgejo requires authenticated repository access. Create pilot repositories
private, and repair Woodpecker metadata after visibility changes. The pinned
Forgejo adapter supplies both visibility and SCM privacy so repair can update
the clone authentication decision.

The clone account has read access to homelab and Darkbit, with no write or admin
permission. Its current token has only `read:repository` scope. The previous
token was limited to homelab alone and remains available to existing consumers.
The encrypted replacement is in `secrets/woodpecker-clone.sops.env`. Decrypt it
with an authorized independently held age key and restore it as root-owned
mode 0600 `/persist/woodpecker/clone.env`, then restart the server. Include this
file in recovery assets; a live server environment alone is insufficient.

Register the `node` read-only Zot credential as Darkbit's Woodpecker registry
pull credential. It supplies the fixed validation image, not publication
authority. Darkbit has no Woodpecker command-step secrets.

All agent tiers use `Restart=always` because disconnect exhaustion exits with
status zero. Explicit operator stops still suppress restarts during recovery.
Activation on homelab-04 was verified on 2026-10-08 after draining running
workflows. A clean SIGTERM exit of the idle deploy agent restarted automatically
after the configured 15-second delay with a new PID. All tiers were active and
scheduling was resumed afterward.

## Validation and limits

The validator requires index.html and style.css and checks referenced rendering
files, including nested HTML/CSS, for missing files, symlinks, root escapes and
external asset URLs. Ordinary outbound navigation links are allowed. CSS
escapes, srcset and HTML base URLs fail closed. This is a narrow static-site
contract, not a JavaScript network sandbox or proof of browser behavior.

The generated command embeds the validator from the deployed immutable Nix
source. It uses a pinned local Python image and does not invoke Nix or fetch
validation tooling. The policy service fails startup on malformed catalogs.

Run the application and homelab policy tests in the pinned environment:

```sh
nix develop ./flake -c python -m unittest tests.test_application_ci tests.test_forgejo_ci
```

Branch protection must require `ci/woodpecker/application-validation` after
live webhook delivery is verified. Existing homelab protection continues to
require validation-v2. Do not change protection until the matching workflow
actually reports status.

## Implementation boundary

PRs, forks, manual runs and feature pushes validate source only. A main push also
runs `application-release` after validation. The server-owned static image
contract does not execute the repository Dockerfile, Python modules, package
scripts or build commands. It needs no host socket, privileged container or
general Dockerfile executor.

Input preparation reads the digest-pinned local unprivileged nginx image using
`registry_read_password`, the node read-only identity. Assembly receives no
secret and adds only index.html, style.css and regular files under assets/ to
the pinned base. Symlinks, missing/corrupt OCI blobs and oversized inputs fail
closed. Timestamps and archive ownership are fixed for deterministic output.

Publication receives only `darkbit_registry_password` for `forgejo-darkbit`.
It rechecks the complete base chain, config, source identity and regenerated
asset layer before any writes. It cannot execute repository code. Zot grants
this identity read/create/update on apps/darkbit only, with no delete or
cross-project permission. The legacy publisher remains separate for cutover.
These credentials are push-scoped to the application repository. The immutable
policy fixes all step images and commands; command-step secrets have empty
image filters, as required by the runner.
Recovery material is encrypted in `secrets/darkbit-release.sops.env`.

The registry client connects directly to 10.0.30.20 while verifying TLS for
registry.rupan.dev. Redirects cannot move release traffic to Cloudflare or an
external artifact service. Release tags reserve `0.0.1000001` through
`0.0.1999999` for Woodpecker pipeline numbers 1 through 999999, above the
legacy GitHub range. A conflicting existing tag is rejected. CI never writes
latest or deploys the cluster; a verified digest still needs a protected homelab
promotion PR and Flux reconciliation. Release signing and SBOM enforcement
remain separate unimplemented interfaces.

Provision the local publisher through the manual protected
`provision-darkbit-publisher` homelab CI operation. It replaces only the Darkbit
entry with its lock-derived policy and refuses any unrelated policy change, preserves
all existing htpasswd entries, checks for concurrent changes and retains
root-only rollback files before restarting Zot. Do not replace an existing
local publisher implicitly.

The source cutover passed live acceptance on 2026-10-08. Darkbit source PR 1
passed authenticated checkout and application validation and merged under
main protection. Test PR 2 proved repository-selected privileged commands were
ignored, a missing asset failed validation and prevented merging, and restoring
the asset returned validation to green. The test PR was closed without merging.
Unsigned policy HTTP requests were rejected with 403.

Local delivery passed on 2026-10-08. Darkbit pipeline 25 published
`0.0.1000025` from Forgejo commit
`06a94923008597dd32dcaa61d18950d8b3f23555`. Independent assembly matched
`sha256:9387340adce2259a4918a7ab5c243e64f557d99f51fc27f2c340a4a56d960623`.
Protected homelab PR 112 promoted the image and Flux deployed a healthy
replacement pod with that exact image identity. Pod assets matched source
bytes. Public HTTPS matched CSS and image bytes; HTML matched after reversing
Cloudflare's email-obfuscation rewrite. The GitHub publishing workflow and its
repository publishing secrets were retired after delivery acceptance.

The independent laptop checkpoint at
`/home/rupan/sovereign-recovery/darkbit/2026-10-08` contains verified source
bundles, encrypted clone and publisher credentials, pinned Python and nginx
OCI inputs, release artifacts and checksum manifests. Credential decryption
was verified with the workstation key, independent of the NAS. This is a
local checkpoint, not a tested off-host platform restore.

## Checkout toolchain

The server's default clone plugin is pinned to
`registry.rupan.dev/upstream/docker.io/woodpeckerci/plugin-git@sha256:0f06b03ec33137b556c77538563f66c400339052d0c5199d4c3ad1a2a37e1964`.
This is the same 2.10.0 upstream index previously observed in the live Docker
cache, verified against the upstream manifest. The explicit build-input
inventory includes it so checkout tooling cannot depend on an untracked
external tag.

Import and verify the reviewed lock through the protected
`registry-lock-import` operation before activating the clone pin. The implicit
clone step uses the server-selected plugin and read-only source identity;
repository pipeline files do not select it. Verify a new checkout with the
local pinned image after activation, without treating an existing cached image
as evidence of local retention.

Credential-free static assembly and runtime have passed tests with networking
disabled from retained inputs. Clean complete offline CI and independent
platform recovery remain separate gates under the
[lifecycle RFC](../research/2026-10-08-sovereign-application-lifecycle-rfc.md).
Homelab validation still needs retained Nix flake sources and complete check
closures; public ingress still uses Cloudflare. Signing and SBOM enforcement
also remain open.
