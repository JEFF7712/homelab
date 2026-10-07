# Cloudflare Tunnel `homelab`

Remote-configured tunnel fronting the new k3s cluster. The rule list is owned by
`tofu/cloudflare/` and applied by CI; see "Adding a hostname" before editing
anything in the Zero Trust dashboard, because dashboard edits do not survive the
next apply.

## Identity

- Name: `homelab`
- ID: `0f08d8c5-6f2c-409e-ba80-dc0601e0227e`
- Config source: Cloudflare remote configuration (`config_src=cloudflare`; checked
  2026-09-23), but the desired state is git: `tofu/cloudflare/main.tf` decodes
  `gitops/cloudflare/ingress-config.yaml` and pushes it. The dashboard is a
  read-back of that, not an input.
- Connectors: 2 replicas from `gitops/cloudflare/tunnel.yaml` (`cloudflared 2026.8.3`)
- Health: `healthy`, 8 connections on `ord10, mci03, ord15, ord06, mci01, ord02`
- Public origin IP seen by edge: `50.93.213.22` (connector pods live in `10.0.30.0/24`)
- Apply job: `cloudflare_apply` (manual, `production` environment, `resource_group: cloudflare-tunnel`)
- Stage order: `cloudflare` runs before `registry`, deliberately. The `registry`
  stage holds four blocking manual jobs (`registry_retention_reconcile`,
  `registry_lock_import`, and the two `provision_*_publisher` jobs), and a
  blocking manual job holds every later stage, so a cloudflare job placed after
  it would never start on a busy `main`. Nothing in this stack depends on
  registry work.

## Traffic path for `photos.rupan.dev`

Internet -> Cloudflare edge -> `cloudflared` pods -> `http://immich-server.immich:80` (in-cluster Service DNS) -> `Service immich-server:80 -> 2283`.

Do not point tunnel origins at a Cilium LB VIP (`10.0.40.x`): Cilium implements it as a local redirect that only answers in host network namespaces, so `cloudflared` (pod netns) blackholes dialing it. Use in-cluster Service DNS names, consistent with the other new-cluster entries. The Gateway API layer was removed (tunnel is the public edge; NetBird covers remote LAN access), so the tunnel is the only path, not a bypass.

Related manifests:

- `gitops/cloudflare/tunnel.yaml`
- `gitops/immich/server.yaml`

## Ingress order (v82 applied 2026-09-29; `sovereign.rupan.dev` staged ahead of apply)

Cloudflare evaluates top to bottom, first match wins. Keep specifics first, catch-all last.

Cutover completed: `rupan.dev` and `www.rupan.dev` route to `http://rupan-dev-svc.rupan-dev:80`
via the homelab tunnel, backed by local image `registry.rupan.dev/apps/rupan-dev`. DNS apex and
`www` point to the tunnel CNAME (`0f08d8c5-6f2c-409e-ba80-dc0601e0227e.cfargotunnel.com`) with
Cloudflare Access public bypass configured.

