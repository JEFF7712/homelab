# Media Music GitOps Stabilization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove plaintext media credentials from Git and stabilize Lidarr/slskd behavior through declarative ArgoCD-managed configuration.

**Architecture:** External Secrets Operator remains the single credential ingress from `gitlab-backend` into namespace `media`. `infrastructure/media` consumes those generated Kubernetes Secrets via `secretKeyRef` and Secret volume mounts. Lidarr runtime automation is pinned to a GitOps-managed `extended.conf` so import cleanup/automation behavior is deterministic.

**Tech Stack:** Kubernetes, ArgoCD, External Secrets Operator, Lidarr, slskd, GitLab secret backend

---

## Preconditions (Out of Repo)

Create or verify these backend keys exist before syncing manifests:

- `LIDARR_DEEZER_ARL`
- `SLSKD_API_KEY` (must match the API key configured in Lidarr's slskd download client)
- `SLSKD_USERNAME`
- `SLSKD_PASSWORD`

If key names must differ, update `remoteRef.key` values in Tasks 1 and 2 accordingly.

---

### Task 1: Add Lidarr ExternalSecret

**Files:**
- Create: `secrets/lidarr.yaml`
- Reference pattern: `secrets/navispot.yaml`

**Step 1: Write failing security check (documents current leak)**

Run:

```bash
grep -n "DEEZER_ARL" infrastructure/media/lidarr.yaml
```

Expected: one match showing literal `value:` in `infrastructure/media/lidarr.yaml`.

**Step 2: Create `secrets/lidarr.yaml`**

Use this manifest:

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: lidarr-secret-eso
  namespace: media
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: gitlab-backend
    kind: ClusterSecretStore
  target:
    name: lidarr-secret
    creationPolicy: Owner
    template:
      type: Opaque
  data:
    - secretKey: DEEZER_ARL
      remoteRef:
        key: LIDARR_DEEZER_ARL
```

**Step 3: Validate manifest shape**

Run:

```bash
kubectl apply --dry-run=client -f secrets/lidarr.yaml
```

Expected: `externalsecret.external-secrets.io/lidarr-secret-eso configured (dry run)`.

**Step 4: Commit**

```bash
git add secrets/lidarr.yaml
git commit -m "chore(secrets): add lidarr deezer arl external secret"
```

---

### Task 2: Add slskd Seed Config ExternalSecret

**Files:**
- Create: `secrets/slskd-seed.yaml`
- Reference pattern: `secrets/immich.yaml`

**Step 1: Confirm current plaintext source (failing check)**

Run:

```bash
grep -n "key:\|username:\|password:" infrastructure/media/slskd.yaml
```

Expected: matches under the `slskd-seed-config` ConfigMap.

**Step 2: Create `secrets/slskd-seed.yaml` with templated `slskd.yml`**

Use this manifest:

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: slskd-seed-secret-eso
  namespace: media
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: gitlab-backend
    kind: ClusterSecretStore
  target:
    name: slskd-seed-secret
    creationPolicy: Owner
    template:
      type: Opaque
      data:
        slskd.yml: |
          directories:
            downloads: /data/downloads/soulseek
          shares:
            directories:
              - /data/music
            filters:
              - \.ini$
              - Thumbs.db$
              - \.DS_Store$
          web:
            authentication:
              api_keys:
                my_api_key:
                  key: "{{ .slskd_api_key }}"
                  role: administrator
                  cidr: 0.0.0.0/0,::/0
          soulseek:
            address: vps.slsknet.org
            port: 2271
            username: "{{ .slskd_username }}"
            password: "{{ .slskd_password }}"
            description: |
              A slskd user. https://github.com/slskd/slskd
            listen_ip_address: 0.0.0.0
            listen_port: 50300
  data:
    - secretKey: slskd_api_key
      remoteRef:
        key: SLSKD_API_KEY
    - secretKey: slskd_username
      remoteRef:
        key: SLSKD_USERNAME
    - secretKey: slskd_password
      remoteRef:
        key: SLSKD_PASSWORD
```

**Step 3: Validate manifest shape**

Run:

```bash
kubectl apply --dry-run=client -f secrets/slskd-seed.yaml
```

Expected: `externalsecret.external-secrets.io/slskd-seed-secret-eso configured (dry run)`.

**Step 4: Commit**

```bash
git add secrets/slskd-seed.yaml
git commit -m "chore(secrets): add slskd seed config external secret"
```

---

### Task 3: Rewire Lidarr Deployment to Secret

**Files:**
- Modify: `infrastructure/media/lidarr.yaml`

**Step 1: Replace hardcoded `DEEZER_ARL` env value**

Change:

```yaml
- name: DEEZER_ARL
  value: "<long token>"
```

To:

```yaml
- name: DEEZER_ARL
  valueFrom:
    secretKeyRef:
      name: lidarr-secret
      key: DEEZER_ARL
```

**Step 2: Verify plaintext removed**

Run:

```bash
grep -n "ea8d49952bb6\|DEEZER_ARL" infrastructure/media/lidarr.yaml
```

Expected: only `DEEZER_ARL` key name remains, no literal token.

**Step 3: Validate manifest shape**

Run:

```bash
kubectl apply --dry-run=client -f infrastructure/media/lidarr.yaml
```

Expected: resource dry-run output without schema errors.

**Step 4: Commit**

```bash
git add infrastructure/media/lidarr.yaml
git commit -m "refactor(media): source lidarr deezer token from secret"
```

---

### Task 4: Rewire slskd Seed Volume to Secret and Remove Inline ConfigMap

**Files:**
- Modify: `infrastructure/media/slskd.yaml`

**Step 1: Switch `seed-config-vol` source from ConfigMap to Secret**

Change volume block to:

```yaml
- name: seed-config-vol
  secret:
    secretName: slskd-seed-secret
```

Keep initContainer copy path unchanged (`/seed/slskd.yml` -> `/config/slskd.yml`).

**Step 2: Remove `ConfigMap` named `slskd-seed-config` from this file**

Delete the entire trailing ConfigMap resource that currently embeds API key and Soulseek credentials.

**Step 3: Validate plaintext removed**

Run:

```bash
grep -n "my_api_key\|username:\|password:" infrastructure/media/slskd.yaml
```

Expected: no matches from seeded credentials block.

**Step 4: Validate manifest shape**

Run:

```bash
kubectl apply --dry-run=client -f infrastructure/media/slskd.yaml
```

Expected: resource dry-run output without schema errors.

**Step 5: Commit**

```bash
git add infrastructure/media/slskd.yaml
git commit -m "refactor(media): load slskd seed config from secret"
```

---

### Task 5: GitOps-Manage Lidarr Extended Runtime Settings

**Files:**
- Create: `infrastructure/media/lidarr-extended-config.yaml`
- Modify: `infrastructure/media/lidarr.yaml`

**Step 1: Create `infrastructure/media/lidarr-extended-config.yaml`**

Create a ConfigMap containing `extended.conf` with these minimum stable overrides:

- `enableAudio="false"`
- `enableVideo="false"`
- `enableQueueCleaner="false"`
- `enableChangeCategory="false"`

Keep `lidarrUrl` and `lidarrAPI` out of this ConfigMap if they are already persisted in `/config`; avoid hardcoding API keys in Git.

**Step 2: Mount this config into Lidarr**

In `infrastructure/media/lidarr.yaml`:

- add volume for `lidarr-extended-config`
- add volumeMount with `subPath: extended.conf` to `/config/extended.conf`

**Step 3: Validate manifests**

Run:

```bash
kubectl apply --dry-run=client -f infrastructure/media/lidarr-extended-config.yaml
kubectl apply --dry-run=client -f infrastructure/media/lidarr.yaml
```

Expected: both dry runs succeed.

**Step 4: Commit**

```bash
git add infrastructure/media/lidarr-extended-config.yaml infrastructure/media/lidarr.yaml
git commit -m "chore(media): manage lidarr extended automation settings via gitops"
```

---

### Task 6: ArgoCD Rollout and Verification

**Files:**
- Verify: `apps/secrets.yaml`
- Verify: `apps/media.yaml`

**Step 1: Sync `secrets` application first**

Run (pick one method):

```bash
argocd app sync secrets
argocd app wait secrets --health --sync --timeout 180
```

Or (if `argocd` CLI is unavailable):

```bash
kubectl -n argocd annotate application secrets argocd.argoproj.io/refresh=hard --overwrite
kubectl -n argocd wait application secrets --for=jsonpath='{.status.health.status}'=Healthy --timeout=180s
kubectl -n argocd wait application secrets --for=jsonpath='{.status.sync.status}'=Synced --timeout=180s
```

Expected: `Synced` and `Healthy`.

**Step 2: Confirm generated secrets exist**

Run:

```bash
kubectl -n media get secret lidarr-secret slskd-seed-secret
```

Expected: both secrets listed.

**Step 3: Sync `media-stack` application**

Run (pick one method):

```bash
argocd app sync media-stack
argocd app wait media-stack --health --sync --timeout 300
```

Or (if `argocd` CLI is unavailable):

```bash
kubectl -n argocd annotate application media-stack argocd.argoproj.io/refresh=hard --overwrite
kubectl -n argocd wait application media-stack --for=jsonpath='{.status.health.status}'=Healthy --timeout=300s
kubectl -n argocd wait application media-stack --for=jsonpath='{.status.sync.status}'=Synced --timeout=300s
```

Expected: `Synced` and `Healthy`.

**Step 4: Runtime verification gates**

Run:

```bash
kubectl -n media get pods
kubectl -n media logs deploy/lidarr --since=30m | grep -E "countryCode parameter missing|DirectoryNotFoundException|UNIQUE constraint failed" || true
```

Expected:

- Lidarr/slskd pods Ready
- No new plaintext-credential related errors
- Error rates trending down over 24h window

**Step 5: Queue health check**

Run your existing Lidarr API queue script and record:

- total records
- `importFailed`
- protocol breakdown for Deezer/Tidal/Soulseek

Expected: baseline captured immediately post-change, then reduced `importFailed` trend in follow-up checks (1h, 6h, 24h).

**Step 6: Commit (if rollout scripts/docs were added)**

```bash
git add <only-new-verification-assets>
git commit -m "docs(media): add rollout verification notes"
```

Skip this step if no repo files changed.

---

## Rollback Plan

1. Revert last media manifest commit(s) and sync `media-stack`.
2. If secret wiring caused startup failure, temporarily restore prior env/config source and re-sync.
3. Keep ExternalSecrets objects in place; they are safe even if currently unused.

---

## Definition of Done

- No plaintext music credentials remain in `infrastructure/media/*.yaml`.
- `secrets` and `media-stack` ArgoCD apps are `Synced/Healthy`.
- Lidarr reads `DEEZER_ARL` from `lidarr-secret` and slskd reads seeded config from `slskd-seed-secret`.
- Lidarr extended automation settings are Git-managed and disable queue-cleanup side effects.
- Post-change queue metrics are captured with a documented before/after trend.
