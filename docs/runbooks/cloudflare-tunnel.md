# Cloudflare Tunnel `homelab`

Remote-configured tunnel fronting the new k3s cluster. Dashboard edits apply directly, so record version and order here.

## Identity

- Name: `homelab`
- ID: `0f08d8c5-6f2c-409e-ba80-dc0601e0227e`
- Config source: Cloudflare remote configuration (`config_src=cloudflare`; checked 2026-09-23). `gitops/cloudflare/ingress-config.yaml` is a tracked mirror, not the active source.
- Connectors: 2 replicas from `gitops/cloudflare/tunnel.yaml` (`cloudflared 2026.8.3`)
- Health: `healthy`, 8 connections on `ord10, mci03, ord15, ord06, mci01, ord02`
- Public origin IP seen by edge: `50.93.213.22` (connector pods live in `10.0.30.0/24`)

## Traffic path for `photos.rupan.dev`

Internet -> Cloudflare edge -> `cloudflared` pods -> `http://immich-server.immich:80` (in-cluster Service DNS) -> `Service immich-server:80 -> 2283`.

Do not point tunnel origins at a Cilium LB VIP (`10.0.40.x`): Cilium implements it as a local redirect that only answers in host network namespaces, so `cloudflared` (pod netns) blackholes dialing it. Use in-cluster Service DNS names, consistent with the other new-cluster entries. The Gateway API layer was removed (tunnel is the public edge; NetBird covers remote LAN access), so the tunnel is the only path, not a bypass.

Related manifests:

- `gitops/cloudflare/tunnel.yaml`
- `gitops/immich/server.yaml`

## Ingress order (v82, 2026-09-29)

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
7. `demo.rupan.dev -> http://cr-demo-svc.cr-demo:80`
8. `soluble.rupan.dev -> http://soluble-rupan-svc.soluble-rupan:80`
9. `photo.rupan.dev -> http://photography-svc.photography:80`
10. `majorfinder.rupan.dev -> http://majorfinder-svc.majorfinder:80`
11. `notes.rupan.dev -> http://quartz-notes.obsidian.svc.cluster.local:80`
12. `ntfy.rupan.dev -> http://ntfy-ntfy.observability.svc.cluster.local:80`
13. `obsidian.rupan.dev -> http://couchdb.obsidian.svc.cluster.local:5984`
14. `renovate-status.rupan.dev -> http://renovate-dashboard.automation.svc.cluster.local:80`
15. `renovate-approve.rupan.dev -> http://renovate-approval-webhook.automation.svc.cluster.local:80`
16. `ha.rupan.dev -> http://home-assistant.home-assistant:8123`
17. `ma.rupan.dev -> http://music-assistant.music-assistant:8095`
18. `ledfx.rupan.dev -> http://ledfx.music-assistant:8888`
19. `rupan.dev -> http://rupan-dev-svc.rupan-dev:80`
20. `www.rupan.dev -> http://rupan-dev-svc.rupan-dev:80`
21. `grafana.rupan.dev -> http://kube-prometheus-stack-grafana.observability.svc.cluster.local:80`
22. `distrojeff.com -> http://distrojeff-site-svc.distrojeff:80`
23. `apollinestore.com -> http://apolline-svc.apolline:80`
24. `darkbitapparel.com -> http://darkbit-svc.darkbit:80`
25. `pulseagent.dev -> http://pulse-svc.pulse:80`
26. `flux-wh-33b0c8004348.rupan.dev -> http://webhook-receiver.flux-system:80` (Flux GitLab push receiver, 2026-09-20; Access app `flux-webhook-bypass` reused Bypass policy, wildcard `*` app would otherwise force login)
27. `jellyfin.rupan.dev -> http://jellyfin.media:80`
28. `navidrome.rupan.dev -> http://navidrome.media:80`
29. `music.rupan.dev -> http://navidrome.media:80`
30. `seerr.rupan.dev -> http://seerr.media:80`
31. `requests.rupan.dev -> http://seerr.media:80`
32. `lidarr.rupan.dev -> http://lidarr.media:80`
33. `radarr.rupan.dev -> http://radarr.media:80`
34. `sonarr.rupan.dev -> http://sonarr.media:80`
35. `prowlarr.rupan.dev -> http://prowlarr.media:80`
36. `bazarr.rupan.dev -> http://bazarr.media:80`
37. `slskd.rupan.dev -> http://slskd.media:80`
38. `torrent.rupan.dev -> http://qbittorrent.media:80`
39. `qbittorrent.rupan.dev -> http://qbittorrent.media:80`
40. `books.rupan.dev -> http://calibre-web.media:80`
41. `media.rupan.dev -> http://filebrowser.media:80`
42. `bookshelf.rupan.dev -> http://bookshelf.media:80`
43. `pod.rupan.dev -> http://pod-agent-dashboard.pod-agent:80` (owner desk, added 2026-09-29; requires the Access app `pod-agent-dashboard`, see below)
44. `http_status:404`