1. `photos.rupan.dev -> http://immich-server.immich:80`
2. `www.pulseagent.dev -> http://pulse-svc.pulse:80`
3. `ism.rupan.dev -> http://ism-svc.ism:80`
4. `nix-agent.rupan.dev -> http://nixagent-svc.nixagent:80`
5. `rupanism.rupan.dev -> http://rupanism-svc.rupanism:80`
6. `spatia.rupan.dev -> http://spatia-svc.spatia:80`
7. `sovereign.rupan.dev -> http://sovereign-inquiry-svc.sovereign:80` (path `^/api/inquiries/?$`, pilot inquiry receiver)
8. `sovereign.rupan.dev -> http://sovereign-svc.sovereign:80` (private concept site, staged; Access app `sovereign`, see below)
9. `demo.rupan.dev -> http://cr-demo-svc.cr-demo:80`
10. `soluble.rupan.dev -> http://soluble-rupan-svc.soluble-rupan:80`
11. `photo.rupan.dev -> http://photography-svc.photography:80`
12. `majorfinder.rupan.dev -> http://majorfinder-svc.majorfinder:80`
13. `notes.rupan.dev -> http://quartz-notes.obsidian.svc.cluster.local:80`
14. `ntfy.rupan.dev -> http://ntfy-ntfy.observability.svc.cluster.local:80`
15. `obsidian.rupan.dev -> http://couchdb.obsidian.svc.cluster.local:5984`
16. `renovate-status.rupan.dev -> http://renovate-dashboard.automation.svc.cluster.local:80`
17. `renovate-approve.rupan.dev -> http://renovate-approval-webhook.automation.svc.cluster.local:80`
18. `ha.rupan.dev -> http://home-assistant.home-assistant:8123`
19. `ma.rupan.dev -> http://music-assistant.music-assistant:8095`
20. `ledfx.rupan.dev -> http://ledfx.music-assistant:8888`
21. `rupan.dev -> http://rupan-dev-svc.rupan-dev:80`
22. `www.rupan.dev -> http://rupan-dev-svc.rupan-dev:80`
23. `grafana.rupan.dev -> http://kube-prometheus-stack-grafana.observability.svc.cluster.local:80`
24. `distrojeff.com -> http://distrojeff-site-svc.distrojeff:80`
25. `apollinestore.com -> http://apolline-svc.apolline:80`
26. `darkbitapparel.com -> http://darkbit-svc.darkbit:80`
27. `pulseagent.dev -> http://pulse-svc.pulse:80`
28. `flux-wh-33b0c8004348.rupan.dev -> http://webhook-receiver.flux-system:80` (Flux GitLab push receiver, 2026-09-20; Access app `flux-webhook-bypass` reused Bypass policy, wildcard `*` app would otherwise force login)
29. `jellyfin.rupan.dev -> http://jellyfin.media:80`
30. `navidrome.rupan.dev -> http://navidrome.media:80`
31. `music.rupan.dev -> http://navidrome.media:80`
32. `seerr.rupan.dev -> http://seerr.media:80`
33. `requests.rupan.dev -> http://seerr.media:80`
34. `lidarr.rupan.dev -> http://lidarr.media:80`
35. `radarr.rupan.dev -> http://radarr.media:80`
36. `sonarr.rupan.dev -> http://sonarr.media:80`
37. `prowlarr.rupan.dev -> http://prowlarr.media:80`
38. `bazarr.rupan.dev -> http://bazarr.media:80`
39. `slskd.rupan.dev -> http://slskd.media:80`
40. `torrent.rupan.dev -> http://qbittorrent.media:80`
41. `qbittorrent.rupan.dev -> http://qbittorrent.media:80`
42. `books.rupan.dev -> http://calibre-web.media:80`
43. `media.rupan.dev -> http://filebrowser.media:80`
44. `bookshelf.rupan.dev -> http://bookshelf.media:80`
45. `pod.rupan.dev -> http://pod-agent-dashboard.pod-agent:80` (owner desk, added 2026-09-29; requires the Access app `pod-agent-dashboard`, see below)
46. `git.rupan.dev -> http://forgejo.cloudflare:3000`
47. `http_status:404`

Removed 2026-09-15 (v72):
- `*.rupan.dev -> https://10.0.20.180:443` (defunct Talos Traefik VIP; caused grafana outage, then 404s for unmatched hosts after grafana fix)
- `sandhufiles.site -> https://10.0.20.180:443` (defunct site)

Removed 2026-09-22 (dead origins; no such Services in-cluster, verified via `kubectl -n cloudflare get svc`):
- `glance.rupan.dev -> http://glance:8080`
- `pihole.rupan.dev -> http://pihole:80`
- `api.rupan.dev -> http://rupan-api:9000`
- `homelab.rupan.dev -> http://homelab-api:9200`

