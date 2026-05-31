# predmarkbot — Plan 2: Homelab Deployment

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Take the locally-runnable bot from Plan 1 and deploy it to the user's homelab Kubernetes cluster via the existing GitOps workflow (ArgoCD watching `~/homelab`), with container image built by the existing GitLab CI (kaniko → Docker Hub), secrets injected by the existing ESO `gitlab-backend` `ClusterSecretStore`, and Renovate auto-bumping the image tag.

**Architecture:** Vendor `~/predmarkbot` into `~/homelab/infrastructure/predmarkbot/src/` via `git subtree` (preserves Plan 1 history; ongoing dev still happens in `~/predmarkbot`, synced via `git subtree pull`). Add a Dockerfile + CI job matching the existing `infrastructure/automation/images/*` pattern. Add a `Namespace`, `Deployment` (1 replica, Recreate), `PersistentVolumeClaim` (Longhorn), `ConfigMap`, `NetworkPolicy`, and `ExternalSecret` under `infrastructure/predmarkbot/` and `secrets/predmarkbot.yaml`. Add an ArgoCD `Application` in `apps/predmarkbot.yaml` with auto-sync + prune + selfHeal. Add Renovate config so the image tag PR-bumps after each new build.

**Tech Stack:** GitLab CI (kaniko + trivy), Docker Hub (`jeff7712/predmarkbot`), External Secrets Operator (ESO) with `gitlab-backend` `ClusterSecretStore`, Longhorn (RWO PVC), Traefik (no ingress needed — bot has no inbound), Stakater Reloader (auto-restart on Secret/ConfigMap change), ArgoCD (auto-sync), Renovate (image tag PRs).

---

## Pre-flight check (assumptions)

Before executing this plan, verify:

```bash
# 1. Plan 1 is complete in ~/predmarkbot and tests pass
cd /home/rupan/predmarkbot
uv run pytest tests/unit -v
# Expected: 65 passed

# 2. The homelab repo is checked out and clean
cd /home/rupan/homelab
git status
# Expected: nothing to commit, working tree clean

# 3. Working Kalshi demo credentials are populated in ~/predmarkbot/credentials.yaml
test -f /home/rupan/predmarkbot/credentials.yaml && \
  yq -r '.KALSHI_DEMO_KEY_ID' /home/rupan/predmarkbot/credentials.yaml | \
  grep -v 00000000 && echo "OK" || echo "FILL credentials.yaml FIRST"

# 4. ESO ClusterSecretStore `gitlab-backend` already exists in cluster
kubectl get clustersecretstore gitlab-backend -o name
# Expected: clustersecretstore.external-secrets.io/gitlab-backend
```

If any check fails, fix before continuing.

---

## File structure

### Files added to `~/homelab/`

```
~/homelab/
├── apps/
│   └── predmarkbot.yaml                                # ArgoCD Application
├── infrastructure/predmarkbot/
│   ├── namespace.yaml                                  # ns: predmarkbot
│   ├── configmap.yaml                                  # config.yaml
│   ├── pvc.yaml                                        # Longhorn RWO 2Gi
│   ├── deployment.yaml                                 # 1 replica, Recreate
│   ├── networkpolicy.yaml                              # egress to Kalshi + ntfy + DNS
│   └── src/                                            # vendored from ~/predmarkbot
│       └── (all files from ~/predmarkbot via git subtree)
└── secrets/
    └── predmarkbot.yaml                                # ExternalSecret
```

### Files added to homelab's `.gitlab-ci.yml`

Three new jobs matching the existing renovate-agent/renovate-dashboard patterns:
- `build_predmarkbot_image` (kaniko build + push)
- `scan_predmarkbot_image` (trivy HIGH/CRITICAL gate)
- (no separate sync job — ArgoCD auto-syncs from the manifests)

### Renovate config updates

Add a `regexManagers` entry in `~/homelab/renovate.json` so the image tag in `deployment.yaml` gets PR-bumped from Docker Hub.

---

## Phase 0 — Vendor predmarkbot into homelab

### Task 0.1: git subtree add

**Files:**
- Create: `~/homelab/infrastructure/predmarkbot/src/` (whole directory tree)

- [ ] **Step 1: Confirm `~/predmarkbot` is clean and on `main`**

```bash
cd /home/rupan/predmarkbot
git status
git log --oneline -1
```

Expected: clean tree, latest commit is the credentials template or later.

- [ ] **Step 2: From `~/homelab`, add the subtree**

```bash
cd /home/rupan/homelab
git subtree add --prefix=infrastructure/predmarkbot/src \
  /home/rupan/predmarkbot main --squash
```

Expected: creates `infrastructure/predmarkbot/src/` populated with the bot source, single squash commit and one merge commit.

