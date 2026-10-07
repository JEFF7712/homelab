# Home Assistant Configuration and UI Adoption Runbook

This runbook defines operational procedures for managing Home Assistant (HA) configuration in this repository. It provides procedures for both human operators and autonomous agents.

## 1. Core Principles and Ownership Model

Configuration is partitioned into three authoritative tiers:

1. **Git Desired State (`home-assistant/`)**: The accepted declarative source of truth reviewed, validated, and committed to Git.
2. **Live Working State (Home Assistant Instance)**: The active runtime state in Home Assistant, including uncommitted UI experiments, registries, and runtime state.
3. **Verified Baseline (`.agent-state/home-assistant/<instance>/baseline.json`)**: The recorded checkpoint of the last configuration accepted in Git and verified as deployed in Home Assistant.

### Resource Classification

Every Home Assistant resource falls strictly into one of four categories:
- **`git-owned`**: Core configuration (`configuration.yaml`), database/recorder configuration. Owned by Git, deployed via Kubernetes/Flux.
- **`ui-editable`**: Automations, scripts, scenes, storage dashboards, and registries (areas, devices, entities). Authored in Git or UI; both representations remain fully editable in the HA frontend.
- **`observe-only`**: Non-secret integration parameters and config entries. Monitored for drift, but not mutated blindly.
- **`runtime-only` / `unsupported`**: OAuth tokens, credentials, ephemeral traces, entity states, and history. Kept in secure persistent storage and covered by volume/database backups; excluded from declarative source diffs.

---

## 2. Workflow Entrypoints

### Workflow A: Natural-Language Agent Request (Source-First)

When a user asks an agent to make a Home Assistant change (e.g. "Create an automation that turns off living room lights at 11 PM"):

1. **Inspect Fresh Context**:
   Run `just agent-context` and `just ha-diff` to understand live entity identities and confirm there are no unadopted experiments on the target resource.
2. **Translate to Git Desired State**:
   Create or edit the resource file under `home-assistant/` (e.g. `home-assistant/automations/bedtime_lights.yaml`).
   Ensure required fields (`id`, `alias`, `trigger`, `action`) are present.
3. **Validate Offline**:
   Run `just ha-validate`. Confirm schema validity, secret cleanliness, and GitOps synchronization.
4. **Plan Application**:
   Run `just ha-plan --select automation/bedtime_lights`. Inspect generated immutable actions and bindings.
5. **Commit and Deploy**:
   Publish a Forgejo PR, pass Woodpecker validation, and merge through main protection. Run the protected `deploy-home-assistant-dry-run` target from the successful current-main pipeline, then `deploy-home-assistant` from that same parent. The deployment verifies changes against the target cluster and advances the shared cluster baseline (`home-assistant-baseline-<instance>`).
   *Authorized Local Apply*: If authorized for manual execution, run `just ha-apply --select automation/bedtime_lights` after ensuring UI edits are paused. The tool acquires an atomic lock, performs read-before-write validation, verifies readback, and advances the shared and local baseline.
6. **Report Completion**:
   Confirm to the user that the automation is live, active, and editable in the Home Assistant UI.

### Workflow B: UI Experiment Adoption ("Reconcile UI Changes")

When a user experiments in the Home Assistant UI and later requests to "reconcile my changes", "sync UI back to Git", or "adopt my automation":

> [!IMPORTANT]
> In this repository, "reconcile UI changes" **always means adopting live UI modifications into Git source**. It must **never** default to overwriting the UI with older Git state.

1. **Diff Live vs Baseline & Git**:
   Run `just ha-diff`.
   Resources modified in the UI appear with status `[EXPERIMENT]`.
2. **Present Detected Scope**:
   Show the user which resources were added or changed in the UI.
3. **Adopt Selected Resources**:
   Run `just ha-adopt --select <kind>/<key>` (or `--all` if adopting all changes).
   If adopting UI deletions, provide `--allow-delete` to confirm removal of Git source files.
   This updates the local YAML files in `home-assistant/` atomically.
   *Note: Adoption does NOT advance the verified baseline, nor does it stage or commit files.*
4. **Validate Candidate Source**:
   Run `just ha-validate` and `just check-changed`.
5. **Commit and Deploy**:
   Once merged and deployed through the authorized `deploy-home-assistant` target, the verified baseline advances upon deployment verification.

### Revert Workflow (Explicit Live Reset)

If and only if the user explicitly asks to "revert live state to Git" or "discard UI changes":

1. Run `just ha-diff --select <kind>/<key>` to verify the divergence.
2. Run `just ha-revert --select <kind>/<key>`.
   This restores the live resource in Home Assistant to the exact accepted configuration from Git.

---

## 3. Conflict Resolution

A `[CONFLICT]` arises when both Git desired state and Home Assistant live state were modified independently since the last baseline checkpoint ($G \neq B$, $L \neq B$, and $G \neq L$).

When a conflict occurs:
1. Automated deployment and adoption are blocked.
2. Run `python -m scripts.home_assistant --json diff --select <kind>/<key>` to view the baseline, Git, and live payloads.
3. Ask the user for clarification on intended behavior.
4. Update the Git source with the intended resolved candidate, validate with `just ha-validate`, and deploy.

---

## 4. CLI Command Reference and Exit Codes

Tool invocations are exposed through `just ha-*` or `python -m scripts.home_assistant`:

