# Agent Workflow Operator Guide

The shared CLI is `python -m scripts.agent`; `just` provides stable recipes. Startup and offline checks do not contact infrastructure or decrypt secrets.

## Commands

- `just agent-context [--json] [--task ID]`: bounded startup packet. `AGENT_TASK_ID` is a session-scoped alternative. Without either, active tasks are listed and none is selected.
- `just doctor [--json]`: local tools, paths, nested flake, adapters, and credential references. It never reads credential values.
- `just check-changed [BASE] [--json] [--select-only]`: includes staged, unstaged, renamed, deleted, untracked, and merge-base changes, explains routing, and executes selected checks unless `--select-only` is used. Text output lists the selected checks with reasons, then per-check exit codes with evidence paths. `--json` reports the same selection and results as structured data (`--select-only` reports the selection without executing it).
- `just check`: full offline validation. Run `just provision-check-deps` once when pinned provider or schema caches are absent.
- `just fmt` and `just fmt-check`: apply or verify repository formatting. Validation and formatting run through these `just` recipes; `python -m scripts.agent` exposes only the context, doctor, task, check-selection, and status commands listed by its `--help`.
- `just task-new ID [--json]`: read creation JSON from stdin and refuse overwrite. `just task-new ID --template` prints a blank creation document to fill in and feed back.
- `just task-resume ID [--json]`: report HEAD and fingerprint drift, stale verification, and the next action.
- `just task-checkpoint ID [--json]`: read a complete record plus `expected_revision` from stdin and atomically replace it under a per-task lock.
- `just task-export ID [--replace]`: write a sanitized Markdown handoff under `docs/agent-tasks/`.
- `just status cluster|network [--json] [--timeout SECONDS] [--record]`: bounded read-only live diagnostics. Cluster status reads node readiness, Flux reconciliation, and failed workloads through the current kubectl context. Network status pings configured cluster nodes; the BGP probe is always reported unavailable from local runs because no read-only OPNsense credential path is wired into this command. The default total budget is 30 seconds. `--record` writes sanitized, timestamped evidence under `.agent-state/evidence/`. These commands are never part of startup or offline checks.

## Task schema

Task IDs match `^[a-z0-9][a-z0-9._-]{0,63}$`, excluding `.` and `..`. Records use schema version 1 and contain objective, status, acceptance criteria, owner and session, owned files, base and checkpoint identity, decisions, completed and remaining work, failures, next action, and verification records. Status is `active`, `blocked`, or `complete`. Blocked records need a concrete dependency. Complete records need evidence for every satisfied criterion and no unresolved failures.

Verification records contain command, exit code, time, source fingerprint, evidence path, and stale state. Resume derives staleness without rewriting the record. Task records and runtime evidence live in ignored `.agent-state/` and remain checkout-local.

## Task lifecycle

1. `just task-new <id> --template > /tmp/<id>.json`, then fill in objective, acceptance criteria, owned files, and next action.
2. `just task-new <id> < /tmp/<id>.json` to create the task. Creation refuses to overwrite an existing task and requires a committed HEAD.
3. Do the work, keeping edits inside the owned files.
4. `just task-resume <id> --json` to inspect HEAD and fingerprint drift plus stale verification. Take the current `record_revision` from the saved record as `expected_revision`.
5. `just task-checkpoint <id>` reads the complete updated record plus `expected_revision` from stdin and replaces the checkpoint atomically. A revision mismatch means another writer won; reload and retry.
6. `just task-export <id>` writes the sanitized handoff to `docs/agent-tasks/<id>.md` for review and commit. The export carries no patch; transfer commits or a reviewed patch separately.

## Check mapping

Host-specific Nix changes evaluate the affected host (`bash scripts/checks/nix.sh <host>`, plus `nixfmt`). Shared Nix modules evaluate all hosts. `flake/flake.nix`, `flake/flake.lock`, `justfile`, `.gitlab-ci.yml`, `scripts/checks/`, and `flake/tests/` select the full gate, as do unknown code paths. Python changes under `opnsense_reconciler/` run the Python gate (`bash scripts/checks/python.sh`: Ruff format and lint, Pyright, then the full unit suite). GitOps changes lint YAML, render every Kustomize boundary, and schema-validate against the pinned set under `schemas/kubernetes/`. OpenTofu changes run `tofu fmt -check` and `validate` with the backend disabled; run `just provision-check-deps` once to install the locked provider. Documentation-only changes (`*.md`, `docs/`) check trailing whitespace (`python scripts/checks/docs.py`), which skips vendored `node_modules` trees and `3d-prints/` CAD sources; `git diff --check` whitespace runs as part of the full gate. Agent workflow changes (`scripts/agent/`, `hooks/`, `.opencode/`, `tests/test_agent_*`) run the workflow gate (`bash scripts/checks/agent-workflows.sh`: agent unit tests plus `shellcheck`). Home Assistant and registry changes run their respective gates. `3d-prints/` is git-ignored local-only content: `check-changed` excludes it and selects no checks for it, and `tests/test_wyse5070_mount.py` skips when the directory is absent. Agent workspace changes (`scripts/agent_workspaces/`, `tests/test_agent_workspace*`, `config/agent-workspaces/`) run manifest validation and the workspace unit tests; `flake/modules/agent-workspace*.nix` runs validation plus all-host evaluation.

The GitOps gate fails when a custom-resource schema is missing. Required CRD schemas are pinned under `schemas/kubernetes/`, so local and CI validation use the same offline inputs. `just refresh-crd-schemas` snapshots all served cluster schemas into ignored `.agent-cache/schemas/` for review when the pinned set needs updating. `just provision-check-deps` installs the locked OpenTofu provider before the offline gate. GitOps validation skips encrypted SOPS manifests, whose kind is ciphertext until Flux decrypts them, and the vendored CRD definitions themselves. Live probes are reported separately from repository validation.

