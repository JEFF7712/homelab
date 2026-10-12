# Agent Workflow and Context Continuity Specification

Status: historical implementation specification. For current commands and
behavior, use [`docs/agent-workflow.md`](agent-workflow.md) and the source code.
Date: 2026-09-05.

## Objective and Scope

Make this repository efficient for agents to discover, modify, verify, and resume across sessions and agent clients. A new agent should recover the current task without reading an entire conversation or reconstructing infrastructure history.

Implement all work packages below. Preserve existing infrastructure ownership and unrelated work. This specification authorizes repository implementation, not publishing changes or deploying infrastructure. Do not modify `/home/rupan/nixos`; use it as a reference. Do not write to personal agent memory stores.

Success means a fresh session can identify the task, relevant source, pending work, and required checks through a small local context packet. Completion claims must distinguish repository checks from live infrastructure evidence.

## Reference Implementation and Known Gaps

Inspect these NixOS repository files before adapting their patterns:

- `/home/rupan/nixos/AGENT_MAP.md`: task routing and minimum validation.
- `/home/rupan/nixos/justfile`: `agent-context`, `check-changed`, and full checks.
- `/home/rupan/nixos/hooks/`: common implementation and client adapters.
- `/home/rupan/nixos/checks/agent-workflows.bash`: workflow verification.
- `/home/rupan/nixos/docs/agent-self-improvement.md`: improvements triggered by observed friction.

Adapt behavior rather than copying configuration wholesale. Its context recipe can classify a checkout with only untracked files as clean. Its edit hook stages new files automatically. Correct both behaviors here: include untracked state and require explicit task-scoped staging.

At specification time, this repository has a nested development flake, Python unittest checks, GitLab CI, agent skills, and nix-agent connector configuration. It lacks the command and context interface specified here. Rediscover current files and installed client capabilities before implementation; preserve existing adapter configuration.

## Design Principles

1. Keep policy small, navigation specific, and executable behavior centralized.
2. Separate durable decisions, active task state, and timestamped observations.
3. Read narrowly and emit bounded summaries; link to full evidence.
4. Keep startup and routine checks local and free of credentials. Explicit live probes may use existing credentials without printing them.
5. Use one implementation across clients, with thin validated adapters.
6. Make task state portable and explicit. Do not depend on one provider's transcript or memory feature.
7. Test behavioral contracts, including failures and stale state, rather than documentation wording.

## Intended Layout

| Path | Responsibility |
| --- | --- |
| `AGENTS.md` | Compact operating rules and entrypoints |
| `AGENT_MAP.md` | Task routing: inspect, edit, validate, consult runbook |
| `justfile` | Stable human and agent command interface |
| `scripts/agent/` | Shared context, task, check-selection, and diagnostic logic |
| `hooks/` | Small lifecycle entrypoints using shared logic |
| Client configuration directories | Adapters referencing the shared hooks |
| `docs/decisions/` | Durable architecture decisions with rationale |
| `docs/agent-workflow.md` | Operator guide and task lifecycle |
| `docs/agent-tasks/` | Explicitly exported, sanitized task handoffs |
| `.agent-state/` | Ignored, checkout-local tasks, evidence, and transient logs |
| `tests/` | Offline workflow and failure-path tests |

Use typed Python for structured state and selection logic, with thin shell wrappers where necessary. Do not introduce a database, embedding service, daemon, or mandatory external memory platform. Add required tools to the development flake. Keep runtime dependencies minimal.

## Work Package 1: Navigation and Command Interface

Create `AGENT_MAP.md` covering host configuration, networking, storage, k3s, Flux workloads, OPNsense provider resources, reconciliation, secrets, and agent tooling. Each row identifies owning paths, minimum validation, and relevant runbooks. Verify referenced paths and commands actually exist. Add subsystem instructions only where they provide distinct guidance.

Expose the following commands through `just`. Recipe implementation may delegate to one shared CLI. Document exact arguments and JSON schemas in the operator guide.

| Command | Contract |
| --- | --- |
| `just agent-context` | Bounded local startup packet |
| `just doctor` | Local tooling and configuration diagnosis |
| `just check-changed [base]` | Explain and execute checks selected from changed paths |
| `just check` | Full offline repository validation |
| `just fmt` / `just fmt-check` | Apply formatting / check without mutation |
| `just task-new <id>` | Create a task without overwriting existing state |
| `just task-resume <id>` | Inspect checkpoint and identify drift |
| `just task-checkpoint <id>` | Validate and persist structured checkpoint input |
| `just task-export <id>` | Export a sanitized handoff to `docs/agent-tasks/` |
| `just status cluster` / `just status network` | Explicit read-only live diagnostics |

