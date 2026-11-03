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
Completion has three separate stages. `transition()` never enters
`in_review` or `completed`; it only moves `in_review` back to `validating`
for re-verification:

1. **Worker-reported.** `settle_attempt()` moves `running` to `validating`.
   The worker says it is done; nothing is proven.
2. **Verified.** `record_verification()` stores the controller verifier's
   `Evidence` (a pull request, a full 40-character head SHA, a Woodpecker
   state of `success`, every acceptance criterion) and moves `validating` to
   `in_review`. Evidence that fails any check is refused and the task stays in
   `validating`.
3. **Finalized.** `complete()` takes no evidence, only the head SHA the
   controller has just read from the pull request. It re-checks the stored
   verification, refuses (`EVIDENCE_REJECTED`) a head that differs from the
   verified one, and moves `in_review` to `completed`.

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
`WorkerChannel` with nine operations: `start`, `renew`, `ask`, `inbox`,
`acknowledge`, `session`, `submit`, `settle`, `finish`. In a worker pod that
channel is `HttpChannel`, a client of `scripts/orchestrator/worker_api.py`,
which also serves `source`:

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
  and `final_text`, never a branch or commit, and a bound attempt cannot
  settle before it has submitted its patch. `Content-Length` must be a
  non-negative integer of at most 64 KiB (4 MiB for `submit`).
- `source` returns the attempt's bound base commit as a tar archive, with no
  Git metadata. `submit` takes the attempt's binary patch (raw bytes) once; a
  different second patch is `CONFLICT`. These replace the forge: a worker
  holds no forge credential, has no Git remote and no forge egress.
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
| `task.status` | `task_id`, `from`, `to`, `reason` for `claimed`, `needs_input`, `in_review`, `completed`, `failed`, `cancelled` | sinks, clients |
| `question.open` | `question_id`, `prompt`, `choices`, `blocking`, `revision` | sinks, clients |
| `question.expired` | `question_id`, `attempt_id` | sinks, clients |
| `worker.answer` | `attempt_id`, `question_id`, `answer` | the attempt's runner |
| `worker.cancel` | `attempt_id`, `reason` | the controller's Job manager |
| `approval.open` | `approval_id`, `action`, `expires` | sinks, clients |

Messages for a worker carry their `attempt_id`; each runner reads only its
own (`attempt_messages`), so other attempts' backlog cannot starve it.
`worker.cancel` is acknowledged by the controller after it stops the Job.
Notification topics fan out: every sink and every client has its own
delivery record (`sink_deliveries`, schema v2), so one consumer's failure
neither blocks nor hides a message from another. Delivery is at least once;
consumers deduplicate by key.

## Controller loop

`scripts/orchestrator/controller.py` reconciles from durable state on every
tick (`python -m scripts.orchestrator serve --config FILE`,
`scripts/orchestrator/daemon.py`), so stopping it at any instant is safe:

1. Expire overdue questions; reap expired leases. On its first tick a
   controller first extends every unfinished attempt's lease by one lease
   period, so workers that waited out the restart (they retry an unreachable
   controller for up to 10 minutes) are not reaped for leases nobody could
   renew.
2. Fence: stop the Job of every pending `worker.cancel`, then acknowledge it.
   The Job is deleted with foreground propagation, so it stays visible until
   its Pods are gone, and the cancel is acknowledged only once the Job is no
   longer running. A stop the Kubernetes API does not confirm (DELETE other
   than 200, 202 or 404) or a Pod still terminating leaves the cancel pending
   for the next tick.
3. Reconcile: an unfinished attempt whose Job is observed missing or done is
   finished as retryable. An attempt whose Job state cannot be read is left
   alone.
4. Ensure every open task has its forge issue.
5. Dispatch ready tasks under the stored cap: claim with a first lease of
   `startup_seconds` (default 600, covering scheduling and image pulls),
   mint the worker token,
   start the Job with a job file holding the brief, the token and the
   attempt branch `agent/<task>/<attempt number>`, and bind the attempt to
   that branch and to the base branch's current commit, read by the
   publisher before the claim (`artifacts`, schema v3). A failed start is only
   logged: the Job may exist anyway, so reconciliation finishes the attempt
   once the Job is observed missing. A Job whose Secret cannot be created is
   deleted at once.
