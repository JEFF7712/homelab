# Agent1 App Migrations: Review Handoff (v2)

Status: complete. All 13 assigned websites/docs projects migrate through
Forgejo → Woodpecker (server-owned policy) → Zot → protected homelab PR →
Flux, with offline build proof and independent recovery inputs.

This answers the five review findings on the prior handoff:
1. All eight node-service apps now have server-owned release pipelines
   (`release_kind: node`); main pushes generate validation + release.
2. GitHub publishing retired on BOTH forges (only `ci.yml` quality gates
   remain where they existed; isms/cr-demo had nothing to retire).
3. Node validator uses fullmatch `LOCAL_IMAGE` via `dockerfile_supply`
   (mirrored-prefix bypass rejected) with regression tests.
4. Cold receipts: per-app pack receipts, 16/16 network-disabled rebuild
   logs, retained supply manifests, in-image provenance labels.
5. Reported digests are the exact published digests, matching merged
   manifests and running pod imageIDs (verified below).

## Application rows

Columns: authoritative repo @ main SHA | Woodpecker release (pipeline,
tag@digest, source) | promotion PR | Flux/runtime (pod digest = pin,
HTTP 200) | cold proof | rollback pin | external deps.

- apolline-site @356ea23ca8fc | repo 3 #5,
  0.0.1000005@6377fb176bd469dcb1d4d7a0a0f5be163d45752ef5aa25f8891d945b07c05c45,
  src 356ea23ca8fc | homelab #159 | website-deploy = pin, HTTP 200 |
  static lane (no build; source = artifact) | 5ae14a4d5950287a |
  Etsy shop link (declared).
- cr-demo @4b7cc8c41756 | static lane, no push since migration (no release
  fired; current image stands) | none (unchanged) | website-deploy 1/1,
  HTTP 200 | git bundle + manifest | current pin unchanged | none.
- distrojeff-site @662b3d849611 | repo 13 #20,
  0.0.1000020@96cfa29f813e29f3d737d435de0b175a19d2767fa58becd6e763c1900ed7a327,
  src 662b3d849611 | homelab #148 | website-deploy = pin, HTTP 200 |
  drill Tier-1 glibc+musl rc=0, pack receipt lock 8e780084 |
  968b62c24c6b4f4a | none.
- ism @9cf88748ce89 | repo 6 #8,
  0.0.1000008@6a0003070b2b3c167f827c23d93dac63310738cd3de12374ecaae5fd5606a05b,
  src 9cf88748ce89 | homelab #156 | website-deploy = pin, HTTP 200 |
  drill glibc+musl rc=0, lock bc7f7d4f | 7f663f0b19b61cb6 |
  none.
- majorfinder @427340ac86aa | repo 9 #9,
  0.0.1000009@399a651b3cd32aba701039b6e87a3301752a362ee8700e58f0fde7b5472087d6,
  src 427340ac86aa | homelab #149 | website-deploy = pin, HTTP 200 |
  drill glibc+musl rc=0, lock cd3db33d | 24714b06d817ce0e |
  Cloudflare Pages project `majorfinder` still exists externally
  (Rupan to delete; not referenced anymore).
- nix-agent-site @f93c7fdaf22f | repo 7 #13,
  0.0.1000013@e96ec28cc3b00fecb4820cc4a277f2ba36a314c408c46ac966259904911f9651,
  src f93c7fdaf22f | homelab #156 | website-deploy = pin (+busybox
  sidecar), HTTP 200 | drill glibc+musl rc=0, lock c0c7948a,
  pnpm→npm migration (tests 111/111, typecheck clean) | 8987116457555b4e |
  none.
- obsidian-vault (quartz) @b17a93d8a7c1 | repo 14 #21,
  0.0.1000021@e4ef61939a2c7ac857d50120d6538988523a2dc5197810c1c8b66a3d75a1b8ad,
  src b17a93d8a7c1 | homelab #157 (port 80→8080) | quartz-notes = pin,
  HTTP 200 ("my notes") | drill glibc+musl rc=0 (45 plugins skipped,
  fonts seeded, 859 files), lock 6d3b58931 | 785e71c0563aca7f |
  none (vault content is source).
- photos @cad9e749f1e5 | repo 10 #5,
  0.0.1000005@c5656a8087c414fca664ec7893b8527c9743f39a1af603ba90bf9fd0cd49cca5,
  src cad9e749f1e5 | homelab #159 | website-deploy = pin, HTTP 200 |
  static lane | 9944a881e22315f |
  Cloudflare Pages project `photography` still exists externally
  (Rupan to delete; not referenced anymore).
- pulse-site @c04dc335a3b5 | repo 8 #26,
  0.0.1000026@4ef2cc00e4b4f22d42ed5e1b0f27df4fd2fc9df2f218b58612b0fa144df46466,
  src c04dc335a3b5 | homelab #150 | website-deploy = pin, HTTP 200 |
  drill glibc+musl rc=0, lock eee0ba05 | 6c1d62dc8c32a92d |
  none. (Sovereign inquiry service: pulse BACKEND is out of scope;
  pulse-site is the static docs/marketing site; no persistent state.)
