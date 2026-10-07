# Agent Workflow Operator Guide

The shared CLI is `python -m scripts.agent`; `just` provides stable recipes. Startup and offline checks do not contact infrastructure or decrypt secrets.

## Commands

- `just agent-context [--json] [--task ID]`: bounded startup packet. `AGENT_TASK_ID` is a session-scoped alternative. Without either, active tasks are listed and none is selected.
- `just agent-run ID CLIENT [-- CLIENT_ARGS...]`: validate an active or blocked task, then launch `codex`, `claude`, `cursor` (the `agent` executable), `opencode`, `antigravity` (the `agy` executable), or `muse` with session-scoped `AGENT_TASK_ID`. Client arguments are forwarded literally and its exit status is preserved. It does not create a shared active-task pointer.
- `just agent-verify [--json] [--task ID] [--record] -- COMMAND...`: run the command with unique output/report evidence, verification time, and before/after source fingerprints. Preserve a positive command exit status; return nonzero if a passing command changed source during execution. `--record` appends the verification to the explicitly selected or inherited task using revision conflict detection. Reports survive a checkpoint conflict. Without `--record`, the task is not modified.
- `just agent-smoke ID CLIENT [--timeout SECONDS]`: explicitly exercise the installed client with one verification command that exits 7. Record actual hook receipts and failure evidence. Codex/OpenCode/Cursor use configured providers; Claude uses a loopback API fixture to exercise its real hook engine without provider inference. Default total client budget is 120 seconds. This is an opt-in integration probe, excluded from startup and routine offline tests.
- `just doctor [--json]`: local tools, paths, nested flake, adapter JSON/TOML syntax, and credential references. It never reads credential values. Adapter presence and valid syntax do not prove that a client loaded the hooks.
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

Verification records contain command, exit code, time, source fingerprint, evidence path, and stale state. Check execution emits this record in `results[].verification` and writes a unique JSON sidecar beside the log. Copy the emitted record rather than assigning the latest checkout fingerprint to an older run. Resume derives `superseded` for earlier runs of the exact same command; those records remain visible but do not cause unresolved-validation reminders after a later successful run. Explicitly recorded staleness remains stale. Resume derives staleness without rewriting the record. Task records and runtime evidence live in ignored `.agent-state/` and remain checkout-local.

## Task lifecycle

1. `just task-new <id> --template > /tmp/<id>.json`, then fill in objective, acceptance criteria, owned files, and next action.
2. `just task-new <id> < /tmp/<id>.json` to create the task. Creation refuses to overwrite an existing task and requires a committed HEAD.
3. Launch the client through `just agent-run <id> <client>` so its hooks inherit task selection. For an already running client, use explicit `--task <id>` on context and verification commands. Do the work, keeping edits inside the owned files. Use `just agent-verify --json --record --task <id> -- <check-command>` to bind checks to observed source identity.
4. `just task-resume <id> --json` to inspect HEAD and fingerprint drift plus stale verification. Take the current `record_revision` from the saved record as `expected_revision`.
5. `just task-checkpoint <id>` reads the complete updated record plus `expected_revision` from stdin and replaces the checkpoint atomically. A revision mismatch means another writer won; reload and retry.
6. `just task-export <id>` writes the sanitized handoff to `docs/agent-tasks/<id>.md` for review and commit. The export carries no patch; transfer commits or a reviewed patch separately.

## Publishing changes

Forgejo is the primary Git host. Agents publish as the dedicated machine user `homelab-agent` with the `tea` CLI in the pinned dev shell, never a personal account:

```sh
git push origin <branch>
just forgejo-pr "imperative title"   # opens the PR against main
# wait for ci/woodpecker/validation-v2 to turn green, then:
just forgejo-merge <index>           # prefer fast-forward merges
```

One-time setup (human, Forgejo UI): create `homelab-agent`, add it as collaborator with write access to `JEFF7712/homelab` only, generate its token with exactly `read:user`, `read:repository`, `write:repository`, `read:issue`, `write:issue`, then run `tea login add --url http://git.internal:3000` on each agent machine. Branch protection binds the machine user too: no direct pushes to main, and merges require a green `ci/woodpecker/validation-v2` status. Where the optional machine-user SSH key is registered (setup step 4 in [Agent Forgejo access](runbooks/ci-pipelines.md)), push with `git push git-agent:JEFF7712/homelab.git <branch>` so the push is also attributed to the machine user; otherwise `git push origin` uses the machine's default Forgejo identity. Full procedure and token rotation: [Agent Forgejo access](runbooks/ci-pipelines.md).

## Check mapping

