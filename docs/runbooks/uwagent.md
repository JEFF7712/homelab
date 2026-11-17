# uwagent

uwagent (`JEFF7712/uwagent`) is served at https://uwagent.rupan.dev from the
handwritten manifests in `gitops/websites/uwagent/`. Its image releases through
the usual application path (`config/ci/applications.json`, `promote`); this
runbook covers the two homelab-owned pieces around it.

## Usage events key

The server logs one JSON line per tool call (`{"evt":"tool",...}`) to stdout,
which reaches Loki. Each event's `session` is an HMAC of the plan id keyed by
`UWAGENT_EVENTS_KEY`, so sessions stay stable across pod restarts only while
that key does. The key is a random 32-byte hex value in
`gitops/secrets/uwagent-events.sops.yaml` (Secret `uwagent-events`, key
`events-key`), encrypted to the Flux recipient. The Deployment reads it as an
optional `secretKeyRef`: without the Secret the server falls back to a
per-process key, so check presence after a change:

```sh
kubectl -n uwagent exec deploy/website-deploy -- sh -c 'test -n "$UWAGENT_EVENTS_KEY" && echo set'
```

Rotating the key splits every session at the rotation, which is acceptable;
replace the file with a new value (see [secret handling](secrets.md)) and
restart the Deployment. `UWAGENT_EVENTS=off` disables events.

## Madgrades grade refresh

`data/grades.json` in uwagent holds grade distributions for the newest six
fall and spring terms Madgrades publishes. Madgrades adds terms by hand, so
the `uwagent-grades-refresh` operation (`scripts/ci/uwagent_grades.py`) polls
weekly:

1. If a `chore/grades-*` PR is already open, it stops.
2. It clones uwagent `main`, downloads the release's pinned `node-toolchain`
   and `uwagent-supply` npm cache, checks their digests, and runs
   `npm ci --offline` under the flake's `nodejs_24`.
3. `npm run fetch-madgrades -- --if-new` makes one call to
   `api.madgrades.com/v1/terms`. With no newer term it logs
   `no term newer than <term>; grades.json unchanged` and the run ends.
4. With a newer term the full fetch runs (about 25 minutes, ~8.6k requests),
   then `npm run build` and `vitest run tests/grades.test.ts
   tests/recommend.test.ts` must pass. The rest of the suite needs a local,
   git-ignored `plan.json`.
5. It pushes `chore/grades-<term>` as `homelab-publisher` and opens
   `chore(data): refresh Madgrades grades through <term>`. An existing branch
   of that name is never overwritten. Merging and releasing stay with uwagent.

npm scripts run with only `PATH`, `HOME`, the npm cache settings and
`MADGRADES_TOKEN`; the CI tokens are not in their environment. A lockfile
change in uwagent that the pinned cache does not cover fails step 2 with
`ENOTCACHED`; refresh the snapshot through [node supply](node-supply.md).

Credentials (homelab repository secrets in Woodpecker):

- `madgrades_token`: the Madgrades API token, events `cron` and
  `deployment`, no image filter.
- `forgejo_publish_token`: `homelab-publisher`, which writes to every
  `JEFF7712` repository through the `publishers` team
  ([Forgejo organization teams](forgejo-organization.md)).
- `supply_read_password`: the read-only supply token; scheduled use needs the
  `cron` event as well as `deployment`.

Run it by hand with `just ship --resume <merge-sha> --deploy uwagent-grades-refresh`
or a `deploy_to=uwagent-grades-refresh` deployment of a validated main commit.