- rupan-dev @847015828c85 | repo 4 #17,
  0.0.1000017@fdd4a9297cbc5b7733cdd9728cb9f3494b410992414337ca3813c87df4b5f1d8,
  src 847015828c85 | homelab #153 | website-deploy = pin, HTTP 200 |
  drill glibc+musl rc=0, lock adb260e2 | 665e49253074d6bc0 |
  none.
- rupanism @6a3135dac728 | repo 5 #13,
  0.0.1000013@b30755a294015a2f1930348ff924d25ff2739b0afbfa6213c26b7a34ecf08f6a,
  src 6a3135dac728 | homelab #156 | website-deploy = pin, HTTP 200 |
  drill glibc+musl rc=0, lock 02904b2c, bun→npm migration (tests
  33/33, typecheck clean) | a982a71729eefa72 | none.
- sovereign-site @155aea35c69e | repo 12 #5,
  0.0.1000005@76d5194e95b779771ce4bd3bc81664152be3823ebb67b5880fd39e2122e3b4e3,
  src 155aea35c69e | homelab #159 | website-deploy = pin, HTTP 200 |
  static lane | 873d57c8372816c | none. (Sovereign inquiry service:
  assessed stateless marketing site; no persistent component found in
  the deployed manifest. No isolated restore applies.)
- spatia-site @15c2248445c5c | repo 11 #5,
  0.0.1000005@eef492ceaebaac7a2c8716aebbe7a78c057c68d4dd9c3c120e4f7fa395b825c3,
  src 15c2248445c5c | homelab #159 | website-deploy = pin (+sidecars),
  HTTP 200 | static lane | 2b0881af3df120f0 | none. (Confirmed hosted
  website only; backend/iOS not deployed, unchanged.)

## Source completion vs live acceptance vs recovery proof

- Source completion: 13/13 Forgejo repos authoritative (full history,
  branches/tags preserved by prior migration; my changes: 3 migration
  PRs + 8 retirement PRs + 1 quartz fix + 4 static retirements, all
  merged; GitHub publish paths deleted on both forges).
- Live acceptance: 13/13 HTTP 200 with expected content from the
  cloudflare namespace (isolate-ingress compliant); pod imageIDs equal
  pins except cr-demo (unchanged, healthy).
- Recovery proof: per-app git bundles under /home/rupan/sovereign-recovery
  (prior) + retained supply in Forgejo generic packages
  (`node-toolchain` v1: node.tar.gz, node-musl.tar.gz, node-libs.tar.gz;
  `<app>-supply` v1 npm caches superseded by v2 dual-libc, v3 current;
  quartz plugins + fonts) + pack receipts (/tmp/supply-*/receipt.json)
  + offline drill logs (/persist/agent1-drill/work/logs-{glibc,musl}/,
  STATUS files 8/8 rc=0 each) + release.json provenance in every image
  (source commit, base, supply identity). No persistent components, so
  no isolated state restores apply.

## Shared files and infrastructure changes (for later review)

- Homelab PRs merged: #143 (node lane + validator + 8 enrollments),
  #144 (assemble paths/diagnostics), #145 (dual-libc selection),
  #146 (musl libs + static contract), #147 (trial with libs),
  #148/#149/#150/#153/#156/#157/#159 (promotions + v3 pins + NODE_ENV
  fix #152). Files: config/ci/applications.json,
  scripts/ci/{applications,node_service,node_release,node_supply}.py,
  tests/test_application_ci.py, gitops website/obsidian pins,
  registry/images.lock.json. Agent 2 entries untouched (merge-clean).
- Policy deployed to homelab-04 /var/lib/woodpecker-policy (full
  scripts/ci dir + catalog) with restarts; `supply_read_password`
  (shared read value, no rotation) installed on repos 4,5,6,7,8,9,13,14.
  Lock held for activation + secrets; released between steps.
- Zot: 9 generic packages, 21 supply files (see UPLOAD.sh/UPLOAD-v2.sh
  record in /tmp/supply-stage/); per-app `forgejo-<id>` published
  releases (existing credentials, no new publish secrets); retention
  tags created per release. No shared credential rotated, no host
  rebooted, no other producer resources touched.
- Woodpecker: 12 app release pipelines green (repos 3,4,5,6,7,8,9,10,
  11,12,13,14). cr-demo had no pipeline event (no changes).

## Remaining external dependencies and actions for Rupan

1. Delete Cloudflare Pages projects `majorfinder` and `photography`
   (workflows deleted; Pages deployments orphaned but live).
2. Decide on cluster `github-runner-*` deployments (idle now; platform
   decision with Agent 2's guard work, not removed by me).
3. Rotate the `homelab-agent` tea token: it printed in cleartext in my
   session output during a failed public-URL push attempt
   (Cloudflare Access rejected the push; token itself still valid).
   Re-run `tea login add` + update `~/.config/tea/config.yml`, then
   revoke the old token in Forgejo UI.
4. Revoke the one-time `write:package` token at
   /tmp/forgejo-package-upload.token (uploads complete + verified;
   delete the file after revocation).
5. GitHub repos remain as inert forks (publish workflows deleted, only
   ci.yml remains); no mirror configured either direction.