Host-specific Nix changes evaluate the affected host (`bash scripts/checks/nix.sh <host>`, plus `nixfmt`). Shared Nix modules evaluate all hosts. `flake/flake.nix`, `flake/flake.lock`, `justfile`, `.gitlab-ci.yml`, `scripts/checks/`, and `flake/tests/` select the full gate, as do unknown code paths. Python changes under `opnsense_reconciler/` run the Python gate (`bash scripts/checks/python.sh`: Ruff format and lint, Pyright, then the full unit suite). GitOps changes lint YAML, render every Kustomize boundary, and schema-validate against the pinned set under `schemas/kubernetes/`. OpenTofu changes run `tofu fmt -check` and `validate` with the backend disabled; run `just provision-check-deps` once to install the locked provider. Documentation-only changes (`*.md`, `docs/`) use `python -m scripts.checks.docs` to check trailing whitespace across owned Markdown, local links and Markdown anchors in maintained entrypoint/runbook/network/decision docs, documented `just` recipe names, and the maintained workflow recipe/CLI option contracts. Link checks exclude external URLs and code examples; historical plans, task exports, and research are outside the maintained-link scope. Whitespace scanning which skips vendored `node_modules` trees and `3d-prints/` CAD sources; `git diff --check` whitespace runs as part of the full gate. Agent workflow changes (`scripts/agent/`, `hooks/`, `.opencode/`, `tests/test_agent_*`, and the exact client configuration files listed under Client hooks, plus `.codex/config.toml` and `.mcp.json`) run the workflow gate (`bash scripts/checks/agent-workflows.sh`: Ruff formatting and lint, Pyright for `scripts/agent`, agent unit tests, and `shellcheck`). The full gate performs Python lint and type checks through its Python entrypoint, avoiding duplicate workflow lint. Home Assistant and registry changes run their respective gates. `3d-prints/` is git-ignored local-only content: `check-changed` excludes it and selects no checks for it, and `tests/test_wyse5070_mount.py` skips when the directory is absent. Agent workspace changes (`scripts/agent_workspaces/`, `tests/test_agent_workspace*`, `config/agent-workspaces/`) run manifest validation and the workspace unit tests; `flake/modules/agent-workspace*.nix` runs validation plus all-host evaluation.

The GitOps gate fails when a custom-resource schema is missing. Required CRD schemas are pinned under `schemas/kubernetes/`, so local and CI validation use the same offline inputs. `just refresh-crd-schemas` snapshots all served cluster schemas into ignored `.agent-cache/schemas/` for review when the pinned set needs updating. `just provision-check-deps` installs the locked OpenTofu provider before the offline gate. GitOps validation skips encrypted SOPS manifests, whose kind is ciphertext until Flux decrypts them, and the vendored CRD definitions themselves. Live probes are reported separately from repository validation.

## Transfer and cleanup

Exports omit raw task logs and redact common secrets and credential paths. An export does not contain an uncommitted patch. Transfer the commits or create a reviewed patch separately. An export is a point-in-time snapshot: it records the export time and both HEADs, but it never updates itself. Check `git log` on the file before relying on an older handoff. Remove obsolete local task and evidence directories only after inspecting them; no command automatically deletes task state.

## Client hooks

`hooks/` holds the shared lifecycle scripts; each client directory wires the events it supports:

| Client | Config | Session start | Validation failure | Stop |
| --- | --- | --- | --- | --- |
| Claude Code | `.claude/settings.json` | SessionStart | PostToolUseFailure (Bash) | Stop |
| Codex | `.codex/hooks.json` | SessionStart | PostToolUse (Bash), reliable exit status through structured verification output | Stop |
| Cursor | `.cursor/hooks.json` | sessionStart | postToolUseFailure (Shell) | stop |
| OpenCode | `opencode.json`, `.opencode/plugins/agent-harness.js` | shared context via `experimental.chat.system.transform` | plugin `tool.execute.after` (Bash), failures via the shared hook | shared reminder on `session.idle` |
| Antigravity CLI | `.agents/plugins/homelab-workflow/` (installed plugin) | shared context as an ephemeral message on first `PreInvocation` | `PostToolUse` (`run_command`), records explicit runtime errors; shell exit failures require explicit verification | advisory shared Stop, never continues the model |
| Meta Muse Code | `.muse/harness/` (installed and approved native plugin) | SessionStart | PostToolUse and PostToolUseFailure adapters; shell failure delivery unverified on 1.0.2 | shared Stop |

Session start injects the bounded context packet without selecting a task. Text context limits path and task lists so navigation and validation remain visible; `--json` returns the complete lists. Selected tasks include their objective, next action, and unresolved failure count; export `AGENT_TASK_ID` to scope context to one task.

