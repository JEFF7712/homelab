# Agent harness follow-ups, 2026-09-30

This continues the [initial audit](2026-09-30-agent-harness-audit.md). The implementation
started at `main@a463db5b53696732776f08974e05b735cdfe4837`, with the previous audit
changes still local. Existing pod-agent and registry changes were preserved.

## 1. Installed client integration

The repeatable entrypoint is `just agent-smoke <task-id> <client>`. It starts the
installed client, executes one command through `scripts.agent verify` that exits 7,
and checks actual timestamped hook receipts and failure evidence. It fails when
required evidence is absent, even if the client exits successfully. Each run records
its client version, task, source fingerprint, mode, stages, and evidence paths.
Receipts from older runs or different tasks cannot establish current success.
This opt-in probe is outside startup and offline validation.

| Client | Installed version | Observed result | Limits |
| --- | --- | --- | --- |
| Codex | 0.159.0 | Startup context, failure evidence with exit 7, and Stop passed in fresh CLI sessions. | Hook trust was bypassed for the vetted smoke invocation only. Raw command stdout does not expose exit status; use structured verification output. |
| Claude Code | 2.1.275 | The actual CLI emitted startup, failure, and Stop events, and recorded exit 7. | Model responses came from a deterministic loopback API fixture. This proves the installed hook engine, without testing provider authentication or inference and without delegating work to Claude. |
| OpenCode | 1.18.32 | Its actual Bash tool reported metadata exit 7, and the project plugin recorded it. A subsequent lifecycle smoke also passed context injection and Stop. | Startup context uses the experimental system transform. Idle reminders use stderr and a bounded TUI toast, without model continuation. |
| Cursor CLI | 2026.09.15-d2fe57e | Startup injected task-scoped context. | The account rejected the configured `cursor-grok-4.5-high` model: `Named models unavailable`, `Free plans can only use Auto`. Failure and Stop integration remain unverified. Auto was not substituted for the configured model lane. |

The first fresh Codex run exposed a contract gap: a failed command with no stdout
arrived as `tool_response: ""`, with no exit-code field. A hook cannot reconstruct
that missing status. `agent-verify --json` solves this by emitting the actual status
and source identity as JSON. The Codex PostToolUse adapter is now wired to the shared,
fail-open recorder, and its structured-output path was exercised successfully.

Claude and Cursor send failure-event error strings. The recorder now supports their
events, extracts explicitly reported exit codes, and records null when no numeric
status was supplied. It retains the reported failure type and redacts error text.
It never invents a process exit status for timeouts or permission denials.

