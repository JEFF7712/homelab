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

This lane validates source only. It grants no build, publication, signing,
promotion or cluster execution authority. Those stages need an isolated build
executor and verified artifact handoff before credentials can be introduced.
The catalog's artifact/deployment paths record ownership, not execution grants.

Next acceptance steps are authorized repository migration/enrollment, activation
of the reviewed policy, signed-extension/webhook verification, and legitimate
validation. Then add build/publication and retained input manifests under the
[lifecycle RFC](../research/2026-10-08-sovereign-application-lifecycle-rfc.md).
Offline build and independent recovery remain separate unpassed gates.