- [ ] **Step 3: Verify**

```bash
ls /home/rupan/homelab/infrastructure/predmarkbot/src/
test -f /home/rupan/homelab/infrastructure/predmarkbot/src/pyproject.toml && echo OK
test -d /home/rupan/homelab/infrastructure/predmarkbot/src/src/predmarkbot && echo OK
```

Expected: both echo `OK`.

- [ ] **Step 4: Already committed by subtree — no extra commit needed.** Confirm:

```bash
git -C /home/rupan/homelab log --oneline -3
```

Expected: top entries are `Add 'infrastructure/predmarkbot/src/' from commit '<sha>'` and (if applicable) a squash-merge commit.

---

### Task 0.2: Exclude src/ from homelab's CI lint rules

The vendored source contains Python + test files; we don't want yamllint or ansible-lint to scan them, and we don't want renovate to try managing predmarkbot's `uv.lock` (it has its own Renovate flow in the upstream repo, if any).

**Files:**
- Modify: `~/homelab/.yamllint.yml`
- Modify: `~/homelab/renovate.json`

- [ ] **Step 1: Read `~/homelab/.yamllint.yml`** to see current ignore patterns.

- [ ] **Step 2: Add ignore for vendored predmarkbot YAML** (config.example.yaml etc.) to `.yamllint.yml`:

```yaml
ignore: |
  infrastructure/predmarkbot/src/
```

Append to the existing `ignore:` block — match the file's current format.

- [ ] **Step 3: Add Renovate ignore path to `renovate.json`**

In the top-level object, add (or extend) `ignorePaths`:

```json
"ignorePaths": [
  "infrastructure/predmarkbot/src/**"
]
```

- [ ] **Step 4: Re-run lint locally**

```bash
cd /home/rupan/homelab
nix develop /etc/ci-flake#ci -c yamllint . 2>&1 | head
nix develop /etc/ci-flake#ci -c python -m json.tool renovate.json > /dev/null && echo "JSON OK"
```

Expected: yamllint silent or warnings only; JSON parses.

- [ ] **Step 5: Commit**

```bash
cd /home/rupan/homelab
git add .yamllint.yml renovate.json
git commit -m "chore: exclude vendored predmarkbot/src from yamllint + renovate"
```

---

## Phase 1 — Container image

### Task 1.1: Dockerfile

**Files:**
- Create: `~/homelab/infrastructure/predmarkbot/Dockerfile`

Two-stage build: a `builder` stage that resolves deps with `uv sync --frozen`, then a slim runtime stage with just the venv + source.

- [ ] **Step 1: Write `Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1.7

# ─── Builder stage: resolve deps with uv ───
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

# Install uv. Pinned for reproducibility; bump via Renovate.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /build
COPY src/pyproject.toml src/uv.lock ./
COPY src/src ./src

# Sync the locked deps + install the package into /opt/venv.
RUN uv sync --frozen --no-dev

# ─── Runtime stage: minimal ───
FROM python:3.12-slim AS runtime

# Run as a non-root user. UID 1000 to match Longhorn fsGroup convention.
RUN groupadd --system --gid 1000 predmark \
 && useradd  --system --uid 1000 --gid predmark --home-dir /home/predmark predmark \
 && mkdir -p /home/predmark /var/lib/predmarkbot \
 && chown -R predmark:predmark /home/predmark /var/lib/predmarkbot

# Copy the prepared venv. /opt/venv/bin is added to PATH so `predmarkbot` works.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER predmark
WORKDIR /home/predmark

# Default: long-lived run mode. config is mounted at /etc/predmarkbot/config.yaml.
ENTRYPOINT ["predmarkbot"]
CMD ["run", "--config", "/etc/predmarkbot/config.yaml"]
```

- [ ] **Step 2: Local sanity check (optional but recommended)**

```bash
cd /home/rupan/homelab/infrastructure/predmarkbot
# Build with podman or docker (whichever is on the system) using `src/` as a sibling.
docker build -t predmarkbot:local . 2>&1 | tail -20 || \
  podman build -t predmarkbot:local . 2>&1 | tail -20 || \
  echo "no local docker/podman — kaniko in CI will build it; skipping"
```

Expected: successful build OR clean skip message.

- [ ] **Step 3: Commit**

```bash
cd /home/rupan/homelab
git add infrastructure/predmarkbot/Dockerfile
git commit -m "feat(predmarkbot): multi-stage Dockerfile (python 3.12 + uv, non-root, slim)"
```

---

### Task 1.2: GitLab CI build + scan jobs

**Files:**
- Modify: `~/homelab/.gitlab-ci.yml`

Add two jobs that exactly mirror the existing `build_renovate_agent_image` + `scan_renovate_agent_image` pattern, scoped to `infrastructure/predmarkbot/src/**`, the Dockerfile, and `.gitlab-ci.yml` itself.

