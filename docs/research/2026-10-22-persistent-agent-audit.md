# Persistent agent and Pi worker audit, 2026-10-22

Phase 0 audit of [RFC-001](2026-10-22-persistent-agent-pi-workers-rfc.md).
The RFC is a proposal; this document records what the repository, the live
platform and the upstream projects actually provide, where the RFC is wrong,
and what may proceed.

**Verdict.** Gate A passes only for offline, provider-agnostic controller and
worker-adapter code. Deployment (Phase 1) is blocked on four operator decisions
listed under [Escalations](#escalations): the Codex OAuth path for Pi, the
Claude worker runtime, new credentials, and a new in-cluster service. Two RFC
assumptions are false: projectctl is not a task scheduler, and Claude
subscription credentials cannot be used inside Pi or Letta.

This was a read-only audit. No pushes beyond this review branch, API writes,
deployments, logins, credential reads or model calls were made. Credential
files were inspected only for key names and auth type, never values.

## Scope and evidence

Source snapshot: `origin/main` at `12ffa9c3` (PR 477). Live observations on
2026-10-22: Forgejo `swagger.v1.json` from `git.internal:3000`, `kubectl top
nodes` and node allocatable, `tea pulls list` for open PRs. Version pins came
from the flake's own nixpkgs (`builtins.getFlake ./flake`), npm registry
metadata, and the locally installed `pi` 1.3.0 store path, whose bundled docs
(`libexec/pi/docs/`) are the version-matched protocol reference. The Pi RPC
probe ran `pi --mode rpc --no-session --no-approve` with `get_state`,
`get_available_models`, `get_commands` and an unknown command; no prompt was
sent and no provider was contacted.

## 1. Source of truth

| Concern | Authoritative source | Notes |
| --- | --- | --- |
| NixOS hosts | `flake/hosts/<host>/default.nix`, `flake/modules/` | homelab-04 carries Woodpecker, approvals, projectctl, agent workspaces |
| Kubernetes | `gitops/<app>/`, bound by `gitops/clusters/homelab-01/<app>.yaml` (Flux) | one namespace per app, Pod Security per namespace (`docs/runbooks/pod-security.md`) |
| Forgejo | `flake/modules/forgejo.nix` on nas-01, SQLite at `/persist/forgejo/data/forgejo.db` | live 15.0.10 |
| Woodpecker | `flake/modules/woodpecker.nix` on homelab-04, patched | 3.19.0 in the flake (runbooks still cite the 3.18 user model) |
| projectctl | `scripts/projectctl/`, `config/projectctl/`, `flake/modules/projectctl.nix` | see section 2 |
| Secrets | SOPS (`.sops.yaml`, `gitops/secrets/`, `secrets/`), ESO for cluster | sops-nix on hosts |
| Approvals | `scripts/approvals/`, `flake/modules/approvals.nix` | WebAuthn passkey, ntfy push |
| Delivery | `just ship` (`docs/runbooks/ship.md`) | standing authorization for routine routes |
| Agent tasks (session-local) | `scripts/agent/`, `.agent-state/` | per-checkout records, not a shared queue |

Open PRs on 2026-10-22 (470, 394 and renovate bumps) touch none of these
areas. No existing Letta, Pi, OpenHands or orchestrator code exists in the
repository.

## 2. Existing functionality and reuse

**projectctl is not a task controller.** It onboards an existing repository
into Forgejo and Woodpecker CI through four fixed capabilities
(`repository`, `ci_enrollment`, `ci_validation`, `branch_protection` in
`scripts/projectctl/models.py`). Its SQLite journal
(`/persist/projectctl/journal.sqlite`) holds `requests`, `operations`,
`attempts`, `allocations` and `authorization_contexts`. It has no notion of
free-form tasks, workers, questions, model providers or chat. RFC open
question 1 is answered: projectctl cannot be reused as the scheduler. Its
patterns are reusable and the controller should copy them:

- idempotency by `(principal, submission_key)` uniqueness;
- partial unique indexes that allow one active request per lock;
- attempts committed before external writes, and allocation receipts that
  prove ownership after a lost response;
- authorization rechecked before each write, plan digests invalidated by
  policy change;
- `synchronous=FULL` SQLite with `projectctl-operator backup`.

projectctl remains the privileged boundary for RFC Phase 5 (onboarding a
worker-created repository into CI), unchanged.

**Approvals dashboard** (`scripts/approvals/`) already implements the RFC's
approval semantics for infrastructure: requests bound to an exact digest
(grants hash each changed path and blob), six-hour expiry, single-use
WebAuthn challenge, ntfy notification, at-most-once trigger. The controller
must not build a second infrastructure approval system. Worker-level
approvals (for example "may this task open a PR against repo X") are a
different, lower-privilege object and belong in the controller; any action
that reaches CI deployment stays on the dashboard.

**pod-agent** (`gitops/pod-agent/`, `docs/runbooks/pod-agent.md`) is the only
production precedent for subscription CLI auth in the cluster. Evidence:

- Codex CLI with a copied `auth.json` works headless in a pod (smoke green
  2026-09-29). Containment needed `POD_AGENT_PROVIDER_CONTAINMENT=best-effort`
  because bubblewrap cannot create user namespaces in an unprivileged pod.
- Claude and Cursor auth "did not transfer (file copy is not honored /
  machine-bound)". The cluster runs Codex-only.