Provide `--json` equivalents through the shared CLI for context, doctor, check selection, task inspection, and status. Normal output is concise; detailed logs are referenced by path. Commands must work with spaces in repository and file paths. Never require interactive prompts for ordinary checks.

## Work Package 2: Context and Task Continuity

### Startup packet

Include repository root, branch or detached HEAD, commit, complete dirty-state summary, task selection, checkpoint drift, and relevant validation entrypoints. Limit default output to 6 KiB, with an explicit truncation indication and links to details. Summarize large changed-file sets by counts rather than silently dropping them.

Run without network requests, Nix evaluation, secret decryption, or cluster probes. Target under one second on a warm local checkout; measure and report actual timing. A missing optional tool must not prevent the agent from starting.

Choose a task through an explicit argument or session-scoped identifier. If none is supplied, list available active tasks briefly. Never silently choose another agent's task or use a shared mutable global active-task pointer.

### Task records

Store one versioned JSON record per task under `.agent-state/tasks/<id>/`. Validate IDs against a documented safe filename pattern and reject path traversal. Required fields:

- Schema version, task ID, objective, status, and acceptance criteria.
- Owning agent/session identity and explicit file ownership boundaries.
- Base commit, checkpoint HEAD, timestamp, and dirty-state fingerprint.
- Decisions and links to durable decision records.
- Completed work, remaining work, unresolved failures, and exact next action.
- Verification records with command, exit status, time, source fingerprint, and evidence path.

Statuses: `active`, `blocked`, and `complete`. A blocked task includes a concrete dependency. Completion includes acceptance evidence or explicitly identifies unresolved acceptance criteria and remains incomplete.

Fingerprint tracked changes and nonignored untracked content without persisting raw file contents. Exclude `.agent-state/` itself. Detect changes made after verification, even if HEAD is unchanged. Do not treat a dirty checkout as proof that all changes belong to this task.

Use atomic writes and per-task locking or revision-based conflict detection. Do not allow silent concurrent overwrite. Preserve the previous valid checkpoint if interrupted or given invalid input. Checkpoint on meaningful milestones, before handoff, and before disruptive operations. Avoid per-tool-call transcripts.

### Resume and transfer

Compare checkpoint identity, HEAD, base availability, and fingerprint with the current checkout. Mark old verification as stale when source state changes. Present drift and the next action; never reset, discard, or automatically apply changes to reconcile it.

Local records survive client restarts but remain checkout-local. For transfer, export a reviewed, sanitized Markdown handoff containing structured task metadata and evidence references. Include instructions to recover uncommitted work: an exported summary does not carry the actual patch. Do not claim cross-machine reproducibility unless required commits or patches are available. Avoid exporting credentials, command output containing secrets, or machine-specific credential paths.

## Work Package 3: Durable Decisions and Evidence

Create short decision records only for consequential choices: problem, decision, rationale, alternatives, consequences, and superseding record when relevant. Do not retroactively invent rationale for existing architecture. Link existing design documents where sufficient.

Store runtime observations under `.agent-state/evidence/`, including observation time, target identity, command or probe identity, source revision/fingerprint when applicable, result, and sanitized details. Runtime evidence is an observation at a time, never an assertion of perpetual health. Startup context may reference the last observation with its age, but must not refresh it automatically.

Document cleanup/export behavior. Do not grow permanent raw transcripts or append session narratives to `AGENTS.md`.

## Work Package 4: Targeted Verification and CI Alignment

Selection must include staged, unstaged, deleted, renamed, and nonignored untracked paths. An optional Git base adds committed changes since the merge base with HEAD. Reject invalid bases clearly. Use robust Git path parsing, deduplicate checks, and show why each check was selected.

Define a maintained mapping:

- Host-specific Nix changes: formatting and affected host evaluation.
- Shared Nix modules: all consuming hosts; use all hosts if dependency inference is uncertain.
- Python reconciliation: Ruff, Pyright, and relevant behavior tests.
- GitOps: YAML lint, render the affected Kustomize/Helm boundary, and schema validation.
- OpenTofu: formatting and validation with backend access disabled for offline checks.
- Agent workflow changes: workflow tests, lint/types, and full offline checks.
- Flake inputs, CI/check infrastructure, and unknown code paths: full checks.
- Documentation-only changes: relevant links, command references, and whitespace checks.

Full checks must be a superset of targeted checks. Use the same entrypoints in CI. Keep live probes outside this gate. Pin or explicitly provision schemas, chart dependencies, and provider dependencies so routine tests do not silently fetch current upstream state. Distinguish initial dependency provisioning from an offline check run. Missing required dependencies are failures with actionable diagnostics, not passing skips.

