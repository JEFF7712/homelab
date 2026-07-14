# Temporary Cloudflare Pages cutover (pulse)

While the homelab is down, `https://pulseagent.dev` (and `www`) is served
from Cloudflare Pages project `pulseagent` (static landing + VitePress docs
built from `~/projects/pulse/site`), not from this cluster manifest.

Permanent target remains this file: Argo CD → namespace `pulse` →
`ghcr.io/jeff7712/pulse-site`.

## Take it down when the cluster is back

1. Confirm the in-cluster site is healthy after Argo syncs this directory
   (`kubectl -n pulse get deploy,pods,ingressroute` and a local curl via
   the Traefik route / tunnel).
2. In Cloudflare Zero Trust / Tunnel, ensure public hostnames exist for
   `pulseagent.dev` and `www.pulseagent.dev` → the cluster service.
3. Replace the temporary DNS records on zone `pulseagent.dev`:
   - Delete proxied CNAMEs for `@` / `www` pointing at
     `pulseagent.pages.dev`
   - Let the tunnel hostnames recreate CNAMEs to
     `<tunnel-id>.cfargotunnel.com` (or set those manually, proxied).
4. Remove the Pages custom domains, then delete the Pages project:
   ```bash
   npx wrangler pages project delete pulseagent
   ```
   Or: Cloudflare dashboard → Workers & Pages → `pulseagent` → Delete.
5. Verify `https://pulseagent.dev` and `/docs/` return the cluster site
   (not Pages) and that monitoring in
   `infrastructure/automation/config.yaml` stays green.

## Do not change

Leave `infrastructure/websites/pulse.yaml` and the GHCR/Docker publish
workflow in the pulse repo as-is; they are the long-term path. This Pages
deploy is disposable.
