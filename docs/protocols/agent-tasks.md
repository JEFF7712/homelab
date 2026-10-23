# Agent task protocol, version 1

Contract between the persistent supervisor, the controller and workers, as
decided in the [audit](../research/2026-10-22-persistent-agent-audit.md). The
controller (`scripts/orchestrator/core.py`) owns all task truth. The supervisor
and workers request changes; the controller validates and records them.

## Store

One SQLite database on a local filesystem, WAL with `synchronous=FULL` and a
5-second busy timeout. Every mutation runs in one `BEGIN IMMEDIATE`
transaction together with the outbox rows it causes, so a crash never leaves
a status change without its notification or the reverse.

- **Local storage only.** Opening a store whose directory is on NFS, CIFS/SMB,
  9p, Ceph, GlusterFS, AFS, DAV or a FUSE network mount fails with `STORAGE`;
  their locking does not support WAL.
- **One active controller.** `Store(..., controller=True)` holds an exclusive
  `flock` on `<db>.controller.lock` for its lifetime; a second controller fails
  with `CONTROLLER_ACTIVE`. Only that store may `claim()` or `reap()`
  (`NOT_CONTROLLER` otherwise). Operator commands open the store without the
  lock. The lock fences processes on one host only: the controller is pinned
  to one node with a node-local volume and must not relocate automatically.
- **Global cap.** The concurrency cap lives in the `settings` table
  (`set_max_running`, default 1) and is read inside the claiming transaction,
  never passed per call.
- **Migrations.** `PRAGMA user_version` is the schema version.
  `MIGRATIONS` in `core.py` is append-only. Only the lock-holding controller
  (or `migrate`, which takes the same lock and so refuses while a controller
  runs) upgrades a store; any other opener of an outdated store gets `SCHEMA`.
  Each pending entry runs in its own transaction under `<db>.migrate.lock`,
  after an integrity-checked snapshot `<db>.pre-v<N>-<random>.sqlite` of any
  store that already has a schema. A failed step rolls back and leaves the
  previous version. A newer schema refuses to open.