Do not auto-stage from edit hooks. If Nix requires a new source file in the Git index, identify and explicitly stage only task-owned files when authorized by the task workflow; otherwise report the precise blocker. Do not indiscriminately stage unrelated untracked files.

## Work Package 5: Shared Hooks and Friction Feedback

Inspect supported lifecycle events and payload formats for installed Claude, Codex, and Cursor versions before wiring adapters. Do not assume all clients support identical events. Preserve existing settings and keep hook behavior in shared scripts.

- Session start/resume: inject the bounded context packet once where supported.
- Relevant validation failure: record sanitized check identity, exit code, and evidence location.
- Stop/handoff: issue a bounded reminder when active work lacks a current checkpoint or unresolved validation exists.

Hooks must have timeouts, prevent recursive invocation, and never block session startup because optional context failed. Validation commands themselves must retain nonzero failure status. Unsupported client events use documented explicit command fallbacks. Test payload handling independently and smoke-test actual supported clients; label adapters that are only fixture-tested.

Repeated friction should lead to a small routing, command, diagnostic, or test improvement. Do not automatically promote failed commands into permanent policy, copy raw shell commands containing secrets, or require a closeout essay for clean sessions.

## Work Package 6: Doctor and Live Diagnostics

`doctor` checks command availability, expected repository paths, nested flake selection, adapter configuration, and presence of credential references. It must not display values, decrypt secrets, mutate configuration, or contact hosts by default. Report each result as pass, fail, or unavailable with a concrete remedy.

Live status commands use existing configured targets and authentication. Inspect actual routing and connector configuration; never guess an address, context, API route, or credential source. Report the selected target before results. Bound each probe and total runtime with documented timeouts. Default total budget: 30 seconds, configurable explicitly.

Cluster status should summarize node readiness, Flux reconciliation, and relevant failed workloads. Network status should summarize reachable configured control-plane endpoints and routing/BGP evidence where available. Use existing runbooks and read-only APIs. Report unsupported or inaccessible probes as unknown/unavailable, never healthy. Provide timestamped JSON and concise text output, with nonzero exit for failed or incomplete requested checks.

Do not grant new privileges, deploy agents, mutate live systems, or test write APIs to implement diagnostics. Record environmental blockers precisely while completing independently testable work.

## Acceptance and Verification Matrix

| Scenario | Required result |
| --- | --- |
| Checkout contains only untracked files | Startup reports dirty state |
| No task, one task, multiple tasks | Explicit selection rules, no ambiguous auto-resume |
| Checkpoint then source edit without commit | Resume flags stale verification |
| HEAD changes or base is unavailable | Clear drift report without Git mutation |
| Two writers update one task | Conflict detected; valid state preserved |
| Interrupted/invalid checkpoint write | Previous valid checkpoint remains readable |
| Path includes spaces or unusual Git filename characters | Correct routing and command invocation |
| Changed file is renamed, deleted, staged, or untracked | Included in check selection |
| Unknown source path or shared check configuration changes | Full validation selected |
| No network or credentials | Startup and provisioned offline tests still work |
| Required validation tool missing | Explicit failing diagnostic |
| Live endpoint times out | Bounded failure with target and timestamp |
| Synthetic secret appears in failure input | No secret in logs or exported handoff |
| Unsupported client hook event | Explicit fallback documented |
| Fresh agent receives exported task | Can identify source, pending work, checks, and next action |

Use temporary Git repositories and synthetic fixtures for destructive/error scenarios. No production credentials in tests. Verify commands execute their intended checks, not merely that recipe names appear in files. Measure startup output size and runtime. Run the completed offline suite and inspect the final diff.

## Implementation Sequence and Handoff

1. Reinspect source, client capabilities, and dirty state; record the implementation task and acceptance criteria.
2. Implement command interface, routing map, and pinned tooling.
3. Implement checkpoint schema, persistence, resume/export, and startup packet with tests.
4. Implement targeted/full checks and align local and CI entrypoints.
5. Add shared hooks, doctor, diagnostics, and evidence capture.
6. Exercise recovery and fresh-session handoff; update `AGENTS.md` and the operator guide to reference delivered commands.

Parallel work is optional. If used, assign disjoint ownership for task-state logic, validation logic, and diagnostics. One integrator owns `justfile`, flake dependencies, client configuration, and CI to prevent collisions. Review combined behavior after integration.

Final implementation report must list delivered commands, checks actually run, measured startup cost, adapter runtime verification, live probes performed, and remaining environmental blockers. Do not mark this specification fully implemented while required capabilities are missing. Do not publish or deploy as part of this work.
