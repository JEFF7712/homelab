---
name: site-launch
description: Take a site from idea to public URL on the homelab. Use when adding a new website, exposing a new service publicly, or cutting over a producer to the local registry. Covers producer publish, manifests, tunnel, and go-live verification.
---

# Site Launch

End-to-end path for a stateless site: producer image -> local registry -> Flux manifests -> tunnel hostname -> public URL. Worked examples: `gitops/websites/rupan-dev/` (steady state), `gitops/websites/sovereign/` (staged launch with extras). Naming convention, all four must agree: namespace `<site>`, Deployment `website-deploy`, selector label `app: <site>-web`, Service `<site>-svc` (ClusterIP `80` -> container port).

## Phase 1: producer publishes to the local registry

First-party images live at `registry.rupan.dev/apps/<project>` and roll out without a manual pin. For a new producer:

1. On `nas-01`: add the `publisher-<project>` htpasswd entry to `/persist/zot/htpasswd`, regenerate access-control from the lock (`just registry-access-control`), restart zot. A policy entry without a credential is inert, so add both together at cutover.
2. In the producer repo: store the plaintext as its protected `REGISTRY_PASSWORD` secret. Producer builds once on a private runner, authenticates as `publisher-<project>`, pushes only to its own `apps/<project>`, and emits the destination digest plus source commit.
3. Confirm the first push landed, then put its digest in the Deployment. A zero placeholder digest (as `sovereign` carries pre-launch) must never deploy as-is.

There is no producer checkout for some legacy sites (`cr-demo`, `ism`): those keep their exact current releases via lock import, not producer pushes. Producer changes themselves belong in the source repo under separate authorization.

## Phase 2: manifests

One directory per site under `gitops/websites/<site>/`, copied from the hardened template (`rupan-dev/deployment.yaml`):

- `namespace.yaml`, `service.yaml` (ClusterIP, port 80), `deployment.yaml` (1 replica, digest-pinned `registry.rupan.dev` image, `IfNotPresent`, TCP probes, 500m/256Mi limits, 100m/128Mi requests, `emptyDir` `/tmp`, full hardening: non-root, no privilege escalation, `ALL` capabilities dropped, read-only root filesystem, RuntimeDefault seccomp).
- `network-policy.yaml` (`isolate-ingress`): allow same-namespace pods, plus the `cloudflare` and `observability` namespaces. No wider ingress.
- `kustomization.yaml` listing the four files.
- Register the directory in `gitops/websites/kustomization.yaml` and add a `website-deploy` healthCheck for the new namespace in `gitops/clusters/homelab-01/websites.yaml` (Flux `websites` Kustomization, path `./gitops/websites`, `prune: true`, 10m interval). Without the healthCheck entry Flux does not gate on the new Deployment.

Extras only when the site needs them (see `sovereign/`): a `nfs-cluster` PVC for state, an `ExternalSecret` against the `homelab-secrets` ClusterSecretStore for SOPS-managed tokens, path-scoped tunnel rules for per-path backends.

## Phase 3: edge

Delegate to the `tunnel-hostname` skill: ingress rule in `gitops/cloudflare/ingress-config.yaml` (specifics first, catch-all last), runbook list updated in position, `python -m unittest tests.test_cloudflare_tunnel`, merge a validated Forgejo PR, protected `cloudflare-apply` from that successful current-main pipeline, hand-made DNS CNAME, Access policy (Bypass for public, Allow-owner-email for sensitive). Origins must be Service DNS names, never a Cilium LB VIP.

## Phase 4: validate offline, then publish

```sh
nix develop ./flake -c kubectl kustomize gitops/websites > /tmp/homelab-websites.yaml
nix develop ./flake -c kubeconform -strict -ignore-missing-schemas /tmp/homelab-websites.yaml
nix develop ./flake -c yamllint gitops/websites gitops/clusters/homelab-01/websites.yaml
python -m unittest tests.test_cloudflare_tunnel
git diff --check
just check-changed
```

Publish a Forgejo PR, pass Woodpecker validation, and merge through main protection. Flux reconciles after its webhook or normal interval (GitRepository 1m, Kustomizations 10m). Confirm `websites` Kustomization `READY=True` at the new revision, then one ready pod and one ready endpoint in the site namespace.

## Phase 5: go-live verification

1. In-cluster: resolve the origin Service from a pod and curl it (proves Flux deployed the right content behind the Service).
2. Public: curl the hostname (proves tunnel + DNS). Confirm the Access behavior matches intent (public serves, sensitive challenges).
3. Steady state: future producer pushes flow through scheduled `registry-promote-first-party` (creates a Forgejo review PR containing the lock and consumer digest), then merge and Flux reconciliation. End-to-end latency includes producer CI, promotion, review, and Flux reconciliation.
4. Rollback is a Git revert; `prune: true` removes the site's resources.

## Gotchas

- Tunnel parity includes hostname, service and path. Preserve ingress order when adding path-scoped rules.
- Promotion is verification-only and never mirrors content; only `publisher-<project>` can write its destination. Candidates the producer has not published are skipped, not queued.
- `registry-promote-first-party` needs the Woodpecker maintenance cron and repository-scoped `FORGEJO_PUBLISH_TOKEN`; promotion creates a review PR and cannot write protected main.
- Never trigger manual registry jobs (`registry_lock_import`, `cloudflare_apply`, retention) from a push; a repository push authorizes nothing by itself.

## Maintaining this skill

This file is a living doc. When a launch teaches something durable (a new required manifest, a changed promotion behavior, a moved gate), edit this file in the same session, keeping the five phases and one-line gotchas. Deep mechanics belong in `docs/runbooks/local-registry.md` (registry), `docs/runbooks/cloudflare-tunnel.md` (edge), and `docs/runbooks/ci-pipelines.md` (CI lanes); this file holds the launch path only. Verify doc edits with `just fmt-check`.
