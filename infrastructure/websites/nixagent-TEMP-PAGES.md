# Temporary Cloudflare Pages cutover (nix-agent)

While the homelab is down, `https://nix-agent.rupan.dev` is served from
Cloudflare Pages project `nix-agent` (static export from
`~/projects/nix-agent/site`), not from this cluster manifest.

Permanent target remains this file: Argo CD → namespace `nixagent` →
`ghcr.io/jeff7712/nix-agent-site`.

## Take it down when the cluster is back

1. Confirm the in-cluster site is healthy after Argo syncs this directory
   (`kubectl -n nixagent get deploy,pods,ingressroute` and a local curl via
   the Traefik route / tunnel).
2. In Cloudflare Zero Trust / Tunnel, ensure a public hostname exists for
   `nix-agent.rupan.dev` → the cluster service (same pattern as the other
   `*.rupan.dev` websites).
3. Replace the temporary DNS record:
   - Delete the proxied CNAME `nix-agent` → `nix-agent.pages.dev`
   - Let the tunnel hostname recreate its CNAME to
     `<tunnel-id>.cfargotunnel.com` (or set that CNAME manually, proxied).
4. Remove the Pages custom domain, then delete the Pages project:
   ```bash
   npx wrangler pages project delete nix-agent
   ```
   Or: Cloudflare dashboard → Workers & Pages → `nix-agent` → Delete.
5. Verify `https://nix-agent.rupan.dev` returns the cluster site (not
   Pages) and that monitoring in `infrastructure/automation/config.yaml`
   stays green.

## Do not change

Leave `infrastructure/websites/nixagent.yaml` and the GHCR publish workflow
in the nix-agent repo as-is; they are the long-term path. This Pages
deploy is disposable.
