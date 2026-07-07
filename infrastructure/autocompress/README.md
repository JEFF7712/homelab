# autocompress runner (NOT LIVE: no apps/autocompress.yaml exists yet)

Benchmark grinder for the Hutter Prize record attempt
(https://github.com/JEFF7712/autocompress). Manifests are **untested drafts**
written while the cluster was down (2026-07); ArgoCD ignores this directory
until an Application references it. Validate everything against the live
cluster before creating `apps/autocompress.yaml`.

## Image

Built from the autocompress repo's flake, NOT a Dockerfile, so laptop and
cluster share the exact nixpkgs toolchain pin (binary_bytes is part of the
compression score; a different clang produces non-comparable ledger rows):

```bash
cd ~/projects/autocompress
nix build .#runner-image
podman load < result
podman tag autocompress-runner:0.1.0 docker.io/jeff7712/autocompress-runner:0.1.0
podman push docker.io/jeff7712/autocompress-runner:0.1.0
```

Do not set `AUTOCOMPRESS_MARCH` in the pod: LLVM 17 detects the Zen 1
workers correctly (the override exists for the laptop's Raptor Lake, where
`-march=native` silently drops AVX2).

## Sizing and placement

- Runs on one CLI ar9070 worker (Ryzen V1605B, 16 GB): the only nodes that
  fit the 10 Gi prize RAM cap (the WYSE has 8 GB). `nodeSelector` pins it;
  verify the hostname when the cluster is back.
- `resources.limits.memory: 10Gi` IS the prize envelope
  (`ram_enforcer = "external"` in the ConfigMap). Do not raise it. Tighten
  to `10000000000` (decimal 10 GB) for compliance-grade t2 runs.
- 40Gi Longhorn PVC (enwik9 + tiers + ~25 GB t2 scratch + build tree).
  ppm.temp does heavy random writes: keep the volume single-replica and
  local to the node; SSD wear is the documented cost of mmap_to_disk=true.
- `geekbench5_single_core` in the ConfigMap ships 0 on purpose: the harness
  refuses to run until the score is measured ON the node.

## Go-live checklist (cluster back ~2026-09)

1. Build, tag, push the image (above); pin a digest in deployment.yaml.
2. Measure Geekbench 5 single-core on the target worker; fill the ConfigMap.
   Also measure a t0 codec rate: prize compliance needs
   cmix_hours x T / 70,000 < 1 on the node (it was 0.95 on the fx2-cmix
   authors' Xeon; the laptop fails this ratio, see the autocompress
   research log).
3. Implement runner remote mode in the autocompress repo (fetch + scan
   origin exp/*, publish ledger + logs to a results branch with a deploy
   token) and an entrypoint.sh (clone/update repo under /work, copy
   /etc/autocompress/config.toml, prepare.py, runner.py). Test locally in
   podman with ram_enforcer=external first.
4. Create `apps/autocompress.yaml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: autocompress
  namespace: argocd
spec:
  project: default
  source:
    repoURL: "https://gitlab.com/JEFF7712/homelab.git"
    targetRevision: main
    path: infrastructure/autocompress
    directory:
      recurse: true
  destination:
    server: https://kubernetes.default.svc
    namespace: autocompress
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=false
      - ServerSideApply=true
```

5. First jobs on the node: t0 baseline (sanity-check cpu_seconds against the
   measured T), then t1, then the t2 full-enwik9 baseline the laptop
   deferred (expect ~3-4 days per stage).
