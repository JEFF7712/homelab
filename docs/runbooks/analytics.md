# Web analytics (Umami)

Cookieless page analytics for the public sites, served at
`https://tally.rupan.dev`. Owned by `gitops/analytics/` (Flux Kustomization
`analytics`).

## Architecture

- **App:** Deployment `analytics/umami`, image
  `registry.rupan.dev/upstream/ghcr.io/umami-software/umami` (declared in
  `gitops/analytics/registry-sources.yaml`, locked in `registry/images.lock.json`).
  It runs Prisma migrations on start, so an image upgrade migrates the schema.
- **Tracker:** `TRACKER_SCRIPT_NAME=t.js`. Filter lists match `umami.js` and
  `script.js` by name, so sites must load `/t.js`. Events post to `/api/send`.
- **Database:** CloudNativePG `analytics/umami-cluster`, two instances on
  static `local-nvme` volumes at `/persist/postgres/cnpg/umami-postgres-{1,2}`
  (homelab-01 and homelab-05). Same layout and recovery as the other clusters;
  see [postgres-disaster-recovery](postgres-disaster-recovery.md).
- **Secrets:** nothing in SOPS. CNPG generates `umami-cluster-app`
  (`uri` feeds `DATABASE_URL`). ESO generates `umami-app-secret` once
  (`refreshPolicy: CreatedOnce`); deleting it rotates `APP_SECRET` and signs
  every dashboard session out.
- **Backups:** `backups/umami-db-dump` (02:45 UTC) dumps to the shared
  `db-dumps` volume, picked up by `dumps-to-r2`. Its password is copied from
  `umami-cluster-app` through the `analytics-db` ClusterSecretStore, which only
  the `backups` namespace may use and which can read only that Secret.
- **Edge:** tunnel rule `tally.rupan.dev -> http://umami.analytics:80`.
  The dashboard-managed Access application `Tally - owner dashboard` uses the
  owner-only `Main - Rupan` policy. `Tally - public tracker endpoints` uses the
  reusable `Bypass` policy only for `/t.js` and `/api/send`, so tracking works
  anonymously. The dashboard also requires Umami's own login.

## First deploy

1. Create the volume directories before Flux applies the cluster, or the
   instances stay Pending:

   ```sh
   ssh homelab-01 'sudo install -d -o 0 -g 26 -m 2777 /persist/postgres/cnpg/umami-postgres-1'
   ssh homelab-05 'sudo install -d -o 0 -g 26 -m 2777 /persist/postgres/cnpg/umami-postgres-2'
   ```

2. After the Deployment is ready, log in as `admin` / `umami` and change the
   password immediately. The admin password lives only in the database.

## Tracking a site

Add one website per hostname in the Umami dashboard (Settings, Websites) and
put its id in the site's `<head>`:

```html
<script defer src="https://tally.rupan.dev/t.js" data-website-id="<id>"></script>
```

Static-contract sites (`release_kind: static`) cannot reference remote
assets, so they vendor the tracker as `assets/t.js` and add
`data-host-url="https://tally.rupan.dev"`; refresh that copy after a Umami
upgrade. Every tag sets `data-domains` to the production hostnames so local
development is not counted.

The tag belongs in the site's source repository; the site rolls out through
its producer pipeline and `registry-promote-first-party`. Tracked sites:

| Site | Repository | Loader | Website id |
| --- | --- | --- | --- |
| `pulseagent.dev` | `JEFF7712/pulse` | remote | `c2a12929-5a52-4e64-bbe1-a1e37fa64d3d` |
| `uwagent.rupan.dev` | `JEFF7712/uwagent` | remote | `b3c2e153-3508-418c-89a9-25a606189997` |
| `majorfinder.rupan.dev` | `JEFF7712/majorfinder` | remote | `b178af9d-3bb8-4bb3-82c9-e45fe504fa58` |
| `nix-agent.rupan.dev` | `JEFF7712/nix-agent` | remote | `e8777c6b-0876-4b6e-8495-3bfee147cf8a` |
| `spatia.rupan.dev` | `JEFF7712/spatia` | vendored | `ca4ca158-6373-4394-85e2-e5e046f23e64` |
| `sovereign.rupan.dev` | `JEFF7712/sovereign` | vendored | `415790b3-5636-42ad-85a8-1da96256e04a` |
| `apollinestore.com` | `JEFF7712/apolline-site` | vendored | `d684a40a-4ed9-43b8-9731-3d077f26dd89` |
| `darkbitapparel.com` | `JEFF7712/darkbit-site` | vendored | `85de2077-6527-4f62-a992-1e9fb90bbbcd` |
| `distrojeff.com` | `JEFF7712/distrojeff-site` | remote | `60762e45-bdce-48bd-aa12-b01774d5a445` |
| `rupan.dev` | `JEFF7712/rupan.dev` | remote | `a82d3be1-f232-4475-9f7c-79f2b8367641` |
| `photo.rupan.dev` | `JEFF7712/photos` | vendored | `3512fee0-d7bd-4c90-9758-7ea93154a480` |
| `rupanism.rupan.dev` | `JEFF7712/rupanism` | remote | `5c8b1f08-fd9a-4eaa-b0ff-976060a547a5` |
| `ism.rupan.dev` | `JEFF7712/ism` | remote | `3e689e8c-ac0b-499a-a677-808e54f0105c` |

## Checks

```sh
kubectl -n analytics get cluster,deploy,pods
curl -sS -o /dev/null -w '%{http_code}\n' https://tally.rupan.dev/  # 302 to Access
curl -fsS -o /dev/null -w '%{http_code}\n' https://tally.rupan.dev/t.js
```