Removed 2026-09-15 (v72):
- `*.rupan.dev -> https://10.0.20.180:443` (defunct Talos Traefik VIP; caused grafana outage, then 404s for unmatched hosts after grafana fix)
- `sandhufiles.site -> https://10.0.20.180:443` (defunct site)

Removed 2026-09-22 (dead origins; no such Services in-cluster, verified via `kubectl -n cloudflare get svc`):
- `glance.rupan.dev -> http://glance:8080`
- `pihole.rupan.dev -> http://pihole:80`
- `api.rupan.dev -> http://rupan-api:9000`
- `homelab.rupan.dev -> http://homelab-api:9200`

## Add a new-cluster hostname

1. Add the `{hostname, service}` rule to `gitops/cloudflare/ingress-config.yaml` in Cloudflare-evaluated order (specifics first, catch-all last) and to the numbered list above in the same position.
2. Run `python -m unittest tests.test_cloudflare_tunnel` — it asserts config order matches this runbook and every origin Service exists.
3. Update the Cloudflare remote ingress configuration to match the GitOps list rule-for-rule and in the same order. The mounted ConfigMap and Reloader do not change remote ingress rules; Reloader only restarts the connector pods.
4. Confirm the tunnel's remote config matches the GitOps list. DNS `CNAME <host> -> <tunnel-id>.cfargotunnel.com` must already exist (created once per hostname in the dashboard).
5. Verify the public hostname serves the expected origin.

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

## Tunnel configuration source

The Cloudflare API reports `config_src=cloudflare` and `remote_config=true`. On 2026-09-23, the remote ingress was updated from version 75 to version 76 to match the GitOps mirror. Supplying `--credentials-file` and a local `--config` file does not change the tunnel's configured source; the Cloudflare remote config remains active.

Before that update, the active remote config had 25 rules while the GitOps mirror had 26. Every remote rule matched the mirror, but the Jellyfin hostname and origin were missing remotely, so the catch-all returned 404. Version 76 now has all 26 rules in the GitOps order, and the public Jellyfin health endpoints return HTTP 200.

v82 (2026-09-29) appended `pod.rupan.dev` ahead of the catch-all, 43 rules to 44. The prior 43 were diffed byte-identical after the write, so the first-match behaviour of every other hostname is unchanged. The Access login for that hostname predates the tunnel rule: `pod.rupan.dev` was already intercepted by an Access app, so before v82 it 302'd to the login and would have 404'd at the catch-all once past it.

The Deployment opts into Stakater Reloader. This restarts connectors when the ConfigMap changes, but a restart alone does not make the local file authoritative. Keep the Cloudflare remote rule list and GitOps mirror in exact parity when changing hostnames.

## Known gap

DNS records and Access policies remain dashboard managed. A `cloudflare_tunnel_config` under `tofu/` would close that loop (only `tofu/opnsense/` exists today).