Sources checked against the installed clients:
[Codex hooks](https://learn.chatgpt.com/docs/hooks),
[Claude hooks](https://code.claude.com/docs/en/hooks),
[Cursor hooks](https://cursor.com/docs/hooks), and
[OpenCode plugins](https://opencode.ai/docs/plugins/).

### Subsequent OpenCode lifecycle integration

The project plugin now calls the shared startup hook through
`experimental.chat.system.transform`, adding its packet to the actual model system
context. It caches the packet during each turn and clears it on idle or compaction
so continued and resumed work receives fresh context. Each active session invokes
the shared Stop hook once on `session.idle`. Duplicate idle events are ignored.
The reminder is advisory: stderr for CLI runs and a warning toast for the TUI.
No automatic model response or synthetic user message is generated.

Startup, Stop, and failure subprocesses have respective six, eight, and five second
limits; toast requests have a one second timeout. Missing or malformed hooks and
notification failures do not block inference or completion. A fresh installed
OpenCode 1.18.32 run passed all three receipt stages and printed the task-scoped
checkpoint reminder. The reusable smoke probe now requires all three stages for
OpenCode; prior failure-only evidence cannot satisfy this new acceptance.

The installed contract was checked against the
[versioned plugin interface](https://github.com/anomalyco/opencode/blob/v1.18.32/packages/plugin/src/index.ts)
and [idle event implementation](https://github.com/anomalyco/opencode/blob/v1.18.32/packages/opencode/src/session/status.ts).

## 2. Explicit task selection

`just agent-run <task-id> <codex|claude|cursor|opencode> [-- client arguments...]`
validates the task, launches from the repository root, sets session-scoped task/client
environment variables, and preserves the client exit status. The parent environment
and other sessions are unchanged. The task must be active or blocked.

The new recipes use positional arguments so prompts with spaces, dollar signs, or
shell syntax are forwarded literally. Startup context explicitly points unselected
sessions to this launcher. Existing sessions can select tasks through explicit
`--task` arguments. No shared active-task pointer or automatic task adoption was added.
Old task statuses were not changed without evidence that their work was complete.

## 3. Offline documentation contracts

The docs gate now checks:

- Whitespace in owned Markdown, excluding vendored dependencies, local CAD, ignored
  task/evidence/cache directories, and local worktrees.
- Local inline links, reference definitions, and Markdown heading anchors in the
  maintained entrypoints, runbooks, networking docs, and decision records.
- Documented `just` recipe references against `just --dump`.
- The maintained workflow recipes against their Python CLI commands and required options.

External URLs are not fetched. Fenced and inline code examples are excluded from
link checks. Historical plans, research, and task exports remain outside maintained
link enforcement. This is a repository-oriented Markdown checker, not a complete
CommonMark renderer; unusual Markdown syntax may need explicit extension.

The checker found a nonexistent `just provision-homelab-01` command in the PostgreSQL
recovery runbook. That reference now points to the actual `nixos-anywhere` provisioning
policy and owning host/disk source, without constructing an unreviewed disk-write command.
CI, `just`, targeted selection, and the full gate all invoke the same module entrypoint.

## 4. Verification freshness

Every selected check now records execution time, source fingerprint before execution,
current fingerprint after execution, actual exit status, output path, and a JSON
sidecar. Source changes during a successful command yield a stale result and nonzero
CLI status. This prevents a moving checkout from being reported as currently verified.

`just agent-verify --json --record --task <id> -- <command>` can append that exact
verification record to the task checkpoint. Revision conflicts preserve the existing
task record and leave the check evidence available. A completed task cannot be changed
through this operation. Without `--record`, verification does not modify task state.

Context failures with `--json` now return structured error envelopes. Selected text
context summarizes the latest verification records. Resume and export expose freshness;
exports include verification time, recorded and current fingerprints, and evidence.
Earlier runs of the exact same command are marked superseded. Their history remains
visible, while an old failure no longer causes reminders after a later successful run.
An explicitly recorded stale run remains stale even if source identity later matches.

Whole-checkout fingerprinting remains conservative: unrelated edits can still make
evidence stale. Fresh repository evidence does not establish live infrastructure health.

The freshness guard also caught the full offline gate refreshing the tracked registry
snapshot from the live cluster during validation. The underlying command succeeded,
but its evidence correctly became stale. The offline registry check now validates the
saved snapshot without refreshing it. Explicit `just registry-check` still performs
the live refresh. A regression test checks the actual shell entrypoint using tools
that reject cluster access and refresh attempts.

## Evidence and acceptance

The full gate for the original four follow-ups passed in a detached checkout at
`28f44380aa3c2c93b6966a72704b01129618a682` with the reviewed harness changes:
1,164 tests run, 23 skipped, secret scanning passed, and five runnable Nix checks
passed on x86_64-linux. Its source fingerprint remained unchanged throughout the
run. The skipped tests include local CAD absent from the isolated checkout.
Other system architectures were not executed. Formatting also passed in the
shared checkout. At that checkpoint, all task-owned implementation files matched
the tested snapshot. The subsequent OpenCode lifecycle extension uses the scoped
workflow gate, documentation checks, formatting, and a new installed-client smoke;
the full Nix gate was not repeated for this extension.

The isolated checkout protected validation from concurrent registry, pod-agent,
and networking work. A temporary external reset of tracked files was detected
and the task-owned harness changes were recovered from that checkout. The
full-gate result applies to its recorded snapshot, rather than establishing that
the continually changing shared checkout is fully verified.

Installed-client summaries and test logs live in `.agent-state/evidence/harness-followups/`.
Repeatable smoke reports live in `.agent-state/evidence/client-smoke/`; event receipts
live in `.agent-state/evidence/hook-integration/`. The task checkpoint records offline
validation results and the remaining Cursor account gate.

No publication or live infrastructure activation was performed. Cursor failure/Stop
acceptance requires an account that permits the configured Grok model, followed by a
new smoke run. The existing integration result must continue to say unavailable until
that evidence exists.