- Antigravity works through a remote OAuth flow plus a native keyring broker.
- It already holds a Telegram bot token and chat ID for business
  notifications.

**Agent workspaces** (`docs/runbooks/agent-workspaces.md`) are libvirt VMs on
homelab-04 with per-workspace nftables isolation, accepted under load on
2026-09-14. The inventory is empty; identity, browser, credential, restore and
HA gates still block enrollment. They are the strongest proven isolation, but
are interactive per-user machines, not disposable per-task runtimes.

**Notifications.** ntfy runs in `gitops/observability/ntfy/`. There is no
shared Telegram bot.

**Forgejo 15.0.10 API** (live swagger): issues, comments, labels and repository
webhooks exist. There is no project or board endpoint, so RFC section 9's
"issue board" reply path must be issue comments, not a board column.

**Woodpecker** runs PR pipelines for enrolled repositories; enrollment of a new
repository is exactly what projectctl's routine scope automates.

### Reuse versus build

| Need | Decision | Source |
| --- | --- | --- |
| Conversation, persona, memory, Telegram | Reuse Letta Code App Server (upstream) | section 4 |
| Task, attempt, question, approval, outbox state | Build a small controller; copy projectctl journal patterns | `scripts/projectctl/journal.py` |
| Durable store | SQLite on a node-pinned volume, single writer | precedent: projectctl, approvals, pod-agent, Forgejo |
| Worker protocol | Reuse Pi RPC (pinned 1.3.0) | section 5 |
| Worker isolation | Kubernetes Job per attempt in a dedicated namespace | section 3 |
| Issues and PRs | Reuse Forgejo API with a dedicated machine user | `docs/agent-workflow.md` homelab-agent precedent |
| CI evidence | Reuse Woodpecker status on the PR head SHA | |
| CI enrollment of new repos | Reuse projectctl routine scope | `config/projectctl/callers.json` |
| Infrastructure approvals | Reuse approvals dashboard | `docs/runbooks/approvals.md` |
| Push notifications outside Telegram | Reuse ntfy | |
| Message broker, Temporal, operator, Postgres | Do not add | no demonstrated need |
| OpenHands | Do not deploy | Pi covers the worker role |

PostgreSQL: there is no shared cluster. CNPG runs one cluster per application
(immich, analytics). A new CNPG cluster for a single-writer controller with
one worker at a time would add backup and failover surface without benefit.
Revisit only if Phase 4 needs multiple controller replicas.

