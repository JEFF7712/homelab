# Cloudflare Tunnel `homelab`

Remote-configured tunnel fronting the new k3s cluster. Dashboard edits apply directly, so record version and order here.

## Identity

- Name: `homelab`
- ID: `0f08d8c5-6f2c-409e-ba80-dc0601e0227e`
- Config source: `gitops/cloudflare/ingress-config.yaml` (cut over 2026-09-22; connectors authenticate with the SOPS `cloudflared-credentials` secret and log no remote versions; dashboard rules vestigial)
- Connectors: 2 replicas from `gitops/cloudflare/tunnel.yaml` (`cloudflared 2026.8.3`)
- Health: `healthy`, 8 connections on `ord10, mci03, ord15, ord06, mci01, ord02`
- Public origin IP seen by edge: `50.93.213.22` (connector pods live in `10.0.30.0/24`)

## Traffic path for `photos.rupan.dev`

Internet -> Cloudflare edge -> `cloudflared` pods -> `http://immich-server.immich:80` (in-cluster Service DNS) -> `Service immich-server:80 -> 2283`.

Do not point tunnel origins at a Cilium LB VIP (`10.0.40.x`): Cilium implements it as a local redirect that only answers in host network namespaces, so `cloudflared` (pod netns) blackholes dialing it. Use in-cluster Service DNS names, consistent with the other new-cluster entries. The Gateway API layer was removed (tunnel is the public edge; NetBird covers remote LAN access), so the tunnel is the only path, not a bypass.

Related manifests:

- `gitops/cloudflare/tunnel.yaml`
- `gitops/immich/server.yaml`

## Ingress order (v73, 2026-09-20)

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
17. `rupan.dev -> http://rupan-dev-svc.rupan-dev:80`
18. `www.rupan.dev -> http://rupan-dev-svc.rupan-dev:80`
19. `grafana.rupan.dev -> http://kube-prometheus-stack-grafana.observability.svc.cluster.local:80`
20. `distrojeff.com -> http://distrojeff-site-svc.distrojeff:80`
21. `apollinestore.com -> http://apolline-svc.apolline:80`
22. `darkbitapparel.com -> http://darkbit-svc.darkbit:80`
23. `pulseagent.dev -> http://pulse-svc.pulse:80`
24. `flux-wh-33b0c8004348.rupan.dev -> http://webhook-receiver.flux-system:80` (Flux GitLab push receiver, 2026-09-20; Access app `flux-webhook-bypass` reused Bypass policy, wildcard `*` app would otherwise force login)
25. `jellyfin.rupan.dev -> http://jellyfin.media:80`
26. `http_status:404`

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
3. Commit and push; Stakater Reloader restarts `cloudflared` when the mounted ConfigMap changes. DNS `CNAME <host> -> <tunnel-id>.cfargotunnel.com` must already exist (created once per hostname in the dashboard).

## Previous dashboard-managed config issue (2026-09-22)

At the time, the remote config was at v75 and the dashboard was the live source of truth. This was superseded by the local config cutover described below.

Verified constraints (do not retry blindly):
- With `cloudflared ... run --token`, the remote config always wins over the local `--config` file (connectors log `Updated to new configuration ... version=N` with remote rules; the mounted ConfigMap is inert).
- Cloudflare rejects an empty remote ingress (error 1056, at least one rule required). A catch-all-only remote (v74) was picked up by both connectors and returned 404 for real hostnames; it was reverted within the window.

The conversion was completed on 2026-09-22: connectors now use a tunnel credentials file, which makes the tunnel locally managed and the local config authoritative. The steps taken were:
1. Minted tunnel credentials and extracted the `TunnelSecret` for `0f08d8c5-6f2c-409e-ba80-dc0601e0227e`.
2. Stored the credentials as a SOPS secret under `gitops/secrets/`, mounted it into the Deployment, and switched to `--credentials-file` with `--config`.
3. Rolled the connectors and confirmed they use the local configuration.
4. Confirmed the public hostnames serve correctly from outside the LAN.
5. Updated the Identity section to `Config source: gitops/cloudflare/ingress-config.yaml`.

## Local config authority

The connectors use `--credentials-file` with `/etc/cloudflared/config.yaml`, so `gitops/cloudflare/ingress-config.yaml` is authoritative. The old dashboard-managed rules are vestigial. The Deployment opts into Stakater Reloader so a ConfigMap update rolls the connectors and loads the new rules.

## Known gap

DNS records and Access policies remain dashboard managed. A `cloudflare_tunnel_config` under `tofu/` would close that loop (only `tofu/opnsense/` exists today).