- [ ] **Step 1: Locate the existing `build_renovate_agent_image` block in `.gitlab-ci.yml`**

```bash
grep -n "build_renovate_agent_image:" /home/rupan/homelab/.gitlab-ci.yml
```

Note the line number — you'll insert the new block immediately after the corresponding scan job (keep build+scan pairs together).

- [ ] **Step 2: Append these jobs** after the last existing image-build/scan pair (search for the last `scan_*_image:` block in `.gitlab-ci.yml`):

```yaml
build_predmarkbot_image:
  stage: build
  image:
    name: gcr.io/kaniko-project/executor:v1.24.0-debug
    entrypoint: [""]
  rules:
    - if: $CI_COMMIT_BRANCH == "main"
      changes:
        - infrastructure/predmarkbot/Dockerfile
        - infrastructure/predmarkbot/src/**/*
        - .gitlab-ci.yml
  variables:
    IMAGE_TAG: "0.0.$CI_PIPELINE_IID"
    IMAGE_NAME: "jeff7712/predmarkbot"
  script:
    - mkdir -p /kaniko/.docker
    - echo "{\"auths\":{\"https://index.docker.io/v1/\":{\"username\":\"$DOCKERHUB_USERNAME\",\"password\":\"$DOCKERHUB_TOKEN\"}}}" > /kaniko/.docker/config.json
    - /kaniko/executor
      --context "$CI_PROJECT_DIR/infrastructure/predmarkbot"
      --dockerfile "$CI_PROJECT_DIR/infrastructure/predmarkbot/Dockerfile"
      --destination "$IMAGE_NAME:$IMAGE_TAG"

scan_predmarkbot_image:
  stage: image_scan
  image: jeff7712/homelab-ci:$CI_IMAGE_TAG
  needs:
    - build_predmarkbot_image
  allow_failure: true
  rules:
    - if: $CI_COMMIT_BRANCH == "main"
      changes:
        - infrastructure/predmarkbot/Dockerfile
        - infrastructure/predmarkbot/src/**/*
        - .gitlab-ci.yml
  variables:
    IMAGE_TAG: "0.0.$CI_PIPELINE_IID"
    IMAGE_NAME: "jeff7712/predmarkbot"
  script:
    - nix develop /etc/ci-flake#ci -c trivy image --severity HIGH,CRITICAL --exit-code 1 "$IMAGE_NAME:$IMAGE_TAG"
```

- [ ] **Step 3: yamllint check**

```bash
cd /home/rupan/homelab
nix develop /etc/ci-flake#ci -c yamllint .gitlab-ci.yml
```

Expected: clean (or only warnings that match what the existing file already has).

- [ ] **Step 4: Commit**

```bash
cd /home/rupan/homelab
git add .gitlab-ci.yml
git commit -m "ci: build + trivy-scan predmarkbot image via kaniko"
```

- [ ] **Step 5: Push to GitLab (this triggers the build)**

```bash
cd /home/rupan/homelab
git push
```

Watch the pipeline at `https://gitlab.com/JEFF7712/homelab/-/pipelines`. The `build_predmarkbot_image` job should succeed and push `jeff7712/predmarkbot:0.0.<PIPELINE_IID>` to Docker Hub.

- [ ] **Step 6: Confirm image is on Docker Hub**

```bash
# Browser: https://hub.docker.com/r/jeff7712/predmarkbot/tags
# Or via cli:
curl -s "https://hub.docker.com/v2/repositories/jeff7712/predmarkbot/tags/?page_size=5" | jq '.results[].name'
```

Expected: `0.0.<some-iid>` listed.

**Note the exact tag** — you'll pin it into `deployment.yaml` in Task 3.4. Renovate will PR-bump it later.

---

## Phase 2 — Secrets (ESO → GitLab CI variables)

### Task 2.1: Populate GitLab CI variables

This is a manual user step in GitLab's web UI. **No code changes** — the verification commands below confirm it's done.

- [ ] **Step 1: In GitLab UI** (`https://gitlab.com/JEFF7712/homelab/-/settings/ci_cd → Variables`), add five new variables, each "Masked" and **NOT** protected (so non-default-branch builds can still use them if needed):

| Variable name | Type | Value source |
|---|---|---|
| `KALSHI_DEMO_KEY_ID` | Variable | `yq -r '.KALSHI_DEMO_KEY_ID' ~/predmarkbot/credentials.yaml` |
| `KALSHI_DEMO_PRIVATE_KEY` | **File** | `yq -r '.KALSHI_DEMO_PRIVATE_KEY' ~/predmarkbot/credentials.yaml` (paste the full PEM block) |
| `KALSHI_PROD_KEY_ID` | Variable | from `credentials.yaml` (or placeholder until prod onboarding) |
| `KALSHI_PROD_PRIVATE_KEY` | **File** | from `credentials.yaml` (or placeholder PEM) |
| `NTFY_TOKEN` | Variable | from `credentials.yaml` |