## 3. Placement and resources

Observed 2026-10-22:

| Node | Allocatable CPU / memory | Memory in use | Notes |
| --- | --- | --- | --- |
| homelab-01 | 2.5 / 12.0 GiB | 35% | control plane, small |
| homelab-02 | 7 / 12.6 GiB | 61% | |
| homelab-03 | 7 / 12.6 GiB | 65% | |
| homelab-04 | 16.5 / 17.5 GiB | 62% | also CI server, workspace slice reserved |
| homelab-05 | 3.5 / 8.6 GiB | 215% of allocatable | overcommitted, exclude |

Decision: control plane (Letta App Server plus controller in one pod, so the
external-tool WebSocket stays on loopback and needs no auth listener) pinned
to homelab-02 or homelab-03 with a node-local volume, following the pod-agent
single-writer pattern. Workers run as Kubernetes Jobs, one per attempt, with
CPU, memory and `activeDeadlineSeconds` limits, an `emptyDir` workspace, no
service account token, `restricted` Pod Security, and a NetworkPolicy that
allows only DNS, Forgejo and the provider endpoint. Pi has no sandbox of its
own (Pi `docs/security.md`), so the pod is the boundary; it never mounts the
controller volume or any host path. Agent-workspace VMs are kept as the later
option for tasks needing a full machine.

## 4. Letta feasibility

- Package `@letta-ai/letta-code` 0.34.20 (npm, 2026-10-21, Apache-2.0,
  `github.com/letta-ai/letta-code`). Not in the flake's nixpkgs; packaging is
  required (container image through the local registry supply lane).
- App Server: `letta server --listen <url>`, one WebSocket at `/ws`.
  Loopback listeners need no auth; non-loopback refuses to start without
  `--ws-auth capability-token|signed-bearer-token`.
- Local backend state: `~/.letta/lc-local-backend`. MemFS is git-versioned.
  Restart durability is not documented; it must be measured (Gate B).
- External tools are connection-scoped: registered in `runtime_start`,
  unregistered on disconnect, restored by sending `runtime_start` again.
  Calls arrive as `external_tool_call_request`; reply with
  `external_tool_call_response`. Timeouts and in-flight behavior on disconnect
  are undocumented, so every tool must be idempotent and keyed by
  `tool_call_id`.
- If the owning client disconnects, App Server cancels the active turn.
- Telegram: built-in channel, long-polling (no inbound exposure). DM policy
  `pairing` (default), `allowlist` or `open`. Routes in
  `~/.letta/channels/telegram/routing.yaml`. The bot token is stored in
  plaintext `~/.letta/channels/telegram/accounts.json`, so the volume holding
  it is a secret store. Use `allowlist` with exactly one user ID, never
  `open`.
- Supervisor model: Letta supports ChatGPT Plus/Pro (Codex) as a coding-plan
  connection through device code. It does not list Claude subscriptions.

## 5. Pi feasibility

- Package moved: `@mariozechner/pi-coding-agent` stopped at 0.73.1 (May 2026);
  the active package is `@earendil-works/pi-coding-agent` (1.4.0 on
  2026-10-21, MIT, `github.com/earendil-works/pi`). The flake's nixpkgs ships
  `pi-coding-agent` 1.3.0 (CHANGELOG dated 2026-10-15). Pin 1.3.0 from nixpkgs.
- RPC verified against the 1.3.0 docs and a live probe: `pi --mode rpc`,
  LF-delimited JSON (split only on `\n`, not U+2028/2029), responses
  `{"type":"response","id","command","success","data"|"error"}`, unknown
  commands return `success:false` with `Unknown command: <name>`.
- Prompt response `data.disposition` is `started`, `queued` or `handled`.
  Completion is `agent_settled` (`{"aborted": bool}`), not `agent_end`.