| Recipe / Command | Arguments | Description |
| --- | --- | --- |
| `just ha-inventory` | `[--json]` | Report installed integrations, surface counts, and capability limits |
| `just ha-capture` | `[--json]` | Capture sanitized live snapshot to `.agent-state/` (0700/0600 permissions) |
| `just ha-diff` | `[--select <kind>/<key>] [--json]` | 3-way comparison between Baseline, Git, and Live |
| `just ha-adopt` | `(--select <key> \| --all) [--allow-delete] [--force] [--json]` | Atomically update local Git files from live state |
| `just ha-validate` | `[--sync-gitops] [--json]` | Offline schema, entity reference, GitOps sync, and secret check |
| `just ha-plan` | `[--select <key>] [--json]` | Generate immutable deployment plan bound to target, version, and source hash |
| `just ha-apply` | `[--select <key>] [--plan-file <f>] [-y] [--json]` | Apply plan with atomic lock, renewal, read-before-write, and verification |
| `just ha-verify` | `[--checkpoint] [--bootstrap] [--json]` | Verify live HA matches accepted Git source; advances cluster baseline when `--checkpoint`/`--bootstrap` is passed and working tree is clean |
| `just ha-revert` | `--select <key> [--json]` | Restore live resource to Git accepted configuration |

### Exit Codes

- `0`: Success / Clean (no pending drift on evaluated scope).
- `1`: Drift or Pending Change (UI experiment or Git change detected).
- `2`: Conflict / Invalid (conflict present, schema invalid, or plan stale).
- `3`: Unavailable / Incomplete (network failure, authentication failure, or partial read).

---

## 5. Security and Credentials Boundary

- **No Plaintext Secrets in Desired State**: All sensitive tokens and credentials use the `!secret <secret_name>` tag in YAML, resolved at runtime via Kubernetes ExternalSecrets and SOPS.
- **Access Tokens**: Home Assistant Long-Lived Access Tokens (LLAT) must never be committed to Git. The CLI resolves tokens from `HASS_TOKEN`, `HASS_TOKEN_FILE`, or `.agent-state/home-assistant/token`.
- **CI Deploy Token**: The `deploy_home_assistant` CI jobs read the token from the `HASS_TOKEN` CI/CD variable, falling back to the cluster secret `home-assistant/home-assistant-token` (key `token`). That secret is not managed by GitOps; provision it once with `kubectl create secret -n home-assistant generic home-assistant-token --from-literal=token=<HA LLAT>`. Without it the jobs fail fast with an explicit error instead of an opaque CLI exit code.
- **Pre-Write Secret Scanning**: `just ha-validate` checks candidate configurations against regex patterns for API keys, private keys, JWTs, query parameter credentials, and database URLs. Both `capture` and `adopt` execute preflight secret validation before any filesystem persistence.

---

## 6. Concurrency, Race Safety, and UI Pause

> [!WARNING]
> Home Assistant's REST API and UI WebSocket do not provide server-side transactional compare-and-swap or frontend-level edit locking. During planned mutation application, **UI editing on the target resources must be paused**.

1. **Atomic Local and Cluster Lease Lock**:
   Every apply operation acquires a lease lock using atomic temporary file creation and hard-linking (`os.link`) with restrictive permissions (`0600`), preventing zero-byte lock race windows. Cross-checkout locking is coordinated via the cluster lock ConfigMap `home-assistant-lock-<instance>`. The lock lease is automatically renewed atomically (`os.replace`) during plan execution and released with owner verification upon completion. Stale crashed locks (> 15 minutes) are reclaimed safely.
2. **Read-Before-Write Verification**:
   Immediately before applying each mutation, the planner reads the live resource and validates that its current content hash matches the expected pre-apply hash (`expected_live_hash`). If the UI was edited concurrently, the write is aborted as stale.
3. **Journaling**:
   Every operation records progress (`started`, `applied`, `verified`, `failed`) in `.agent-state/home-assistant/<instance>/journal/<plan_id>.jsonl`.
4. **Shared Authoritative Baseline**:
   In addition to local `.agent-state`, verified baselines are persisted to the Kubernetes cluster ConfigMap `home-assistant-baseline-<instance>` so CI pipelines and multiple checkouts share the authoritative deployment checkpoint with readback hash verification.

---

## 7. Backup and Disaster Recovery

### Scheduling and Node Failure

Home Assistant uses host networking for HomeKit mDNS and Govee LAN control.
Its required node affinity excludes `homelab-05`, where LedFx also binds UDP
port `4002`, but permits scheduling on the other cluster nodes. Both HA's
configuration and Matter data use shared NFS volumes. If LedFx's node pin
changes, update HA's exclusion in the same change.

HA remains a single-replica `Recreate` deployment. During a node outage, an
old pod can remain stuck terminating and block replacement. Confirm the old
HA and Matter processes have stopped, or fence the failed host, before
force-deleting that pod. Shared NFS volumes do not prevent concurrent writers.
After recovery, verify the replacement's node, readiness, public HTTP access,
and HomeKit, Matter, and Govee device behavior.

### Backup Coverage

- **Persistent Configuration Volume (`home-assistant-config`)**: Contains `/config` (`.storage/`, `secrets.yaml`, custom components). Backed up via Kubernetes volume snapshot and restic.
- **PostgreSQL Recorder Database**: Dumped daily via Kubernetes CronJob `gitops/backups/home-assistant-db-dump.yaml` to Cloudflare R2.
- **External Secrets**: Managed in GitLab CI / SOPS.

### Restoration Procedure (New Instance / Disaster Recovery)

1. Deploy Kubernetes manifests (`gitops/clusters/homelab-01/home-assistant.yaml`).
2. Restore the PostgreSQL database from the latest R2 dump.
3. Ensure ExternalSecret creates `home-assistant-secrets`.
4. Restore `/config/.storage/auth` if migrating credentials, or complete onboarding to create an administrative user.
5. Deploy desired configuration from `home-assistant/` using `just ha-apply --all`.
6. Verify operational status with `just ha-verify`.
