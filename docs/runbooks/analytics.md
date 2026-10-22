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
  The Access application `tally` is a public Bypass (dashboard-managed like
  every other Access app): trackers on every site must load the script and post
  events anonymously. The dashboard relies on Umami's own login.

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

The tag belongs in the site's source repository; the site rolls out through
its producer pipeline and `registry-promote-first-party`. Tracked sites:

| Site | Website id |
| --- | --- |

## Checks

```sh
kubectl -n analytics get cluster,deploy,pods
curl -fsS https://tally.rupan.dev/api/heartbeat
curl -fsS -o /dev/null -w '%{http_code}\n' https://tally.rupan.dev/t.js
```