- Extension UI: dialog methods `select`, `confirm`, `input`, `editor` emit
  `extension_ui_request` and block until an `extension_ui_response` with the
  same `id`. Omit `timeout`, or Pi auto-resolves the dialog. The model can
  only reach these dialogs through a tool, and the bundled `question.ts`
  example refuses to run outside the TUI, so the worker needs a small
  first-party `ask_supervisor` extension (loaded with `--extension`) whose
  tool calls `ctx.ui.select` or `ctx.ui.input`.
- Sessions default to `~/.pi/agent/sessions/`; `--session-dir` overrides.
- Project trust: in RPC mode `defaultProjectTrust: "ask"` skips
  project-supplied extensions, but `AGENTS.md`/`CLAUDE.md` load regardless.
  Workers must run with `--no-approve` and treat repository instructions as
  untrusted.

## 6. Subscription and auth feasibility

| Provider | Path | Status | Evidence |
| --- | --- | --- | --- |
| Codex | Codex CLI, ChatGPT sign-in, headless | **verified** documented | learn.chatgpt.com/docs/auth: device-code login, tokens auto-refresh, "access tokens are for trusted scripts, schedulers, and private CI runners"; API keys recommended for CI. pod-agent runs it |
| Codex | Pi `openai-codex` OAuth provider | **unknown** | No OpenAI primary text permits or forbids third-party harnesses on plan credentials. Requires operator acceptance |
| Codex | Letta supervisor via device code | **unknown** terms, supported by Letta | docs.letta.com/configuration/models |
| Claude | Pi or Letta with Pro/Max OAuth | **unsupported** (prohibited) | code.claude.com/docs/en/legal-and-compliance: OAuth "designed to support ordinary use of Claude Code and other native Anthropic applications"; developers may not "route requests through Free, Pro, or Max plan credentials" |
| Claude | Unmodified Claude Code CLI with `claude setup-token` | **verified** documented | code.claude.com/docs/en/authentication: one-year `CLAUDE_CODE_OAUTH_TOKEN` "for CI pipelines and scripts"; usage limits "assume ordinary, individual usage" |
| Claude | API key | verified, metered | requires operator billing approval |
| Antigravity | remote OAuth plus keyring broker | verified in pod-agent | lower priority |
| Muse | | unknown | not investigated |

Quota accounting (included versus metered) for Codex sign-in is not stated in
OpenAI's auth page. Mark **unknown**; measure on a canary.

Refresh-token hazard: Codex refresh tokens rotate. Copying the laptop's
`auth.json` into workers (the pod-agent method) gives several consumers one
refresh chain; the first refresh can invalidate the others, including
pod-agent's. Each consumer needs its own device-code login.

The laptop's `~/.pi/agent/auth.json` currently holds an `anthropic` OAuth
entry. Under the terms above, that use is not permitted; this audit does not
change it.

## 7. Forgejo and CI

- Idempotent issue creation: Forgejo has no client idempotency key. The
  controller records an outbox row first, embeds a marker
  (`<!-- orchestrator:task=<id> -->`) in the body, and before retrying lists
  issues by label and searches for the marker. Same for PRs, keyed by branch
  name `agent/<task-id>/<attempt>`; Forgejo already rejects a second open PR
  for the same head and base.
- Completion evidence: commit status on the PR head SHA from Woodpecker, read
  by the controller, never reported by the worker.
- Worker identity: a new machine user scoped to the target repositories,
  token scopes `read:user`, `read:repository`, `write:repository`,
  `read:issue`, `write:issue` (the homelab-agent recipe). The worker never
  receives homelab-agent's token or merge rights; branch protection blocks
  direct pushes to default branches.
- Webhooks: `ALLOWED_HOST_LIST` permits `*.internal` and RFC1918, so an
  in-cluster controller can receive issue-comment webhooks without new
  exposure. Polling is an acceptable MVP alternative.

## 8. Threats and recovery