### Antigravity and Muse registration

The launcher uses `agy` for Antigravity CLI. The `antigravity` executable opens the
desktop app. Muse means Meta Muse Code, whose executable is `muse`.

Antigravity's bundle is registered with `agy plugin install
.agents/plugins/homelab-workflow`. Muse's bundle is registered from this trusted
workspace with `muse plugins install .muse/harness --scope project --json`, then
`muse plugins approve homelab-workflow --json`. After editing the bundles, reinstall
Antigravity's bundle and run `muse plugins update homelab-workflow --json` followed
by approval. Their installed copies do not automatically track repository edits.
Inspect the actual registrations with Antigravity's `/hooks` and
`muse plugins inspect homelab-workflow --json`.

Both launches create a temporary helper on their own PATH under ignored
`.agent-state/client-sessions/`. Shared hooks recover the selected task from that
helper when the runtime filters custom environment variables. Each launch has its
own helper, removed on exit. Muse hooks locate the repository from the payload's
`cwd`; Antigravity uses `workspacePaths` or the scoped helper when that list is
empty, as observed on 1.2.5. Launch through `just agent-run ID CLIENT` to preserve
this scope. Raw launches still receive context when their payload identifies this
repository, but do not implicitly select an active task.

Fresh installed-client probes on Antigravity 1.2.5 and Muse Code 1.0.2 confirmed
task-scoped context and advisory Stop. Both probes remain failed at the shell
failure receipt gate: Antigravity delivered PostToolUse without an error for exit
7, and Muse did not deliver a shell failure hook. Do not treat plugin validation
or a successful client process exit as proof of complete hook integration.
Use `just agent-verify --json --record --task ID -- COMMAND...` for durable task
verification on these versions. Explicit task selection also works when a
client's shell environment omits `AGENT_TASK_ID`. See the
[integration report](research/2026-09-30-extra-client-integration.md).

The stop hook inspects the scoped task, or all active and blocked tasks when none is scoped, using one checkout fingerprint within a six-second total subprocess budget, and reminds when checkout drift or unresolved validation exists, including failures saved on a current checkpoint. The reminder is advisory-only: the stop hook never blocks stopping.

OpenCode injects the shared packet into model system context, cached during a turn
and refreshed after idle or compaction. New and resumed sessions use the same path.
Only sessions that requested context trigger an idle reminder, once per turn.
Reminders go to stderr and a TUI warning toast, without starting a model response
or writing synthetic chat messages. Shared hook subprocess limits are six seconds
for context, eight for Stop, and five for failure recording; TUI delivery has a
one-second timeout. Missing tools, malformed hook output, and notification errors
fail open. These experimental model hooks are covered by the installed-client
smoke probe, which now requires all three stages for OpenCode too.

Failing `just`, `nix`, `tofu`, `ruff`, `pyright`, `yamllint`, `kubeconform`, `kubectl`, `python`, `bash`, and `gh` commands are recorded with schema version and UTC timestamp as one JSON object per line in sanitized JSONL under `.agent-state/evidence/hooks/`. Claude/Cursor failure events with no numeric status are recorded with a null exit code and their reported failure type; no numeric code is invented. Claude error strings containing an explicit exit code are recognized. The installed Codex passes raw stdout as `tool_response`, so an empty failed command cannot be distinguished from success by the hook. Use `agent-verify --json` or `check-changed --json` for an explicit result. Command redaction runs before truncation to 200 characters; OpenCode forwards the full command to the shared recorder. Evidence paths from `check-changed` are unique per execution so later runs cannot overwrite earlier proof.

Hooks fail open: missing `jq`, a timeout, recursive invocation, or an unwritable evidence directory exits silently so sessions are never blocked. Where a client has no wired event, use the explicit `just` command instead. Opt-in `AGENT_HOOK_TRACE=1` records bounded metadata receipts under `.agent-state/evidence/hook-integration/`, including event, phase, task selection, input keys, and normalized failure status. It does not record raw hook payloads or stdout. Codex smoke tests bypass hook trust only for that vetted invocation; normal client sessions retain their hook review/trust flow.

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

## Harness audit

The four follow-ups and installed-client results are recorded in [`docs/research/2026-09-30-agent-harness-followups.md`](research/2026-09-30-agent-harness-followups.md).

The findings, local timing measurements, and remaining improvements from the September 2026 audit are recorded in [`docs/research/2026-09-30-agent-harness-audit.md`](research/2026-09-30-agent-harness-audit.md). Fixture tests establish repository behavior; timestamped smoke reports establish the observed installed-client behavior and its limitations.
