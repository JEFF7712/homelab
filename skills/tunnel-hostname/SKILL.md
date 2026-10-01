---
name: tunnel-hostname
description: Add or change a public hostname on the homelab Cloudflare Tunnel. Use when the user asks to expose a service (e.g. "add X.rupan.dev"), set up Access gating, or debug a hostname serving the wrong origin. Applies via CI, not locally.
---

# Tunnel Hostname

The tunnel `homelab` (`0f08d8c5-6f2c-409e-ba80-dc0601e0227e`) is remote-configured; `tofu/cloudflare` writes that remote config from Git. The dashboard is a read-back, not an input. Canonical reference: `docs/runbooks/cloudflare-tunnel.md`.

## Adding a hostname

1. Add the `{hostname, service}` rule to `gitops/cloudflare/ingress-config.yaml` in Cloudflare-evaluated order (specifics first, catch-all `http_status:404` last). This file is the single source: `tofu/cloudflare/main.tf` decodes it with `yamldecode`.
2. Add the same entry to the numbered ingress list in `docs/runbooks/cloudflare-tunnel.md`, same position. `tests/test_cloudflare_tunnel.py` asserts config order matches the runbook and every origin Service exists.
3. Run `python -m unittest tests.test_cloudflare_tunnel` before pushing.
4. Commit and push. `cloudflare_plan` runs on `main`; read the resource changes in the job log.
5. Run `cloudflare_apply` (manual job) from the pipeline. It reports the new `config_version`.
6. DNS `CNAME <host> -> <tunnel-id>.cfargotunnel.com` is created by hand in the dashboard, once per hostname. It is not managed by this stack.
7. Verify the public hostname serves the expected origin.

Never edit ingress rules in the Zero Trust dashboard; the next apply reverts them.

## Access gating (dashboard-managed, pick one)

- Public site: Bypass policy (as on `rupan.dev`, `flux-webhook-bypass`).
- Anything sensitive (dashboards that approve/publish, pre-launch sites, owner desks): Allow policy on the owner email only. Never Bypass. A wildcard `*` app must not gain Bypass covering it.
- Double-gate where the runbook says so (e.g. `pod.rupan.dev`: Access login outer, `DASHBOARD_TOKEN` cookie inner).

## Gotchas

- Origins must be in-cluster Service DNS (`http://svc.namespace:port`), never a Cilium LB VIP (`10.0.40.x`) which `cloudflared` blackholes.
- Access applications and DNS stay out of tofu deliberately (provider v5 detaches policies on empty `policies` lists; a bad first apply locks out rather than causing downtime). If the runbook's "Known gaps" section changes, this skill follows it.
- A 404 on a new hostname usually means a missing rule (remote had fewer rules than the mirror) or rule order, not DNS.

## Maintaining this skill

This file is a living doc. When the runbook changes (new Access pattern, DNS moves into tofu, ordering rule changes), update this file in the same session to match; it must never disagree with `docs/runbooks/cloudflare-tunnel.md`. Keep the recipe to these seven steps. Verify doc edits with `just fmt-check`.
