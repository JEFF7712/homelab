# Nix Binary Cache: Activation + Laptop/CUDA Consumer Plan

Last reviewed: 2026-07-11

**Goal:** Finish and operationalize the self-hosted Attic binary cache so the NixOS
laptop substitutes expensive closures (unfree CUDA: `torch`, `ctranslate2`, plus the
whole system closure) from the homelab instead of rebuilding from source. Directly
addresses the recurring pain from the 2026-07-11 `njs` incident: a nixpkgs bump forced
a ~3000-derivation from-source rebuild (toolchain cascade + CUDA overlay packages that
never hit `cache.nixos.org`). The cascade guard in `~/nixos`
(`home/scripts/nix-cascade-guard`) now *defers* those bumps; this cache makes the
unavoidable CUDA rebuilds a one-time cost that every later switch reuses.

**Status: mostly built, currently offline.** This is an activation/completion plan, not
a from-scratch deploy. The detailed original implementation plan and design spec still
apply for the server internals; see References. Do this "later," when the homelab is
back online.

---

## Current State (verified 2026-07-11)

**Server side (homelab repo), authored, committed, iterated:**
- `infrastructure/nix-cache/attic.yaml`: Namespace, ConfigMap (`server.toml`: SQLite
  metadata + S3/MinIO chunk storage, chunking, 60d GC), 5Gi Longhorn PVC, `atticd`
  Deployment (Reloader-annotated), LoadBalancer Service pinned to `10.0.20.190:8080`
  via Cilium LB-IPAM.
- `infrastructure/nix-cache/gc-cronjob.yaml`: weekly GC (Sun 04:00, `Forbid` concurrency).
- `apps/nix-cache.yaml`: ArgoCD Application, auto-sync + prune + selfHeal.
- `secrets/nix-cache.yaml`: two ExternalSecrets (HS256 server token; mirrored
  `minio-creds-nix-cache`) via the `gitlab-backend` ClusterSecretStore.
- MinIO backend already refined past the original plan: `server.toml` points at the
  externally-routable endpoint `http://10.0.20.191:9000` (commit `d91e3cdc`) so the
  presigned GET URLs atticd hands back resolve for off-cluster clients (laptop/CI).
- ServiceMonitor was intentionally dropped; probes hit `/` (commits `84c26515`, `8bba9551`).

**Cache was created at least once:** the ed25519 public key is already captured:
`homelab:s17u8G3szjlQ6UmMAPsszVS/J1jaw6gDwSDM9+/QeNQ=`. A `homelab` cache existed and
published its key, so PR1 (deploy + bootstrap) was executed, not just authored.

**Laptop side (`~/nixos`): staged but disabled:** `hosts/laptop/base.nix:299-311` holds
a commented-out declarative block (`nix.settings.extra-substituters` /
`extra-trusted-substituters` / `extra-trusted-public-keys`) with the real IP and the
captured pubkey above, labeled "Disabled while the homelab is offline."
`nix.settings.accept-flake-config = true` is already active (so flake `nixConfig`
substituters do not hang direnv on a y/N prompt).

**What is NOT proven / NOT done:**
- Liveness: `curl http://10.0.20.190:8080/` from the laptop currently times out. Unknown
  whether the homelab is powered down, the laptop is off the NetBird mesh, the ArgoCD app
  is unsynced, or the pod is crashlooping. First task is to disambiguate.
- Laptop declarative wiring is commented out (never switched in).
- The expensive laptop closures (CUDA `torch`/`ctranslate2`, full system toplevel) have
  never been pushed, so the cache is empty for the paths that matter here.
- Homelab CI consumer wiring (flake `nixConfig`, CI Dockerfile `nix.conf`,
  `push_ci_closure_to_attic` job): status unverified; that is the older plan's PR2 and is
  orthogonal to the laptop/CUDA goal.

---

## Remaining Work

### Phase A: Bring the cache online and verify (homelab)

1. Confirm the homelab is up and the laptop is on NetBird (`10.0.20.0/24` reachable).
   `ping 10.0.20.1` (OPNsense) and `ping 10.0.20.190` (atticd LB).
2. ArgoCD: `nix-cache` Application `Synced` + `Healthy`. If not, sync it.
3. Pod ready: `kubectl -n nix-cache get pod` → `atticd-* 1/1 Running`. On crashloop check
   `kubectl -n nix-cache logs deploy/atticd` (usual suspects: HS256 secret malformed,
   MinIO unreachable at `10.0.20.191:9000`, PVC stuck Pending on Longhorn).
4. ESO secrets present: `kubectl -n nix-cache get secret attic-tokens minio-creds-nix-cache`.
5. MinIO bucket exists: `attic-homelab` (see the `mc ls` recipe in the original plan Task 7).
6. Health from laptop: `curl -sv http://10.0.20.190:8080/` returns 404 (expected; not 5xx/timeout).
7. Confirm the `homelab` cache still exists and the pubkey matches base.nix:
   `attic cache info local:homelab` (admin token via `attic-server make-token` using the
   HS256 secret from the password manager). If the cache/DB was lost, recreate with
   `attic cache create local:homelab --public` and update the pubkey in `base.nix` if it changed.