## Adding a hostname

The rule list lives in exactly one place, `gitops/cloudflare/ingress-config.yaml`.
`tofu/cloudflare/main.tf` decodes it with `yamldecode` and pushes it, so there is
no second copy to keep in parity. The ConfigMap the connector pods mount is the
same file; Reloader restarting the pods on a change is harmless and does not
affect remote ingress.

1. Add the `{hostname, service}` rule to `gitops/cloudflare/ingress-config.yaml`
   in Cloudflare-evaluated order (specifics first, catch-all last) and to the
   numbered list above in the same position.
2. Run `python -m unittest tests.test_cloudflare_tunnel` — asserts config order
   matches this runbook and every origin Service exists.
3. Commit and push. `cloudflare_plan` runs on `main` and prints the resource
   changes; read them in the job log before continuing.
4. Run `cloudflare_apply` (manual) from the pipeline. It reports the new
   `config_version` as an artifact.
5. Verify the public hostname serves the expected origin.

Do not edit ingress rules in the Zero Trust dashboard. The dashboard is where
the applied state is *read*, and a hand edit there is reverted by the next apply.

DNS `CNAME <host> -> <tunnel-id>.cfargotunnel.com` is still created by hand in
the dashboard, once per hostname. It is not managed by this stack.

## First apply (one-time, before trusting the pipeline)

The stack must be imported, not created. Creating it would 409 against the live
tunnel and fight the connector Deployment. This runs in CI, not locally, because
the state backend is GitLab's HTTP state API and a local run has no job token.

1. Add the GitLab variables (below) and push this stack.
2. Run `cloudflare_import` from the pipeline. It imports
   `cloudflare_zero_trust_tunnel_cloudflared_config.homelab` and then plans with
   `-detailed-exitcode`, which returns 0 for a no-op, 2 for changes, or 1 for
   error. **Anything other than 0 here means the imported state and the mirror
   disagree**; stop and reconcile by hand rather than applying, because this one
   resource is the entire public edge of the cluster.
3. Let the next `cloudflare_plan` run and confirm
   `python -m scripts.cloudflare.plan_summary` prints `no changes: the edge
   already matches git`.
4. `cloudflare_apply` then has nothing to do until a hostname changes.

Until step 2, `cloudflare_plan` reports a `create`, which is the expected
artefact of an unimported singleton and is not a reason to apply.

The one thing to watch in that first plan is `originRequest`. The mirror sets no
origin options, and if the provider fills defaults in for them, the plan will
show a permanent diff that no apply can settle. That is the failure mode this
step exists to catch; if it appears, the fix is in `main.tf`'s `ingress` local,
not in the dashboard.

GitLab variables, both protected and environment-scoped to `*`, added as a pair:
`CLOUDFLARE_API_TOKEN` (secret, masked) and `CLOUDFLARE_ACCOUNT_ID`. The
`cloudflare` jobs read them by those names; `tofu` picks the token up from the
environment, so it never enters HCL or state. The token needs `Account >
Cloudflare Tunnel > Edit` on the account and nothing else. The `cloudflare` stack
uses its own state namespace (`cloudflare-production`); `tofu/opnsense` is
untouched.


## `pod.rupan.dev` (owner desk)

The pod-agent dashboard is the only host here that is not a public site, and it
is not a public one either: it can approve proposals, which publishes products
to a live storefront. It sits behind **two** independent gates, and both must
be in place before the hostname serves anything useful.

1. Cloudflare Access application `pod-agent-dashboard`, hostname
   `pod.rupan.dev`, with an **Allow** policy on the owner email. Do not give it
   a Bypass policy the way `rupan.dev` and `flux-webhook-bypass` have one:
   Bypass on this hostname would put the storefront on the public internet.