6. Publish and verify `validating` tasks (see "Publishing" below): publish
   the attempt's patch, ensure the pull request for the attempt branch, read
   CI and reviews for the published commit
   (`scripts/orchestrator/verifier.py`), `record_verification`, then re-read
   and re-verify before `complete`. Failed CI, a missing or rejected patch, or
   a pull request head that is not the published commit sends the task back
   to `ready` until attempt 3, then `failed`. A task left in `in_review` by a
   crash is finalized only after a fresh read shows the published commit
   still passing; otherwise it goes back to `validating`.
7. Apply issue commands from the task principal's forge login only:
   `/answer <question> <revision> <text>`, `/confirm <proposal>`,
   `/approve <id>`, `/decline <id>`, `/cancel`. Each comment is processed once (`forge_cursors`, schema v2);
   rejected commands become `command.rejected` events. Comments by the
   controller's own login, or carrying its message marker, never count: they
   quote worker text.
8. Deliver notifications to each sink (forge comments, ntfy) within a
   per-tick budget (`delivery_budget_seconds`, default 10). The daemon does
   not hold the store lock across a tick, so a slow sink never blocks the
   worker API.

Acceptance criteria are checked without worker input: `ci:<context>` needs
that status context to succeed on the pull request head; any other criterion
needs an approving review of that exact head from the principal's forge
login. CI is read from every status on the head (`/commits/<sha>/statuses`,
all pages), keeping the newest per context among those posted by the
configured `verifier.ci_logins`. Anyone with repository write access, which
includes every worker, can post a status, and the forge's combined view
keeps only the newest per context, so statuses from other accounts are
ignored rather than trusted. With no CI login configured nothing passes.
Reviews are read across all pages; the newest per reviewer counts.

### Forge writes and reconciliation

`scripts/orchestrator/forge.py` makes every write findable again, so a
controller that dies just before or just after a write converges to one
object: issues carry `<!-- orchestrator:task=<id> -->` and the
`orchestrator` label and are found by label scan before creation; pull
requests are unique per head branch and looked up by base and head first
(a 409 on create is resolved by the same lookup); comments carry
`<!-- orchestrator:message=<outbox key> -->`. A marker counts only as the
final line of an issue or comment authored by the controller's own login
(`GET /api/v1/user`), and controller comments break `<!--` in the text they
quote, so worker text reflected through the controller cannot carry one.
Another account therefore cannot pre-post a marker to hijack an issue or
suppress a notice. Only the publisher writes Git; the controller opens pull
requests and reads CI. Nothing merges: workers produce reviewable pull
requests, and the controller has no merge call.

### Publishing

`scripts/orchestrator/publisher.py` is the only component with a forge write
credential. It runs in the controller pod, which workers reach only through
the worker API, and its token reaches git through the environment for one
command at a time, never argv or a file. For a settled attempt it:

1. refuses an empty patch, one over 4 MiB, one with a `delta` binary hunk
   or a `literal` hunk over 8 MiB, one that does not apply to the bound base
   commit, one whose resulting files exceed 8 MiB each or grow the tree by
   over 32 MiB, one that touches a protected path, and one that adds a
   symlink or submodule. Protected paths are matched case-insensitively
   against every path suffix, so they apply at any depth: any `.git`
   component, `.gitattributes`, `.gitmodules`, `CODEOWNERS`, CI and forge
   configuration (`.woodpecker*`, `.forgejo/`, `.gitea/`, `.github/`,
   `.gitlab-ci.yml`, `.pre-commit-config.yaml`), and agent configuration
   (`.claude/`, `.codex/`, `.cursor/`, `.opencode/`, `.mcp.json`, `.envrc`,
   `AGENTS.md`, `CLAUDE.md`), plus any per-repository patterns. There is no
   override; such changes need a human pull request;
2. applies the patch to a temporary index of the base commit with
   `git apply --cached` under a 512 MiB address-space limit, so nothing is
   checked out, filtered or executed, hooks are disabled, and a hunk that
   inflates cannot exhaust the controller;
3. commits the tree with a fixed identity and the submission time, so the
   same patch on the same base is always the same commit;