**Use the actual values from `~/predmarkbot/credentials.yaml`** — don't paste the example template.

The `NTFY_TOKEN` already exists in the homelab project (it's referenced by the `automation-secrets-eso` ExternalSecret). Leave that one alone; predmarkbot can reuse it.

- [ ] **Step 2: Sanity-check via ESO**

ESO refreshes on its own interval, but you can force a refresh later. For now, just confirm the variables exist:

```bash
# In GitLab UI: see all 5 listed under Variables.
# Or via the gitlab CLI if installed:
glab variable list --project JEFF7712/homelab 2>&1 | grep -E 'KALSHI|NTFY'
```

Expected: 4 KALSHI_* + NTFY_TOKEN listed.

- [ ] **Step 3: No commit — this is a UI/state task.** Move on.

---

### Task 2.2: ExternalSecret manifest

**Files:**
- Create: `~/homelab/secrets/predmarkbot.yaml`

Mirror the pattern in `~/homelab/secrets/automation.yaml` (which you read in Phase 0 prep) but scoped to the `predmarkbot` namespace.

- [ ] **Step 1: Write `~/homelab/secrets/predmarkbot.yaml`**

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: predmarkbot-secrets-eso
  namespace: predmarkbot
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: gitlab-backend
    kind: ClusterSecretStore
  target:
    name: predmarkbot-secrets
    creationPolicy: Owner
    template:
      type: Opaque
      data:
        # Kalshi DEMO credentials.
        KALSHI_DEMO_KEY_ID: "{{ .kalshi_demo_key_id }}"
        kalshi-demo-private-key.pem: "{{ .kalshi_demo_private_key }}"
        # Kalshi PROD credentials (unused in v1, present so prod rollover is a config-only change).
        KALSHI_PROD_KEY_ID: "{{ .kalshi_prod_key_id }}"
        kalshi-prod-private-key.pem: "{{ .kalshi_prod_private_key }}"
        # ntfy notification token.
        NTFY_TOKEN: "{{ .ntfy_token }}"
  data:
    - secretKey: kalshi_demo_key_id
      remoteRef:
        key: KALSHI_DEMO_KEY_ID
    - secretKey: kalshi_demo_private_key
      remoteRef:
        key: KALSHI_DEMO_PRIVATE_KEY
    - secretKey: kalshi_prod_key_id
      remoteRef:
        key: KALSHI_PROD_KEY_ID
    - secretKey: kalshi_prod_private_key
      remoteRef:
        key: KALSHI_PROD_PRIVATE_KEY
    - secretKey: ntfy_token
      remoteRef:
        key: NTFY_TOKEN
```

- [ ] **Step 2: yamllint**

```bash
cd /home/rupan/homelab
nix develop /etc/ci-flake#ci -c yamllint secrets/predmarkbot.yaml
```

Expected: clean.

- [ ] **Step 3: Don't commit yet** — Phase 3 will create the namespace this ExternalSecret targets. ESO will fail to sync if the namespace doesn't exist, so we batch the commit at the end of Phase 3.

---

## Phase 3 — Kubernetes manifests

### Task 3.1: Namespace

**Files:**
- Create: `~/homelab/infrastructure/predmarkbot/namespace.yaml`

- [ ] **Step 1: Write the manifest**

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: predmarkbot
  labels:
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/audit: restricted
    pod-security.kubernetes.io/warn: restricted
```

`pod-security.kubernetes.io/enforce: restricted` opts the namespace into Kubernetes' strictest Pod Security Standard — fine for predmarkbot because the Dockerfile already runs non-root.

---

### Task 3.2: ConfigMap (config.yaml)

**Files:**
- Create: `~/homelab/infrastructure/predmarkbot/configmap.yaml`

- [ ] **Step 1: Write the manifest**

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: predmarkbot-config
  namespace: predmarkbot
  annotations:
    reloader.stakater.com/match: "true"
data:
  config.yaml: |
    # predmarkbot runtime config.
    # v1: shadow mode against Kalshi DEMO. Flip to demo (live demo orders)
    # once shadow run is verified; flip to prod only after demo soak.

    mode: shadow

    kalshi:
      api_base_url: https://demo-api.kalshi.co/trade-api/v2
      ws_base_url: wss://demo-api.kalshi.co/trade-api/ws/v2
      key_id_env: KALSHI_DEMO_KEY_ID
      private_key_path: /var/run/secrets/kalshi/kalshi-demo-private-key.pem

    discovery:
      series:
        - KXHIGHNY
      poll_interval_seconds: 300

    feed:
      reconcile_interval_seconds: 60
      ws_reconnect_max_backoff_seconds: 60

    risk:
      min_edge_cents: 1
      max_per_market_dollars: 50
      max_total_exposure_dollars: 200
      max_orders_per_minute: 30
      max_daily_loss_dollars: 25
      max_intent_size: 10

    state:
      db_path: /var/lib/predmarkbot/state.db

    notify:
      ntfy_url: https://ntfy.rupan.dev
      ntfy_topic: predmarkbot
      ntfy_token_env: NTFY_TOKEN
```

The `reloader.stakater.com/match: "true"` annotation tells Stakater Reloader to restart any Deployment that mounts this ConfigMap when its content changes — so editing the ConfigMap and re-syncing via ArgoCD rolls the pod automatically.

---

### Task 3.3: PersistentVolumeClaim

**Files:**
- Create: `~/homelab/infrastructure/predmarkbot/pvc.yaml`

- [ ] **Step 1: Write the manifest**

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: predmarkbot-data
  namespace: predmarkbot
  labels:
    # Opt into the existing Longhorn recurring-job group used elsewhere in the cluster.
    recurring-job.longhorn.io/source: enabled
    recurring-job-group.longhorn.io/app-configs: enabled
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 2Gi
```

This will store `state.db`, `bot.log`, and the `state.db.killed` sentinel.

---

### Task 3.4: Deployment

**Files:**
- Create: `~/homelab/infrastructure/predmarkbot/deployment.yaml`

This is the heaviest manifest. Sets the image (with the tag captured in Task 1.2 step 6), wires the Secret (env vars + key files), wires the ConfigMap, mounts the PVC, runs as non-root with `restricted` Pod Security.

- [ ] **Step 1: Look up the image tag from Task 1.2**

```bash
curl -s "https://hub.docker.com/v2/repositories/jeff7712/predmarkbot/tags/?page_size=1" | \
  jq -r '.results[0].name'
```

Example output: `0.0.1234`. Use this in the next step.

- [ ] **Step 2: Write the manifest** (substitute the tag you noted)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: predmarkbot
  namespace: predmarkbot
  annotations:
    reloader.stakater.com/auto: "true"
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: predmarkbot
  template:
    metadata:
      labels:
        app: predmarkbot
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: predmarkbot
          # renovate: datasource=docker depName=jeff7712/predmarkbot
          image: jeff7712/predmarkbot:0.0.1234
          imagePullPolicy: IfNotPresent
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          env:
            - name: KALSHI_DEMO_KEY_ID
              valueFrom:
                secretKeyRef:
                  name: predmarkbot-secrets
                  key: KALSHI_DEMO_KEY_ID
            - name: KALSHI_PROD_KEY_ID
              valueFrom:
                secretKeyRef:
                  name: predmarkbot-secrets
                  key: KALSHI_PROD_KEY_ID
            - name: NTFY_TOKEN
              valueFrom:
                secretKeyRef:
                  name: predmarkbot-secrets
                  key: NTFY_TOKEN
          volumeMounts:
            - name: config
              mountPath: /etc/predmarkbot
              readOnly: true
            - name: kalshi-keys
              mountPath: /var/run/secrets/kalshi
              readOnly: true
            - name: data
              mountPath: /var/lib/predmarkbot
            - name: tmp
              mountPath: /tmp
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 256Mi
      volumes:
        - name: config
          configMap:
            name: predmarkbot-config
        - name: kalshi-keys
          secret:
            secretName: predmarkbot-secrets
            items:
              - key: kalshi-demo-private-key.pem
                path: kalshi-demo-private-key.pem
                mode: 0400
              - key: kalshi-prod-private-key.pem
                path: kalshi-prod-private-key.pem
                mode: 0400
        - name: data
          persistentVolumeClaim:
            claimName: predmarkbot-data
        - name: tmp
          emptyDir:
            sizeLimit: 50Mi
```

**Why these choices:**
- `readOnlyRootFilesystem: true` + `tmp` emptyDir — defense in depth; Python writes to `/tmp` for pip/uv caches and tempfiles, so an explicit writable mount is needed.
- `runAsNonRoot: true` + `runAsUser: 1000` — matches the `predmark` UID in the Dockerfile.
- Image tag is pinned with a `# renovate:` magic comment — Renovate will create PRs to bump it.
- `Recreate` strategy — never two bots running at once (would race on the PVC + Kalshi orders).

---

### Task 3.5: NetworkPolicy

**Files:**
- Create: `~/homelab/infrastructure/predmarkbot/networkpolicy.yaml`

Restrict egress to Kalshi + ntfy + DNS only. No ingress required.

- [ ] **Step 1: Write the manifest**

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: predmarkbot-egress
  namespace: predmarkbot
spec:
  podSelector:
    matchLabels:
      app: predmarkbot
  policyTypes:
    - Egress
  egress:
    # DNS to kube-dns
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
          podSelector:
            matchLabels:
              k8s-app: kube-dns
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    # All HTTPS to internet (Kalshi demo + prod, ntfy, Cloudflare time)
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.0.0.0/8
              - 172.16.0.0/12
              - 192.168.0.0/16
      ports:
        - protocol: TCP
          port: 443
    # WS to Kalshi (TCP 443 already covered; this is belt-and-suspenders)
```

The egress is intentionally broad (any 443 to public internet) because Kalshi API IPs change. Restricting by hostname requires a CNI-specific policy (Cilium has `CiliumNetworkPolicy` for that). If you want hostname-level egress later, see the Cilium-extension follow-up in the design spec.

- [ ] **Step 2: Commit Phase 3 manifests together (Task 3.1–3.5) plus the Phase 2 ExternalSecret** (it can now sync because the namespace exists):

```bash
cd /home/rupan/homelab
git add infrastructure/predmarkbot/namespace.yaml \
        infrastructure/predmarkbot/configmap.yaml \
        infrastructure/predmarkbot/pvc.yaml \
        infrastructure/predmarkbot/deployment.yaml \
        infrastructure/predmarkbot/networkpolicy.yaml \
        secrets/predmarkbot.yaml
git commit -m "feat(predmarkbot): k8s manifests + ExternalSecret"
```

- [ ] **Step 3: yamllint full repo** (catches any issues before push):

```bash
cd /home/rupan/homelab
nix develop /etc/ci-flake#ci -c yamllint infrastructure/predmarkbot/ secrets/predmarkbot.yaml
```

Expected: clean.

---

## Phase 4 — ArgoCD Application

### Task 4.1: ArgoCD Application manifest

**Files:**
- Create: `~/homelab/apps/predmarkbot.yaml`

Pattern matches `~/homelab/apps/automation.yaml`.

- [ ] **Step 1: Write the manifest**

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: predmarkbot
  namespace: argocd
spec:
  project: default
  source:
    repoURL: "https://gitlab.com/JEFF7712/homelab.git"
    targetRevision: main
    path: infrastructure/predmarkbot
    directory:
      # Exclude the vendored source tree — ArgoCD should not try to apply it as k8s manifests.
      exclude: "src/*"
      recurse: true
  destination:
    server: https://kubernetes.default.svc
    namespace: predmarkbot
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=false   # namespace.yaml is in the path; let it create the ns
      - ServerSideApply=true
```

The `directory.exclude: "src/*"` (with `recurse: true`) tells ArgoCD's plain-directory plugin to walk `infrastructure/predmarkbot/` but skip the `src/` subtree (which contains Python source, not k8s YAML).

- [ ] **Step 2: Commit + push**

```bash
cd /home/rupan/homelab
git add apps/predmarkbot.yaml
git commit -m "feat(argocd): predmarkbot Application with auto-sync + prune"
git push
```

This is the moment of truth — ArgoCD will pick up the new Application on its next reconcile and start syncing.

- [ ] **Step 3: Watch ArgoCD sync**

```bash
# Either via UI: open the ArgoCD UI, find "predmarkbot" Application, watch it sync.
# Or via CLI:
kubectl -n argocd get application predmarkbot -o wide
# Wait up to ~2 min, then:
kubectl -n argocd describe application predmarkbot | tail -30
```

Expected: `Sync Status: Synced`, `Health Status: Healthy`. If it shows `Degraded` or `OutOfSync`, jump to Phase 5 — that section covers debugging.

---

## Phase 5 — Verify first deploy

### Task 5.1: Pod boots cleanly

- [ ] **Step 1: Confirm pod is running**

```bash
kubectl -n predmarkbot get pods -o wide
```

Expected: one pod, status `Running`, restarts: 0 (within first minute).

If the pod is `ImagePullBackOff`: the image tag in `deployment.yaml` doesn't match what Docker Hub has. Re-check Task 1.2 step 6 and 3.4 step 1.

If the pod is `CrashLoopBackOff`: continue to step 2.

- [ ] **Step 2: Check logs**

```bash
kubectl -n predmarkbot logs deployment/predmarkbot --tail=100
```

Expected highlights (in order):
```
... INFO predmarkbot.runner startup
... INFO predmarkbot.discovery discovered N tickers across 1 series
... INFO predmarkbot.feed WS connected, subscribed to N tickers
... INFO predmarkbot.feed (orderbook updates flowing)
```

**Common failures:**
- `ClockSkewError`: the cluster node's clock is too far off. Fix via NTP on the node, or temporarily widen `max_skew_seconds` in `clock.py`.
- `KalshiApiError 401 NOT_FOUND`: the demo key isn't registered, or the ExternalSecret hasn't synced yet. Run `kubectl -n predmarkbot get secret predmarkbot-secrets -o yaml` to confirm the Secret exists and has the expected keys.
- `RuntimeError: kill-switch sentinel present`: a previous run tripped the kill switch and left `/var/lib/predmarkbot/state.db.killed` on the PVC. `kubectl exec` in and delete it (see Task 5.5).

### Task 5.2: ntfy startup notification fires

- [ ] **Step 1: Check your ntfy phone app or the subscriber UI** at `https://ntfy.rupan.dev/predmarkbot`

Expected: a "predmarkbot up" notification with the version, mode (shadow), and N watched markets.

If no notification: check `kubectl -n predmarkbot logs deployment/predmarkbot | grep ntfy` for `ntfy post failed` warnings. The most likely cause is a misconfigured `NTFY_TOKEN` — confirm it matches the token configured on the ntfy server.

### Task 5.3: Shadow mode confirmed — no real orders

- [ ] **Step 1: Wait ~5 minutes** to let the bot see real market data.

- [ ] **Step 2: Exec into the pod and inspect the DB**

```bash
kubectl -n predmarkbot exec -it deployment/predmarkbot -- \
  sqlite3 /var/lib/predmarkbot/state.db \
  "SELECT count(*) FROM orders; SELECT count(*) FROM shadow_intents; SELECT count(*) FROM markets;"
```

Expected:
- `orders` count: **0** (shadow mode must never place real orders — this is load-bearing)
- `shadow_intents` count: ≥ 0 (may be 0 if no arb was detected yet)
- `markets` count: ≥ 1 (MarketDiscovery has populated)

If `orders` count > 0 in shadow mode, **that's a critical bug**. Stop everything, snapshot the DB, file an issue.

### Task 5.4: Run the `status` CLI inside the pod

- [ ] **Step 1: Exec `predmarkbot status`**

```bash
kubectl -n predmarkbot exec -it deployment/predmarkbot -- \
  predmarkbot status --config /etc/predmarkbot/config.yaml
```

Expected:
```
today realized P&L: $+0.00
open exposure:      $0.00
pending orders:     0
submitted orders:   0
```

### Task 5.5: Smoke command inside the pod

- [ ] **Step 1: Run the smoke self-checks**

```bash
kubectl -n predmarkbot exec -it deployment/predmarkbot -- \
  predmarkbot smoke --config /etc/predmarkbot/config.yaml
```

Expected:
```
[OK] clock skew: ...s
[OK] public REST: /series/KXHIGHNY returned shape
[OK] signed REST: /portfolio/balance accepted

all smoke checks passed
```

If signed REST fails: re-verify ESO sync (`kubectl -n predmarkbot get externalsecret predmarkbot-secrets-eso -o yaml`).

- [ ] **Step 2: No commit — this is runtime verification.**

---

## Phase 6 — Operational polish

### Task 6.1: Renovate regex manager for image tag

**Files:**
- Modify: `~/homelab/renovate.json`

Without this, the image tag in `deployment.yaml` stays at whatever you pinned in Task 3.4. With it, Renovate will open a PR whenever Docker Hub has a newer tag.

- [ ] **Step 1: Inspect existing regex managers**

```bash
jq '.regexManagers // []' /home/rupan/homelab/renovate.json
```

Note the format used.

- [ ] **Step 2: Add a regex manager entry**

The plan's existing `# renovate: datasource=docker depName=jeff7712/predmarkbot` comment in `deployment.yaml` (Task 3.4) gives Renovate everything it needs IF the default custom-manager picks up the `# renovate: ...` style. Confirm via the docker_image registry of regex managers; if needed, add explicit `customManagers` block (the newer name for `regexManagers`):

```json
{
  "customManagers": [
    {
      "customType": "regex",
      "fileMatch": [
        "infrastructure/predmarkbot/deployment.yaml"
      ],
      "matchStrings": [
        "# renovate: datasource=(?<datasource>\\S+) depName=(?<depName>\\S+)\\s+image:\\s+\\S+:(?<currentValue>\\S+)"
      ]
    }
  ]
}
```

If the `renovate.json` already has a `customManagers` array, append the new object; don't replace.

- [ ] **Step 3: Validate the JSON**

```bash
nix develop /etc/ci-flake#ci -c python -m json.tool /home/rupan/homelab/renovate.json > /dev/null
```

- [ ] **Step 4: Commit**

```bash
cd /home/rupan/homelab
git add renovate.json
git commit -m "chore(renovate): track jeff7712/predmarkbot image tag"
git push
```

Renovate will open a PR on the next scheduled run if there's a newer tag than what you pinned.

---

### Task 6.2: Document the dev-to-deploy sync workflow

**Files:**
- Create: `~/homelab/infrastructure/predmarkbot/README.md`

Future-you (and future-Claude) will appreciate a one-page operational doc.

- [ ] **Step 1: Write the README**

```markdown
# predmarkbot deployment

Vendored from `~/predmarkbot` via `git subtree` and built/deployed via the
homelab GitLab CI + ArgoCD GitOps loop.

## Update the deployed bot

When you ship a new version of predmarkbot:

```bash
# 1. Develop + commit in the dev repo as usual
cd ~/predmarkbot
git commit -am "..."

# 2. Sync the vendored source in the homelab repo
cd ~/homelab
git subtree pull --prefix=infrastructure/predmarkbot/src \
  ~/predmarkbot main --squash

# 3. Push — CI builds a new image, Renovate PR-bumps the deployment tag
git push
```

## Flip from shadow → demo trading

Edit `infrastructure/predmarkbot/configmap.yaml`, change `mode: shadow` to
`mode: demo`. Commit, push. ArgoCD will sync the ConfigMap; Stakater
Reloader will restart the pod.

## Flip from demo → prod (real money)

Edit `infrastructure/predmarkbot/configmap.yaml`:
- `mode: prod`
- `prod_confirmed: true`
- `kalshi.api_base_url: https://api.elections.kalshi.com/trade-api/v2`
- `kalshi.ws_base_url: wss://api.elections.kalshi.com/trade-api/ws/v2`
- `kalshi.key_id_env: KALSHI_PROD_KEY_ID`
- `kalshi.private_key_path: /var/run/secrets/kalshi/kalshi-prod-private-key.pem`

The bot will refuse to start without `prod_confirmed: true` (config-level safety gate).

## Recover from a kill-switch trip

If the kill switch fires, the pod writes `/var/lib/predmarkbot/state.db.killed`
and refuses to restart. Resolve manually:

```bash
# Inspect the cause first
kubectl -n predmarkbot logs deployment/predmarkbot --previous

# Then delete the sentinel
kubectl -n predmarkbot exec deployment/predmarkbot -- \
  rm /var/lib/predmarkbot/state.db.killed

# Restart
kubectl -n predmarkbot rollout restart deployment/predmarkbot
```
```

- [ ] **Step 2: Commit**

```bash
cd /home/rupan/homelab
git add infrastructure/predmarkbot/README.md
git commit -m "docs(predmarkbot): deployment + ops runbook"
git push
```

---

## Self-review checklist

Before declaring Plan 2 complete:

1. **Image is on Docker Hub** under `jeff7712/predmarkbot` with the expected tag.
2. **ArgoCD `predmarkbot` Application is `Synced` and `Healthy`.**
3. **One pod running, no restarts, logs show WS messages flowing.**
4. **`shadow_intents` table is populating; `orders` table is empty** (shadow mode invariant).
5. **ntfy startup notification arrived.**
6. **Renovate will track the image tag** (verify by checking next scheduled Renovate run).
7. **All five secrets sync from GitLab via ESO** (confirmed by smoke check passing inside the pod).

Known v1 deferrals (consistent with the design spec's Future Work):
- NetworkPolicy egress is broad (any 443) rather than hostname-pinned to Kalshi/ntfy.
- No PodDisruptionBudget — bot is single-replica; PDB has no effect.
- No HorizontalPodAutoscaler — single-replica by design.
- No PrometheusServiceMonitor — observability is via ntfy + logs + `status` CLI for v1.

---

## What's next (post Plan 2)

The natural follow-ons once predmarkbot is running stably in shadow mode for ≥1 week:

- **Flip to demo trading.** Edit `configmap.yaml`, set `mode: demo`. Monitor for a day.
- **Position cache + risk wiring.** Replace the `lambda _t, _s: 0` stubs in `runner.py` with a real position cache that refreshes after each fill. Required before demo flipping yields useful risk-limit behavior.
- **Fill polling loop.** Currently only `Executor.submit` writes orders; v1 has no loop polling `/portfolio/fills` to detect fills. Add this before demo, or rely on Kalshi's WS fill channel (requires extending `kalshi/ws.py`).
- **Reconciliation on startup.** The `_reconcile_orders_on_startup` stub should actually query Kalshi for the live status of any `pending`/`submitted` orders left by a previous run.
- **Strategy expansion** (per design spec Future Work): calendar arb, news-driven entries, light market-making.
- **Backtesting** (per design spec Future Work): replay the `orderbook_snapshots` table against new `Strategy` implementations.

Plan 3 (if/when needed) would cover whichever subset of the above the operator wants to tackle first.