2. `DASHBOARD_TOKEN` (GitLab `POD_AGENT_DASHBOARD_TOKEN`, hex 32) is already
   set on the Deployment, and `serve_forever` refuses a non-loopback bind
   without it. Every request still needs it, so the Access login is followed
   by a token prompt. First visit with
   `https://pod.rupan.dev/?token=$POD_AGENT_DASHBOARD_TOKEN`; the page sets the
   `dashboard_token` cookie for the browser session.

Access is the outer gate and the token is the inner one, deliberately. A
wildcard `*` Access app or a mis-scoped policy change cannot publish a
product on its own, and a token leaked from the GitLab project still only works
from behind an authenticated Access session.

Origin reachability is restricted in-cluster by
`gitops/pod-agent/network-policy.yaml`: only pods in the `cloudflare`
namespace may open a connection to the dashboard container port. A
compromised workload elsewhere in the cluster has no path to the origin at
all, so the hostname is the only way in.

## `sovereign.rupan.dev` (private concept site)

The Sovereign concept site is not a public launch: it exists for owner review
and early customer discovery. It stays behind a dedicated Cloudflare Access
application, dashboard managed like DNS (see "Known gaps"):

1. Access application `sovereign`, hostname `sovereign.rupan.dev`, with an
   **Allow** policy on the owner email only. No Bypass policy: Bypass on this
   hostname would put the pre-launch site on the public internet. The wildcard
   `*` app must not gain a Bypass covering it either.
2. DNS `CNAME sovereign.rupan.dev ->
   0f08d8c5-6f2c-409e-ba80-dc0601e0227e.cfargotunnel.com`, created by hand in
   the dashboard once, like every other hostname here.
3. The tunnel rule itself (`gitops/cloudflare/ingress-config.yaml`) is applied
   by `cloudflare_apply` like any other hostname; Access sits in front of it
   at the edge, so an unauthenticated visitor never reaches the origin.

The producer repository (`JEFF7712/sovereign`) is private.

## Tunnel configuration source

The Cloudflare API reports `config_src=cloudflare` and `remote_config=true`, and
that stays true: the connectors are remote-configured and always have been.
What changed on 2026-09-29 is who writes that remote config. It is
`tofu/cloudflare`, not a person in the dashboard.

The `config_src` value is unrelated to *where the ingress list is edited*.
Supplying `--credentials-file` and a local `--config` file never made the local
file authoritative, and the Deployment's Reloader annotation only restarts
connector pods. Both statements were true before this stack existed and neither
is how the rules reach the edge now.

History, kept because it explains the failure modes this stack is meant to
remove:

- 2026-09-23, v75 to v76: the remote config had 25 rules against 26 in the
  mirror. Every rule that existed matched, but Jellyfin was missing remotely, so
  the catch-all returned 404 until it was added.
- 2026-09-29, v81 to v82: `pod.rupan.dev` appended ahead of the catch-all, 43
  rules to 44, prior rules diffed byte-identical afterwards.
- 2026-09-29: stack added. Both failures above were parity failures between two
  copies of one list, which is the class of bug `yamldecode` removes.

## Known gaps

DNS records and Access policies are still dashboard managed, deliberately.

- DNS spans at least five zones including apexes (`rupan.dev`,
  `distrojeff.com`, `apollinestore.com`, `darkbitapparel.com`,
  `pulseagent.dev`). The resource is `cloudflare_dns_record` in provider v5,
  where the `cname` convenience attribute was removed, so each target is built
  as `<tunnel-id>.cfargotunnel.com`. Worth doing, but zone by zone, and not
  before the tunnel config is boring.
- Access should stay out of this stack for now. In provider v5 the
  application-scoped policies moved inline onto
  `cloudflare_zero_trust_access_application`, and applying an application whose
  `policies` list is empty detaches every policy from it, after which Cloudflare
  garbage-collects the orphans. There is already a wildcard `*` Access app
  gating this account's hostnames, so the first bad apply is a lockout rather
  than a downtime. If it is ever adopted, import first, never auto-apply, and
  give the `*` app its own state.