4. pushes it to `agent/<task>/<attempt>` (the only branches it accepts) with
   `--force-with-lease=<branch>:`, so the branch must not exist. A rejected
   push succeeds only if the branch already holds exactly that commit (a
   crash between push and record); a different commit there is a conflict
   and voids the attempt; no branch at all means the push failed for
   another reason and is retried.

The base archive served by `source` is built once per commit (at most
128 MiB of blobs), one build at a time, and served from disk afterwards.

The publisher records the pushed commit (`record_publication`). From then on
verification is bound to it: the verifier fails a pull request whose head is
any other commit, `record_verification` and `complete` refuse evidence about
any other commit, and the stored evidence lists the exact statuses
(context, state, creator, status id) and reviews (login, review id) it
relied on. Finalization re-reads the pull request and re-runs the checks
immediately before `complete`.

Merging stays manual. Forgejo 15.0.10's merge endpoint accepts
`head_commit_id` and compares it with the head it fetched into the merge
clone, which it then merges, so an autonomous merge could be bound to the
verified commit; that capability is not enabled.

## Client API

`scripts/orchestrator/client_api.py` is the only surface a supervising agent
uses, and it knows no particular agent. Each bearer token (stored as a
SHA-256) maps to one client name and one principal. Client names are unique,
and `forge` and `ntfy` are reserved for the sinks. A client acts only on its
principal's tasks in the configured project: `create_task` (idempotency key
required), `get_task`, `list_tasks`, `cancel_task`, `answer_question`, and
`events`/`ack` for its own notification feed. It cannot decide approvals;
those come from the human principal on the forge. The controller binds it to
loopback; the bridge runs beside it.

A client is an agent with state-changing tools, so worker-originated text is
kept out of it in application code, not by prompting:

- No response carries text a worker wrote. Task views list open questions
  as `question_id`, `revision` and `choice_count`; attempts as `number` and
  `finished`. Events carry only the fields in `EVENT_FIELDS` (identifiers
  and controller states): `task.status` `from`/`to`, `question.open`
  `question_id`/`revision`/`blocking`, `question.expired` `question_id`,
  `approval.open` `approval_id`/`expires`. Question prompts, choices,
  failure reasons, final text and approval descriptions go only to the human
  channels (the forge issue and ntfy).
- `answer_question` does not answer. It validates the question, revision and
  choice and records a proposal (`answer_proposals`, schema v4), which the
  forge and ntfy sinks show as `answer.proposed` with its `/confirm <id>`
  command. The principal's `/confirm` on the issue answers the question,
  only if the proposal is pending, belongs to that task, and the question is
  still open at the proposed revision with a live attempt. A stranger's or
  the controller's own `/confirm` is ignored like any other command.
- Errors carry their code and a fixed sentence (`ERROR_TEXT`), never the
  store's message, which can quote stored values.
- Since no worker text reaches the supervisor, none can be promoted into its
  persistent memory either.

`scripts/orchestrator/letta_bridge.py` is one such client. It registers those
operations as Letta external tools in `runtime_start`, answers
`external_tool_call_request` from the client API, re-registers after every
reconnect, and delivers events as `input`/`create_message` user turns wrapped
in `<system-reminder>` with the outbox key as `client_message_id`. Those turns
render identifiers and controller states only (anything else becomes
`(invalid)`), plus the principal's own task title, quoted so it cannot close
the wrapper. The bridge acks an
event only after Letta answers its `input` with `input_accepted` and
`accepted: true`; a rejection or no answer within `accept_seconds` leaves
the event pending for redelivery, and a failed ack is retried on the next
poll. Replacing Letta replaces only this file.

## Error codes

`INVALID_SPEC`, `NOT_FOUND`, `IDEMPOTENCY_CONFLICT`, `DEPENDENCY_CYCLE`,
`INVALID_TRANSITION`, `STALE`, `EVIDENCE_REQUIRED`, `EVIDENCE_REJECTED`,
`LEASE_LOST`, `ALREADY_ANSWERED`, `INVALID_ANSWER`, `FORBIDDEN`, `SCHEMA`,
`CONFLICT`, `UNREACHABLE`.