## Transfer and cleanup

Exports omit raw task logs and redact common secrets and credential paths. An export does not contain an uncommitted patch. Transfer the commits or create a reviewed patch separately. An export is a point-in-time snapshot: it records the export time and both HEADs, but it never updates itself. Check `git log` on the file before relying on an older handoff. Remove obsolete local task and evidence directories only after inspecting them; no command automatically deletes task state.

## Client hooks

`hooks/` holds the shared lifecycle scripts; each client directory wires the events it supports:

| Client | Config | Session start | Validation failure | Stop |
| --- | --- | --- | --- | --- |
| Claude Code | `.claude/settings.json` | SessionStart | PostToolUseFailure (Bash) | Stop |
| Codex | `.codex/hooks.json` | SessionStart | not wired, use the client’s failure event where supported | Stop |
| Cursor | `.cursor/hooks.json` | sessionStart | postToolUseFailure (Shell) | stop |
| OpenCode | `opencode.json`, `.opencode/plugins/agent-harness.js` | not wired, run `just agent-context` | plugin `tool.execute.after` (Bash), failures via the shared hook | not wired, run `just task-checkpoint <id>` |

Session start injects the bounded context packet without selecting a task; export `AGENT_TASK_ID` to scope context to one task. The stop hook inspects the scoped task, or every active task when none is scoped, and reminds when checkout drift or unresolved validation exists. The reminder is advisory-only: the stop hook never blocks stopping. Failing `just`, `nix`, `tofu`, `ruff`, `pyright`, `yamllint`, `kubeconform`, `kubectl`, `python`, `bash`, and `gh` commands are recorded as sanitized JSONL under `.agent-state/evidence/hooks/`. Hooks fail open: missing `jq`, a timeout, recursive invocation, or an unwritable evidence directory exits silently so sessions are never blocked. Where a client has no wired event, use the explicit `just` command instead.

## JSON envelope

Structured commands return `schema_version`, `command`, and command-specific data. Failures return nonzero with `status: error`, `error_type`, and a sanitized `message`. Doctor items use `pass`, `fail`, or `unavailable`. Runtime evidence is timestamped and describes one observation, never perpetual health.

## Acceptance coverage

| Scenario | Executable proof |
| --- | --- |
| Only untracked files are dirty | `tests.test_agent_git_state.GitStateTest.test_only_untracked_files_are_dirty_and_change_the_fingerprint` |
| Zero, one, or multiple active tasks | `tests.test_agent_context.AgentContextTest.test_no_task_lists_active_tasks_without_selecting` and `test_explicit_and_session_task_selection` |
| Source edit makes verification stale | `tests.test_agent_tasks.TaskRecordTest.test_resume_reports_drift_unavailable_base_and_stale_verification_without_rewrite` |
| HEAD drift or unavailable base | `tests.test_agent_tasks.TaskRecordTest.test_resume_reports_drift_unavailable_base_and_stale_verification_without_rewrite` |
| Concurrent writers conflict | `tests.test_agent_tasks.TaskRecordTest.test_checkpoint_requires_matching_revision_and_preserves_previous_record` |
| Invalid or interrupted checkpoint preserves state | `tests.test_agent_tasks.TaskRecordTest.test_lock_conflict_and_interrupted_replace_keep_valid_record` |
| Spaces and unusual Git path bytes | `tests.test_agent_git_state.GitStateTest.test_collects_all_local_change_sources_with_unusual_paths` and `test_repository_root_with_newline_is_preserved` |
| Renamed, deleted, staged, and untracked files route | `tests.test_agent_git_state.GitStateTest.test_collects_all_local_change_sources_with_unusual_paths` and `test_staged_deletion_is_an_index_change` |
| Unknown or shared check paths select full validation | `tests.test_agent_checks.AgentCheckSelectionTest.test_routes_repository_surfaces_with_reasons` |
| Startup remains local without credentials | `tests.test_agent_context.AgentContextTest.test_text_output_is_bounded_and_reports_truncation` and `tests.test_agent_doctor.AgentDoctorTest.test_doctor_reports_structured_results_without_secret_values` |
| Missing required tooling fails explicitly | `nix develop ./flake -c python -m unittest tests.test_agent_doctor -v` |
| Live timeout is bounded and nonhealthy | `tests.test_agent_status.AgentStatusTest.test_unknown_probe_is_nonhealthy_and_bounded` |
| Secrets are removed from evidence and handoff | `tests.test_agent_status.AgentStatusTest.test_evidence_is_sanitized` and `tests.test_agent_context.AgentContextTest.test_export_redacts_and_explains_uncommitted_recovery` |
| Unsupported hook event has an explicit fallback | `tests.test_agent_hooks.AgentHookTest.test_client_hook_configuration_is_valid_json`; use `just agent-context` where a lifecycle event is unavailable |
| Codex PostToolUse payload records failures | `tests.test_agent_hooks.AgentHookTest.test_validation_result_accepts_codex_post_tool_use_payload` |
| Stop without a scoped task scans active tasks | `tests.test_agent_hooks.AgentStopHookTest.test_unscoped_scan_finds_drifted_tasks` |
| Export contains fresh-session recovery fields | `tests.test_agent_context.AgentContextTest.test_export_redacts_and_explains_uncommitted_recovery` |
| Export records export time and HEADs | `tests.test_agent_context.AgentContextTest.test_export_redacts_and_explains_uncommitted_recovery` |