| Threat | Control |
| --- | --- |
| Unauthorized Telegram sender | Letta `allowlist` with one user ID; controller also checks `principal_id` on every tool call |
| Prompt injection through repo, issue or tool output | Controller validates every tool call against task scope; worker has no credential beyond its scoped Forgejo token and provider login; no cluster or NAS access |
| Credential exfiltration from worker | Provider login and Forgejo token are the only secrets in the pod; per-worker identities so revocation is targeted; egress NetworkPolicy |
| Extension supply chain | Only the reviewed first-party `ask_supervisor` extension; Pi pinned from nixpkgs |
| Compromised worker | Pod has no service account token, no host mounts; output is evidence only, completion needs CI status read by the controller |
| Task or PR duplication | Idempotency keys, transactional claim with lease, outbox with markers |
| Stale questions or approvals | Revision check, expiry, approvals bound to action digest |
| Controller or Letta loss | SQLite backup plus Letta volume backup; restore test is a Gate B criterion. Workers hold no authority without the controller |
| Refresh-token chain breakage | Separate device-code logins per consumer |

## 9. Design delta

Corrections to the RFC:

1. Drop "reuse projectctl for scheduling". Build a small controller; use
   projectctl only for CI onboarding and later privileged work.
2. Claude cannot be a Pi provider. The Claude lane is a second worker adapter
   that drives the unmodified Claude Code CLI (`claude -p --output-format
   stream-json`) with a `setup-token` credential, or the API with approved
   billing. The `WorkerAdapter` interface already allows this.
3. Use SQLite, not Postgres.
4. Replace "issue board" replies with issue comments.
5. Co-locate Letta and the controller so the tool socket stays on loopback.
6. Pin Pi 1.3.0 from nixpkgs; the npm package name in older docs is stale.

### PR sequence

| PR | Scope | Needs |
| --- | --- | --- |
| 1 | This audit and the RFC | nothing |
| 2 | Controller core: schema, transition table, idempotent create, lease claim, events, questions, approvals, outbox; protocol doc; tests | nothing (offline) |
| 3 | Pi RPC worker adapter and `ask_supervisor` extension, tested against a fake Pi process | nothing (offline) |
| 4 | Controller HTTP/tool surface and Letta external-tool bridge, tested against a fake App Server | nothing (offline) |
| 5 | Images: Letta App Server, controller, Pi worker through the registry supply lane | operator decision on Letta packaging |
| 6 | GitOps namespace, PVC, NetworkPolicies, Job template, ExternalSecrets | new credentials, new service approval |
| 7 | Forgejo issue/PR/CI integration against a disposable repository | worker Forgejo identity, projectctl routine scope for the test repo |
| 8 | Telegram question and approval flow | Gate C passed |

### Rollback

PRs 1 to 4 add files only; revert the merge. PR 6 rollback: suspend the Flux
Kustomization, scale the control plane to zero, delete running Jobs; the
volume is retained. Revoke the worker Forgejo token and provider logins
independently of the host generation. Nothing in this plan changes Forgejo,
Woodpecker, projectctl or approvals configuration.

## Escalations

Decisions that only the operator can make before Phase 1:

1. **Codex in Pi.** Accept the unknown terms status of Pi's `openai-codex`
   OAuth provider, or use Codex CLI as the first worker runtime (documented
   path) and keep Pi for later.
2. **Claude lane.** Approve the Claude Code CLI adapter with a
   `claude setup-token` credential (subscription, "ordinary, individual usage"
   caveat for unattended work), or API billing, or defer Claude.
3. **New credentials.** A Telegram bot for the supervisor (not pod-agent's),
   separate device-code logins for the supervisor and the worker, and a
   dedicated Forgejo worker identity.
4. **New service.** A `persistent-agent` namespace on homelab-02/03 with a
   node-local volume. Not externally exposed.
5. **Disposable test repository** and its addition to
   `config/projectctl/callers.json` (protected review).