### Phase B: Wire the laptop declaratively (`~/nixos`)

1. Ensure a laptop push token exists (10y TTL, `--pull homelab --push homelab`); store in
   password manager. `attic login homelab http://10.0.20.190:8080 <token>` on the laptop.
2. Uncomment the block in `hosts/laptop/base.nix:307-311`. Verify the pubkey matches
   Phase A step 7. This is the declarative equivalent of editing `~/.config/nix/nix.conf`;
   prefer it so the setting is reproducible and applies to the nix-daemon system-wide.
3. Guard for offline resilience: `extra-substituters` (not `substituters`) so
   `cache.nixos.org` stays primary and a down homelab only means a fetch miss, never a
   hard failure. Confirm nix's default `connect-timeout`/`fallback` behavior degrades
   gracefully; consider `narinfo-cache-negative-ttl` tuning if a down cache slows evals.
4. `just switch` (the cascade guard will pass: this is a config-only delta). Verify:
   `nix store info --store http://10.0.20.190:8080/homelab` succeeds.

### Phase C: Pre-warm the expensive closures (the payoff for today's incident)

1. Build the CUDA packages once on the laptop (or reuse the current store paths):
   `torch`, `ctranslate2`, and anything else from the `ctranslate2-cuda` overlay.
2. Push them plus the full system closure:
   ```
   attic push homelab (nix path-info --recursive /run/current-system)
   attic push homelab (nix path-info --recursive .#nixosConfigurations.laptop.config.system.build.toplevel)
   ```
3. Verify a cache hit: on a throwaway path or after a GC of a specific CUDA path, a rebuild
   should log `copying path '...-torch-...' from 'http://10.0.20.190:8080/homelab'` instead
   of `building '...-torch-...drv'`.
4. Going forward, the next nixpkgs bump that rebuilds `torch`/`ctranslate2` is a one-time
   cost: push once, and every subsequent laptop switch (and any future NixOS host)
   substitutes it.

### Phase D (optional): Auto-push on every switch

Rather than pushing by hand, add a `post-build-hook` (or a systemd path/After hook on the
auto-update services) that runs `attic push homelab <out-paths>` for newly built paths.
Weigh against upload time on the metered/slow path; gate it to run only when the homelab is
reachable. Keep it best-effort (never fail a switch because the push failed).

### Phase E (optional): Homelab CI consumer

If not already done, execute the original plan's PR2 (flake `nixConfig` substituter, CI
image `nix.conf`, `push_ci_closure_to_attic` GitLab job) to drop `build_ci_image` from
~36min to ~5-8min on no-op flake bumps. Independent of the laptop/CUDA goal above.

---

## Operational Notes

- **GC / retention:** weekly cron, 60d `default-retention-period`. The CUDA closures are
  large; if `mc du local/attic-homelab` grows past ~50GB, tighten retention or pin the
  CUDA paths as GC roots so they survive the 60d window (they are the whole point).
- **Offline behavior:** with `extra-substituters`, a down homelab is a soft miss. Keep the
  laptop block behind an easy toggle (it already is: comment/uncomment + switch) so a long
  homelab outage does not drag on evals.
- **Token / HS256 rotation:** push tokens are 10y; rotate by re-issuing and updating the
  password manager + laptop login. Rotating the HS256 server key invalidates all tokens and
  needs an atticd restart (Reloader handles it on the ESO refresh); expect brief cache
  downtime, during which consumers fall back to `cache.nixos.org`.
- **Single point of failure:** single-replica atticd on a single-replica Longhorn PVC +
  single MinIO. Acceptable for a convenience cache (source of truth is nixpkgs + the flake);
  do not treat it as durable storage.

---

## Open Questions / Decisions

- Is the homelab intentionally offline (power/cost) or transiently down? If it is often off,
  the auto-push (Phase D) and heavy reliance are less valuable; the cache is then a
  "when it happens to be up" accelerator, which the `extra-substituters` soft-miss design
  already handles.
- Push scope: full system closure every switch (simple, large) vs just the unfree/overlay
  packages that `cache.nixos.org` will never have (smaller, targeted). Targeted is the
  better default; the toolchain cascade paths get cached by hydra within a day anyway, which
  is exactly what the `~/nixos` cascade guard waits for.

---

## References

- Implementation detail (server internals, two-PR rollout, exact manifests):
  `docs/superpowers/plans/2026-05-28-nix-binary-cache.md`
- Design/rationale: `docs/superpowers/specs/2026-05-28-nix-binary-cache-design.md`
- Laptop hook: `~/nixos/hosts/laptop/base.nix:299-311`
- Cascade guard (the complementary defense): `~/nixos/home/scripts/nix-cascade-guard`,
  wired into `~/nixos/modules/nixos/auto-update.nix` and `~/nixos/justfile`.
