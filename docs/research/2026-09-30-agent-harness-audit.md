# Agent harness audit, 2026-09-30

Scope: repository instructions, navigation, task state, check selection and execution,
shared lifecycle hooks, Claude/Codex/Cursor/OpenCode adapters, local diagnostics,
and their relationship to CI. This is a local source and behavior audit. Publication
and infrastructure activation are separate operations.

Baseline: `main` at `f109f3a8b7a82f9c55add87607e0137add8ec0b1`.
The checkout already contained unrelated changes to `registry/observed-images.json`.
Those changes were preserved. Local recovery record: `.agent-state/tasks/harness-audit/`.

## Findings and delivered changes

| Priority | Finding | Change and observable result |
| --- | --- | --- |
| High | Failure hooks persisted raw commands although the guide promised sanitization. OpenCode truncated before redaction. `jq -n` also produced multiple lines per record despite the JSONL contract. | Shared Python recorder redacts before truncation, writes one JSON object per line, adds schema version and UTC timestamp, validates numeric exit codes, and tolerates malformed payloads. OpenCode forwards the complete command. Redaction also covers common secret CLI arguments. |
| High | Check evidence used one fixed filename per check, allowing later or concurrent runs to replace evidence referenced by an earlier task. | Each execution gets a unique log path. Regression coverage proves earlier evidence survives a later run and remains redacted. |
| Medium | Stop launched a full task resume for every active task. Each resume collected checkout state twice, including a base comparison unnecessary for a reminder. Per-task timeouts did not bound the overall scan. | One Python scan validates records and fingerprints the checkout once. One six-second subprocess timeout bounds the scan. Output remains advisory and lists at most five tasks in deterministic order. |
| Medium | A current checkpoint with unresolved task failures produced no reminder if it lacked failed verification records. | Stop checks unresolved failures explicitly. Corrupt records are skipped independently. |
| Medium | Exact client configuration paths selected the full gate through the unknown-path fallback. The targeted workflow gate omitted Python formatting, lint, and types. | Client configuration changes select the workflow gate. It runs Ruff, Pyright, agent tests, and ShellCheck. Changes to routing/check infrastructure still select full validation. |
| Medium | A large changed-path list consumed the startup packet before task selection and validation instructions. A JSON list used as a task record crashed task discovery. | Text context caps path/task lists, retains totals and a truncation notice, and includes navigation links. Selected context includes objective, next action, and unresolved failure count. Discovery skips nonobject records. JSON retains complete lists. |
| Low | Doctor called any existing adapter file configured without parsing it. The navigation map pointed agents primarily at historical implementation plans. Session context contained a literal backslash-n. | Doctor parses JSON/TOML and reports syntax failures without printing file content. The map points to the operator guide and maintained workflow gate. The context prefix uses a real newline. |

## Timing

Three consecutive warm invocations per command on this laptop, using the same
checkout with 16 active/blocked tasks including this audit. Commands were executed
directly, with stdout captured. These are local hook timings, not CI speedups.

| Command | Before, seconds | After, seconds |
| --- | --- | --- |
| `just agent-context` | 0.089, 0.092, 0.083 | 0.093, 0.086, 0.089 |
| `bash hooks/stop`, stdin `{}` | 3.949, 3.975, 4.082 | 0.100, 0.095, 0.094 |

Median Stop latency fell from 3.975 seconds to 0.095 seconds, about 42 times faster.
Median context latency remained 0.089 seconds. The text context grew from 856 to
1228 bytes as navigation, omitted-list counts, and new changed paths were included.
The six-KiB limit remains enforced. Large or slow files can still exhaust the
hook timeout; the optional hook then returns without a reminder.

## Existing design to retain

- Shared behavior lives in repository code with thin client adapters.
- Startup is local, avoids credentials/network calls, and never selects another
  agent's task implicitly.
- Task updates use validated schemas, atomic replacement, per-task locks, and
  revision conflict detection. Resume derives staleness without rewriting state.
- Unknown code and validation infrastructure conservatively select full checks.
- Desired state, local validation, recorded observations, publication, and live
  activation have separate meanings and authorization boundaries.
- Ignored task/evidence files support recovery without permanent transcript growth.

## Remaining improvements, in order

1. **Verify adapters in fresh client sessions.** Repository fixture tests validate
   payload handling and envelopes; they do not prove installed clients load or emit
   these events. Codex failure recording is deliberately unwired after earlier hook
   failures. Payloads without a numeric exit code are ignored. Do not wire new
   events solely to claim client parity.
2. **Make task selection habitual.** There were 15 preexisting available records
   and no selected task at entry. Use session-scoped `AGENT_TASK_ID` or explicit
   `--task`; reconcile old task statuses only after inspecting their evidence.
   Whole-checkout fingerprints intentionally make unrelated edits stale. This is
   conservative but makes unscoped reminders noisy; changing that requires a clear
   dependency model, not filtering solely by owned filenames.
3. **Validate documentation links and command references.** The current docs gate
   checks whitespace, not link existence or executable recipe contracts. The dated
   specification describes capabilities and acceptance requirements beyond what
   today's adapter fixtures prove. Keep it historical; the operator guide describes
   current behavior.
4. **Expose verification freshness consistently.** JSON errors are structured for
   task/check selection failures but context errors still print text even with
   `--json`. Exported verification lines omit the derived stale flag. These remain
   interface gaps for automation and portable handoffs.
5. **Define evidence retention.** Unique logs preserve proof but increase local
   storage. Cleanup should follow inspected task references and explicit retention
   rules. Existing historical hook evidence was not rewritten by this audit.

## Verification evidence

Local outputs are stored under `.agent-state/evidence/harness-audit/`. The workflow
gate covers fixture handling, redaction before truncation, JSONL framing, malformed
payloads, preservation of previous check evidence, one Git-state scan, unresolved
failures at a current checkpoint, adapter routing, and bounded startup context.
Validation completed so far:

- Workflow gate: 115 tests passed, plus Ruff, Pyright, and ShellCheck.
- Repository Python gate: 1138 tests passed, with 10 existing skips.
- Formatting and pinned-environment doctor passed.
- The initial full gate stopped at missing Cloudflare provider dependencies.
  Locked providers were provisioned with `-backend=false -lockfile=readonly`.
  Remaining OpenTofu, Home Assistant (107 resources), documentation, whitespace,
  and secret checks passed. `nix flake check 'path:.?dir=flake'` also passed.
  The full gate completed across the initial run and the resumed remaining stages.
- Doctor was checked again after refining its JavaScript presence wording.

Final validation results and remaining limitations are recorded in the audit task
checkpoint.

Actual Claude, Cursor, Codex, and OpenCode session integration was not exercised.
No cluster/network probes, deployment, push, or external messages were performed.