- **Backup.** `python -m scripts.orchestrator backup --db <db> <dest>` uses
  SQLite's online backup into a private temporary file, runs `PRAGMA
  integrity_check` on it, and only then links it to the destination (mode
  0600), so a failed backup leaves nothing that looks valid. It refuses an
  existing destination. `check` reports schema
  version, filesystem type and integrity. Both print one JSON object.
- **Recovery.** Nothing in memory is authoritative. After a controller crash
  the next controller reaps expired leases, which returns their tasks to
  `ready` as a new attempt.

## Identities and idempotency

| Write | Key | Redelivery | Same key, different content |
| --- | --- | --- | --- |
| create task | `(principal, idempotency_key)` | returns the stored task | `IDEMPOTENCY_CONFLICT` |
| event | `event_id` | ignored | ignored (first write wins) |
| question | `question_id` | returns the stored question | returns the stored question |
| approval request | action digest | returns the pending approval | new digest, new approval |
| outbox message | message key | ignored | ignored |

The request digest of a task covers project, title, goal, acceptance criteria,
capabilities, priority, parent and dependencies. Event payloads larger than
16 KiB are replaced by `{"truncated": true, "bytes": n}`; raw logs belong in an
access-controlled artifact store, referenced by `context_ref`.

## Task states

| From | Allowed next states |
| --- | --- |
| `draft` | `ready`, `cancelled` |
| `ready` | `claimed`, `cancelled` |
| `claimed` | `running`, `retryable_failed`, `failed`, `cancelled` |
| `running` | `needs_input`, `validating`, `retryable_failed`, `failed`, `cancelled` |
| `needs_input` | `running`, `retryable_failed`, `failed`, `cancelled` |
| `validating` | `in_review`, `retryable_failed`, `failed`, `cancelled` |
| `in_review` | `completed`, `retryable_failed`, `failed`, `cancelled` |
| `retryable_failed` | `ready`, `failed`, `cancelled` |
| `completed`, `failed`, `cancelled` | none |

Callers may pass the status they observed; a mismatch is `STALE` and nothing
changes. Every transition is appended to `transitions` with actor and reason.
Completion has three separate stages, and `transition()` refuses both
`in_review` and `completed`:

1. **Worker-reported.** `settle_attempt()` moves `running` to `validating`.
   The worker says it is done; nothing is proven.
2. **Verified.** `record_verification()` stores the controller verifier's
   `Evidence` (a pull request, a full 40-character head SHA, a Woodpecker
   state of `success`, every acceptance criterion) and moves `validating` to
   `in_review`. Evidence that fails any check is refused and the task stays in
   `validating`.
3. **Finalized.** `complete()` takes no evidence. It re-checks the stored
   verification and moves `in_review` to `completed`.

Both later stages are controller-only (`NOT_CONTROLLER` otherwise). The worker
API and the supervisor's tools expose neither. The verifier builds `Evidence`
from its own Forgejo and Woodpecker reads, never from worker output.

## Attempts and leases

`claim()` selects the highest-priority `ready` task whose dependencies are all
`completed`, creates attempt `n + 1` with a lease and the digest of its
worker API token, and moves the task to `claimed`, enforcing the stored
global cap in the same transaction. At most
one unfinished attempt exists per task (partial unique index).

The lease owner renews before expiry. A renewal after expiry, by another
owner, or for a cancelled, failed or completed task is `LEASE_LOST`. `reap()`
finishes each expired attempt as retryable and queues its `worker.cancel` in
the same transaction, re-checking the expiry inside it, so a renewal that wins
the race is kept and a crash cannot finish an attempt without fencing it. An
expired lease does not stop a live process, so the Job manager deletes the
pod and acknowledges the cancel. The attempt's worker API token stops working
at once. Branches are attempt-scoped, so a fenced straggler
cannot write over its successor. A retryable finish returns the task to
`ready` until attempt 3, then `failed`.

`settle_attempt()` records `worker.settled`, moves the task from `running` to
`validating` and closes the attempt in one transaction, and only for the
current, unexpired lease holder.
Finishing an attempt is idempotent. It changes the task only while the task
is worker-driven (`claimed`, `running`, `needs_input`); once the task is in
`validating`, `in_review` or a terminal state it just closes the attempt and
frees the slot.

Cancellation moves the task to `cancelled` immediately and queues
`worker.cancel` for the live attempt. The attempt keeps its concurrency slot
until the worker adapter confirms termination with `finish_attempt()`.

## Questions

A worker question is stored before any notification is queued. A blocking
question moves the task from `running` to `needs_input`.

- The answer must name the current `revision`; `revise()` increments it.
- The first valid answer wins. Later answers fail with `ALREADY_ANSWERED`;
  answers to old revisions, after the deadline or for a finished attempt fail
  with `STALE`. Callers report the failure and raise a follow-up instead of
  overriding.
- A non-empty choice list restricts answers to its members.
- When no blocking question remains open, the task returns to `running` and a
  `worker.answer` message is queued for the adapter.
- An expired blocking question queues `worker.cancel`; the task stays in
  `needs_input` until the adapter finishes the attempt.

Worker question event, as produced by the Pi adapter from an
`extension_ui_request`:

```json
{
  "type": "worker.question",
  "event_id": "evt_...",
  "task_id": "task_...",
  "attempt_id": "att_...",
  "question_id": "q_...",
  "blocking": true,
  "prompt": "Should the service use local accounts or OAuth?",
  "options": ["local", "oauth", "discuss"],
  "context_ref": "artifact-or-log-reference",
  "revision": 1
}
```

## Worker backends

`scripts/orchestrator/worker.py` defines the contract every runtime
implements. A `Backend` opens a `Session` for one attempt; the session starts
from a brief and reports normalized events:

| Event | Meaning | Runner effect |
| --- | --- | --- |
| `session` | backend session or thread id | `worker.session` event |
| `question` | the worker needs an answer (key, prompt, choices) | blocking question `q_<attempt>_<key>` |
| `settled` | the run ended normally (final text) | `worker.settled` event; task to `validating` |
| `failed` | the run ended abnormally (detail, retryable) | attempt finished |

The session also accepts `answer(key, value)` and `abort()`. `AttemptRunner`
owns everything else for every backend: lease renewal every third of its
length (losing it stops the runner), `worker.answer` delivery, `worker.cancel`
handling (abort, stop the process, finish the attempt), the run deadline
(abort, attempt failed), process exit before settling (attempt finished,
retryable), and any other backend error (attempt finished, retryable). Each
attempt gets a private directory (mode 0700). The child environment holds
only locale, `PATH`, CA bundle variables and what the backend configuration
names; nothing else from the controller is inherited.

### Worker API

The runner never holds the store. It talks to the controller through a
`WorkerChannel` with eight operations: `start`, `renew`, `ask`, `inbox`,
`acknowledge`, `session`, `settle`, `finish`. In a worker pod that channel is
`HttpChannel`, a client of `scripts/orchestrator/worker_api.py`:

- At claim the controller mints a random token (`issue_token`), stores only
  its SHA-256 on the attempt, and gives the token to that attempt's Job.
- `POST /v1/attempt/<operation>` with `Authorization: Bearer <token>`
  resolves the token to its single unfinished attempt with an unexpired lease.
  Anything else returns 403 `LEASE_LOST`, so an expired worker cannot fail
  its task before the reaper runs. Authentication and the operation run under
  one store lock, so a reap or cancellation cannot land between them.
- There is no route for creating tasks, answering questions, deciding or
  consuming approvals, verifying or completing tasks, claiming, or reading
  other attempts. `acknowledge` accepts only this attempt's answer keys; a
  worker can never acknowledge its own `worker.cancel`. `renew` fails once
  the task is cancelled. `settle` keeps only `backend`, `provider`, `model`
  and `final_text`. `Content-Length` must be a non-negative integer of at most
  64 KiB.
- The token is checked before any body is read. Each connection carries one
  request (HTTP/1.0), every socket read times out after 10 seconds, and a
  watchdog closes any connection still open after 15 seconds, however slowly
  it trickles bytes. One peer address holds at most 4 connections and the
  server at most 32; extra connections get 503 at once. Workers run in
  separate pods, so a compromised worker cannot take the slots others need
  for lease renewal. The NetworkPolicy admits only worker pods to the API.
- Text from the worker (questions, final text, failure reasons, session ids)
  passes through `redact()`, which removes the backend's own credential values
  (the Claude token, the Codex login's token strings) and common token shapes
  (`sk-`, `sk-ant-`, GitHub tokens, bearer tokens, JWTs, `*_token=`), and
  truncates to 8 KiB. Identifiers derived from worker keys (question and
  session ids) are SHA-256 fragments, so a worker cannot persist text through
  an identifier.
- Starting a session is bounded: Pi's start is one RPC command with a
  30-second timeout, well inside the lease, and CLI backends write the brief
  on a separate thread.

`LocalChannel` implements the same operations directly on the store for the
controller process and tests. CI runs every Pi scenario through both.

### Pi

`scripts/orchestrator/pi_rpc.py` launches pinned Pi with `--mode rpc
--no-approve --no-extensions --no-skills`, the first-party
`scripts/orchestrator/pi/ask_supervisor.ts` extension, an explicit provider
and model, and `--session-dir <attempt>/pi-session`. The backend refuses the
`anthropic` provider outright and `openai-codex` unless it is constructed with
`experimental=True`.

| Pi record | Session event |
| --- | --- |
| `prompt` response `disposition: handled` | `failed`, not retryable; no run started |
| `extension_ui_request` `select`/`confirm`/`input`/`editor` | `question` keyed by the request id |
| other `extension_ui_request` | none |
| `agent_settled` with `aborted: false` | `settled` with `get_last_assistant_text` |
| `agent_settled` with `aborted: true` | `failed`, retryable |

`agent_end` alone is never treated as completion. Answers go back as
`extension_ui_response` (`value`, `confirmed`, or `cancelled`); abort is the
`abort` command.

### Native CLIs: Codex and Claude Code

`scripts/orchestrator/worker_cli.py` runs one non-interactive turn per
attempt. The brief goes to stdin. Questions reach the controller through
`scripts/orchestrator/ask_mcp.py`, a standard-library stdio MCP server that
the CLI launches as server `supervisor`. Each tool call opens the attempt's
private Unix socket (`QuestionBroker`, a fresh 0700 directory) and blocks until
the runner answers. The broker turns each call into a `question` event; when
the broker closes, any waiting call receives a tool error.

| | Codex CLI (pinned 0.163.0) | Claude Code (pinned 2.1.301) |
| --- | --- | --- |
| Command | `codex exec --json --model M --cd W --skip-git-repo-check --ignore-user-config --ignore-rules -c approval_policy="never" -c mcp_servers.supervisor.{command,args,tool_timeout_sec,required} -` | `claude -p --output-format stream-json --verbose --model M --session-id <uuid5(attempt)> --setting-sources user --strict-mcp-config --mcp-config <json> --permission-prompts none` |
| Sandbox `external` (pod is the boundary) | `--dangerously-bypass-approvals-and-sandbox` | `--permission-mode bypassPermissions` |
| Sandbox `host` (development) | `--sandbox workspace-write` | `--permission-mode acceptEdits --allowedTools mcp__supervisor__ask_supervisor` |
| Credential | ChatGPT login in a dedicated `CODEX_HOME`, enforced with `-c forced_login_method="chatgpt"` to refuse saved API-key logins | `CLAUDE_CODE_OAUTH_TOKEN` read from a token file at launch |
| Refused in the backend env | `OPENAI_API_KEY`, `CODEX_API_KEY`, `OPENAI_BASE_URL` | `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_CODE_USE_{BEDROCK,VERTEX,FOUNDRY}` |
| `session` | `thread.started.thread_id` | `system/init.session_id` |
| `settled` | `turn.completed`, text of the last `agent_message` item | `result` with `subtype: success`, `is_error: false` |
| `failed` (retryable) | `turn.failed` | `result` with an error subtype or `is_error: true` |
| Abort | SIGTERM | SIGTERM |

Codex exposes MCP tools to the model as namespace `mcp__supervisor`; Claude
Code names the tool `mcp__supervisor__ask_supervisor`. Both were verified with
the real binaries against scripted loopback models
(`tests/test_orchestrator_cli_e2e.py`). The Claude run authenticated with the
OAuth token as a bearer credential and sent no API key. `--setting-sources
user` keeps a repository's `.claude/settings.json` hooks from loading.
`--ignore-user-config` and `--ignore-rules` do the same for Codex user
configuration and exec-policy rules. A `test_endpoint` override exists only
for loopback URLs.

## Approvals

An approval binds the SHA-256 of the canonical JSON of task id, attempt id
(required), action, principal and controller policy version. `consume()` spends
one `approved`, unexpired approval of exactly that digest under the current
policy version, marking it `used`. It fails for a terminal task or a finished
attempt, so an approval never carries over to a retry. Changed
inputs, another attempt, another principal, a policy deployment or a second
use all need a new approval. Only the named principal may decide, once. These are
worker-level approvals (for example opening a PR against a repository).
Anything that triggers a CI deployment uses the existing
[approvals dashboard](../runbooks/approvals.md).

## Outbox topics

| Topic | Payload | Consumer |
| --- | --- | --- |
| `task.status` | `task_id`, `from`, `to`, `reason` for `claimed`, `needs_input`, `in_review`, `completed`, `failed`, `cancelled` | supervisor notification |
| `question.open` | `question_id`, `prompt`, `choices`, `blocking`, `revision` | supervisor, Forgejo comment |
| `question.expired` | `question_id`, `attempt_id` | supervisor |
| `worker.answer` | `attempt_id`, `question_id`, `answer` | worker adapter |
| `worker.cancel` | `attempt_id`, `reason` | worker adapter |
| `approval.open` | `approval_id`, `action`, `expires` | supervisor |

Messages for a worker carry their `attempt_id`; each runner reads only its
own (`attempt_messages`), so other attempts' backlog cannot starve it.
Consumers acknowledge after delivery. Delivery is at least once; consumers
deduplicate by key.

## Error codes

`INVALID_SPEC`, `NOT_FOUND`, `IDEMPOTENCY_CONFLICT`, `DEPENDENCY_CYCLE`,
`INVALID_TRANSITION`, `STALE`, `EVIDENCE_REQUIRED`, `EVIDENCE_REJECTED`,
`LEASE_LOST`, `ALREADY_ANSWERED`, `INVALID_ANSWER`, `FORBIDDEN`, `SCHEMA`.
