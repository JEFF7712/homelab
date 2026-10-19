# projectctl v0.1 plan and authorization protocol

Status: implementation contract, 2026-10-16. The CLI, worker, service adapters,
signing tools and disabled Nix profile are implemented locally. No live worker
activation or pilot enrollment has occurred. The
[runbook](../../runbooks/projectctl.md) records current rollout prerequisites.

## Outcome and scope

Onboard one existing local Python project using the `python-uv-v1` contract.
Verify repository identity, approved visibility, CI enrollment, webhook delivery,
and validation of the exact planned commit. Interrupt execution after repository
creation and resume without duplicate resources or expanded permissions.

The local CLI describes intent. An independent operator authorizes a bounded
plan. A trusted worker performs fixed operations and verifies their effects.
Forgejo owns Git, Woodpecker owns builds, NixOS owns host and policy deployment,
and Flux owns Kubernetes reconciliation.

Defer GitHub mirror provisioning, templates, publication, application deployment,
secret provisioning, automatic resource deletion, HTTP/MCP transports, and
distributed workers. Existing mirrors can be diagnosed read-only. Ordinary
onboarding acceptance does not require a platform disaster-recovery drill.

## Existing owners and constraints

| Concern | Current owning source | Integration rule |
| --- | --- | --- |
| Approved development identity and CI contract | [projects.json](../../../config/ci/projects.json) | Protected changes retain authority |
| Development validation | [development_projects.py](../../../scripts/ci/development_projects.py) | Select an existing contract; repository configuration cannot select privileges |
| CI admission and privileged operations | [policy.py](../../../scripts/ci/policy.py), [operations.json](../../../config/ci/operations.json) | Add fixed operations through reviewed platform changes |
| Immutable policy deployment | [woodpecker.nix](../../../flake/modules/woodpecker.nix) | Observe deployed policy before verification; registry edits alone do not activate admission |
| Supported onboarding APIs | [services.py](../../../scripts/projectctl/services.py) | Fixed operations replace the retired migration entry point |
| Local task evidence | [agent workflow](../../agent-workflow.md) | Keep implementation-task evidence separate from authoritative worker receipts |

The retired migration script generated an all-scopes Forgejo token, derived a
Woodpecker token from database material, embedded a registry credential, and
obtained credentials even for dry runs. It recorded completion and deployed policy
before final verification. Its CI check matches a branch without binding the
planned source SHA. These are source observations, not a credential-validity or
exposure audit. Do not copy credential values into plans, tests, or audit notes.

## Trust boundary and approval provenance

Restricted callers have no administrative tokens, host shell or sudo access,
worker database access, signing key, privileged runner access, or infrastructure
kubeconfig. Repository contents and local configuration are untrusted input.
The worker executes no repository code and accepts no commands, credential
paths, executable paths, arbitrary URLs, or caller-selected service endpoints.
The Python contract runs project code only in the existing sandbox CI tier.

This boundary applies to restricted callers. An agent retaining the homelab's
existing deployment authority can bypass it by other means. Introducing
projectctl does not revoke existing privileges or make the whole lab isolated.

Current branch protection requires validation but does not configure a required
human approval. `policy.py` admits `homelab-agent` as a deployment operator.
A merged file or a successful CI job therefore cannot, by itself, authorize a
projectctl mutation.

For v0.1, require BOTH a merged approval record on protected homelab main and
an operator Ed25519 signature over its complete authorization payload. The
operator signing key must be unavailable to the requesting agent. The worker's
trusted public keys and service bindings are installed separately by an
authorized operator, outside caller-writable storage. No keys are generated or
enrolled implicitly. Provisioning this trust root is a deployment prerequisite.

Store future approval records under `config/projectctl/approvals/`. This is a
proposed location, not an existing catalog. The worker reads the current main
revision from its configured Forgejo identity, never a caller-provided checkout,
URL, branch, approval boolean, or review description. A signed record must still
exist unchanged on current main. Removal or replacement withdraws that grant.
Failure to refresh authority blocks new external mutations.

## Data contracts and identity

All protocol documents have `schema_version: 1`, reject unknown fields, and use
UTF-8 JSON. Identifiers and operation parameters use explicit allowlists.
Timestamps are UTC RFC 3339 seconds with `Z`. Digests are `sha256:` followed by
64 lowercase hexadecimal characters. Git object IDs carry their object format
and full hexadecimal value; the first adapter supports SHA-1 repositories only
and rejects other formats explicitly.

For hashed and signed payloads, allow ASCII object keys and strings, booleans,
null, arrays, and integers within the interoperable exact range. Reject floats,
duplicate JSON keys, and noncanonical identifiers. Canonical bytes are JSON with
sorted object keys, no insignificant whitespace, and no trailing newline.
The integer range is negative (2^53 - 1) through positive (2^53 - 1). Use
the literal ASCII encoding of strings, with JSON escapes for control characters,
double quotes, and backslashes. Restrict strings to printable ASCII in these
payloads. Human diagnostic messages are outside the hashed payload.

Hash a payload as SHA-256 of its canonical bytes. `plan_digest` is external to
the plan payload. The operator signs the canonical approval payload prefixed
with the literal bytes `projectctl.approval.v1\n`. Signature metadata contains
only the trusted key ID and base64-encoded signature. Executable schemas and
cross-process canonicalization test vectors are required before implementation
can accept a live approval.

| Document | Required content |
| --- | --- |
| Project specification | Stable project key, Forgejo owner/name, explicit visibility, default branch, exact source commit, `python-uv-v1`, and requested readiness capabilities |
| Plan | Specification, authenticated intended principal, platform binding, effective policy digest, scoped discovery observations, and ordered operation DAG |
| Operation | Unique ID, fixed kind, typed parameters, dependencies, preconditions, postconditions, and declared output types |
| Submission | Canonical plan, its digest, and caller-generated idempotency key |
| Approval payload | Unique grant ID, server request ID, plan digest, principal, project key, platform binding, policy digest, allowed operation IDs, `not_before`, `expires_at`, original `catalog_revision`, and `catalog_entry_digest` |
| Request record | Server-assigned request ID, authenticated principal, plan digest, submission key, request state, operation states, and current blockers |
| Receipt | Request and operation identity, attempt number, actual resource IDs, sanitized observations, verification evidence, timestamps, worker version, policy digest, and approval reference |

The platform binding identifies the configured Forgejo instance, Woodpecker
instance, and owning homelab repository. The effective policy digest covers
applicable permission limits, operation implementations, CI contract semantics,
service bindings, and approval rules. Unrelated catalog entries need not change
it. Policy changes affecting this scope require a new plan and approval.
Approval verification rules and trusted keys cannot be selected by a plan.

The plan separately declares allowed registry transitions: absent identity to
approved pending identity, then binding that identity to verified allocation
outputs. These exact effects do not count as policy drift. The worker compares
the full affected entry before accepting each transition. Changes to contract,
visibility, permission limits, unrelated identities, or unverified IDs are not
allowed deltas. Record actual merged revisions and deployed package identities
as evidence, separately from the effective policy digest.

Local paths and descriptive language preferences are discovery inputs, not
worker parameters. Python preferences may use `[tool.projectctl]` in
`pyproject.toml`; they cannot authorize visibility changes or privileged CI.
Requested visibility becomes authoritative only through the protected registry.
Observed resource IDs remain receipts until protected enrollment binds them.

## Operation dependencies and ownership

An operation may reference a prior output using a typed pair of producer
operation ID and output name. The worker resolves this only from its own verified
receipt. Unknown producers, cycles, type mismatches, and references to caller
supplied receipts are rejected. Resource IDs assigned during execution do not
change the original plan digest.

| Fixed operation | Preconditions and verified result |
| --- | --- |
| `forgejo.repository.ensure` | Approved owner/name and visibility; absent target or explicitly approved existing numeric ID; records exact repository identity and visibility |
| `forgejo.clone_reader.ensure` | Verified repository output; configured read-only clone identity; verifies its repository permission |
| `woodpecker.repository.ensure` | Verified Forgejo numeric ID and owner/name; activates through supported API and verifies the mapping and privacy metadata |
| `woodpecker.pull_access.ensure` | Verified enrollment; installs only the configured registry read capability from protected secret storage and verifies its configuration without returning its value |
| `catalog.enrollment.prepare` | Produces a narrow local patch/evidence artifact containing verified IDs and approved contract; never pushes, merges, or deploys |
| `source.observe` | Waits read-only for the exact planned branch tip in Forgejo |
| `policy.observe` | Waits read-only for merged enrollment and evidence that the immutable deployed policy includes the expected mapping and contract |
| `ci.trigger` | Exact source and deployed admission verified; requests one fixed validation event for that commit |
| `readiness.verify` | Reads exact repository, webhook, policy, workflow, source, and branch-protection evidence; records capability results |

The DAG creates the repository before configuring clone access and activating
Woodpecker. Pull access and the enrollment artifact depend on verified
Woodpecker enrollment. CI triggering depends on both source and policy
observation. Final readiness depends on successful validation and the required
branch protection. The reviewed enrollment change configures protection through
existing platform ownership after the matching status context has been proven;
if that needs a second protected change, the request waits for it.

For v0.1, source transfer uses the caller's existing project-scoped Git identity
and an explicitly authorized push of the planned branch. The worker neither
accepts local filesystem paths nor imports caller bundles. Planning reports
whether that Git identity is available. No identity is invented or enrolled.
The caller's push authorization is separate from worker API authority. Additional
branches, tags, history rewrites, remote reconfiguration, and source updates
require separately scoped work. A dirty checkout is reported; only the named
commit is in scope.

An existing resource is never adopted solely because its name matches. The plan
must bind its observed instance, numeric ID, owner/name, and required settings.
An absent target that appears during a request is an ownership conflict unless
the worker has a verified creation receipt or can uniquely reconcile its own
uncertain attempt. Name equality alone cannot establish who created it.
If the API cannot distinguish a concurrent creation from an uncertain success,
stop for inspection and a separately approved adoption plan.

## Request, approval, and execution

1. The CLI discovers local Git state and requests scoped read-only remote
   discovery. Planning never mints credentials or calls write APIs. Missing
   discovery produces an explicit blocker, never invented IDs or assumed absence.
2. `request` authenticates the caller and stores the proposed plan. The worker
   independently validates its schema, policy, identity, and live preconditions.
   Client observations do not constitute authority.
3. An operator reviews the exact plan and signs the bounded approval for its
   server-assigned request ID. Publishing that record and the required registry
   intent is a protected homelab change.
4. An explicit authorized dispatch starts the on-demand worker. An authenticated
   submission or status read cannot dispatch mutations.
5. The worker takes the project lock, verifies approval and preconditions, then
   executes allowed operations with durable attempt records and receipts.
6. After allocation it emits verified enrollment evidence and waits for the
   protected registry change and its authorized policy deployment. The worker
   cannot silently modify policy or deploy homelab-04 to unblock itself.
7. Source and deployed policy must agree before CI runs. Final verification
   records readiness independently for each requested capability.

The signature binds all listed operation IDs, including allocation-dependent
operations. It authorizes only their typed references and original parameters.
New operations or parameters require a new approval. Grants may be renewed for
the same request and plan with a new signed grant ID; completed operations are
not replayed. A grant cannot be reused for a different request, including a
new request with the same plan digest.
The server records authenticated identity from transport credentials, never from
the submission's claimed principal. Caller scope also governs request visibility.

Before every external write, refresh approval presence, time validity, current
policy, cancellation state, and that operation's resource preconditions. Check
expected postconditions of successful dependencies, rather than comparing every
step against the original empty world. Repeated writes to the same resource use
supported conditional updates; otherwise re-read immediately before dispatch
and report the residual external-writer race. Internal locks cannot serialize
manual administrators or other service clients.

Authorization is checked immediately before dispatch, not atomically with an
external API. Revocation or cancellation cannot retract an already dispatched
write. Finish only read-only outcome discovery for it, record the result, and
stop later writes. An expired approval permits inspection, never corrective
mutation. No fallback to personal accounts, database writes, or root shell.

## States, journal, and recovery

Use a single trusted worker host, local SQLite database, and a host-owned
process lock covering execution. Serialize requests for the same project and
enforce unique active instance/repository ownership in the database. Do not use
network-filesystem SQLite, distributed leases, or a database transaction held
open over an external API call.

| Request state | Meaning |
| --- | --- |
| `awaiting_authorization` | Valid request without a currently usable grant |
| `queued` | Explicit dispatch accepted; execution has not started |
| `running` | Worker is inspecting or executing operations |
| `waiting` | External source, protected enrollment, deployed policy, or CI evidence is pending |
| `blocked` | Ownership conflict, stale plan, lost authority, or another condition requiring intervention |
| `failed` | A definite nonrecoverable operation failure occurred |
| `cancelled` | Cancellation recorded; no further writes may be dispatched |
| `succeeded` | All requested onboarding capabilities have fresh passing evidence |

Operation states are `pending`, `running`, `uncertain`, `waiting`, `verified`,
`blocked`, and `failed`. Persist intent and increment the attempt before the
API call. Persist observations and verification afterwards. A crash leaving a
`running` attempt is recovered as `uncertain`. A timeout is not a definite
failure. Retry only after read-only discovery distinguishes success, absence,
and conflict. Retryable failures carry bounded backoff and a next-attempt time;
an unsafe unknown outcome never permits blind replay.

The same principal and idempotency key with the same plan returns the existing
request. Reusing that key for another plan returns `IDEMPOTENCY_CONFLICT`.
A new key cannot bypass an existing active request for the same resource.
Cancellation is authenticated, durable, and terminal for that request. New work
requires a new request and approval; old grants cannot resurrect a cancelled
request. `status` and `doctor` never resume it.

Receipts record grant ID, signed payload digest, protected revision, actual IDs,
preconditions, timestamps, sanitized outcome, and verification references. Do
not retain tokens, request authorization headers, credential URLs, raw service
bodies, environment dumps, or repository-controlled text. Allowlist evidence
fields and bound response sizes. Protect journal and backups as operator-only.
Losing the journal disables automatic adoption/resumption until operator
reconciliation restores trusted ownership evidence. Retained public resources
are not automatically deleted on failure, cancellation, or expiry.

## CLI and errors

These commands are implemented; a configured restricted worker is required:

| Command | Effect |
| --- | --- |
| `projectctl adopt PATH --ci python-uv-v1 --plan --json` | Read-only discovery and candidate plan |
| `projectctl plan SPEC --json` | Read-only desired/observed comparison |
| `projectctl request PLAN --idempotency-key KEY --json` | Authenticated submission and journal mutation only |
| `projectctl status REQUEST --json` | Read-only request, operation, and evidence report |
| `projectctl doctor PROJECT --json` | Read-only current capability diagnosis; any corrective plan needs separate approval |
| `projectctl cancel REQUEST --json` | Authenticated cancellation recorded in the journal; no resource deletion |

No caller-facing `apply` in v0.1. Worker dispatch is an operator action separate
from request admission. The execution core is transport-independent; v0.1 uses
a restricted SSH forced-command gateway and separate operator-only dispatch.
No unrestricted SSH or caller-controlled job command is acceptable.

JSON stdout contains one versioned envelope with `ok`, `data`, and `error`;
exactly one of data and error is non-null. Diagnostics use stderr. Exit 0 means
the command completed, including a successful status read of a blocked request;
2 means invalid input; 3 means rejected authority; 4 means conflicting state;
5 means unavailable dependency; 1 means internal failure. Request state is
separate from command exit status. Agents must inspect both.

Errors contain `code`, affected operation ID when available, bounded reason,
`retryable`, and a typed recovery action with authorization requirements.
Initial codes: `INVALID_SPEC`, `AUTHORIZATION_REQUIRED`,
`AUTHORIZATION_EXPIRED`, `AUTHORIZATION_REVOKED`, `POLICY_CHANGED`,
`PLAN_STALE`, `RESOURCE_CONFLICT`, `IDEMPOTENCY_CONFLICT`,
`OUTCOME_UNCERTAIN`, `DEPENDENCY_UNAVAILABLE`, `SOURCE_MOVED`,
`CI_FAILED`, and `VERIFICATION_PENDING`. A retryable error never grants mutation
authority or permission to rewrite Git history.

## Readiness and evidence

| Capability | Required evidence |
| --- | --- |
| Repository | Instance and numeric identity, owner/name, approved visibility, default branch, and exact source tip |
| CI enrollment | Woodpecker numeric ID mapped to the exact Forgejo identity, verified clone and pull configuration, merged enrollment revision, deployed policy identity and matching contract |
| CI validation | Successful expected workflow and Forgejo status context, exact source SHA/ref, repository identity, pipeline ID/event, effective policy identity, and observed delivery of a real push webhook |
| Branch protection | Expected validation context is required for the approved default branch; enforcement verified through an authorized acceptance probe |
| Optional existing mirror | Observation time, expected refs, observed refs, lag/divergence, and dependency availability, reported independently |

A manual run alone cannot prove webhook delivery. A successful older pipeline
on the same branch cannot prove the planned commit passed. If main advances,
retain evidence for the old SHA but report `SOURCE_MOVED`; do not silently
retarget the request. New source needs a new plan. Successful onboarding remains
historical evidence; later doctor results may be degraded or unknown.
Every observation has a timestamp, source identity, and policy-defined maximum
age. Unavailable or stale evidence is unknown, never healthy. Optional mirror
outages cannot block requested local Git/CI readiness unless explicitly required.

## Implementation sequence and acceptance

1. Audit legacy credential exposure without printing values. Remove hardcoded
   credentials and broad acquisition from any reusable path. Rotation, consumer
   updates, revocation, publication, and deployment require their own authorized
   change. Planning must be credential-free for writes from its first release.
2. Implement typed schemas, canonical bytes/digest/signature verification,
   operation DAG validation, pure planner, and stable error envelopes. Use
   in-memory adapters and offline fixtures before adding network mutations.
3. Implement the local worker journal, ownership and idempotency constraints,
   recovery, cancellation, and approval checks against fake services. Wire the
   CLI to the same core; do not create a second authorization implementation.
4. Add read-only adapters, then fixed Forgejo and Woodpecker operations through
   supported APIs. Prove prerequisite credential scopes and project isolation
   before enabling writes. If required scoping or APIs are unavailable, block
   that operation and specify a bounded alternative for review.
5. Review the concrete worker host, authentication, operator key custody,
   protected dispatch, scoped credentials, revocation access, backup procedure,
   deployment diff, and rollback. Obtain deployment authorization for this
   concrete implementation, then run the pilot on an explicitly selected repo.

| Acceptance case | Required result |
| --- | --- |
| Plan only | No write API, token minting, source mutation, or service deployment; missing discovery is explicit |
| Canonical documents | Equivalent payloads hash identically; altered fields, duplicate keys, unsupported versions, and malformed DAGs are rejected |
| Approval forgery | A caller-supplied boolean, forged signature, wrong key/principal/platform, or agent-merged unsigned record cannot authorize a write |
| Stale/revoked approval | Policy change, expiry, removal, or parameter tampering stops writes; read-only outcome discovery remains possible |
| Allocation outputs | Later operations accept only typed outputs from the worker's verified receipts |
| Client disconnect | After verified repository creation, the client can reconnect and retrieve the same request without duplicating resources |
| Worker crash/timeout | Success before receipt persistence becomes uncertain; discovery proves ownership or blocks, with no blind replay |
| Concurrent requests | Same request retries coalesce; different plans/keys cannot race for the same project or adopt an unknown repository |
| Protected enrollment | Worker waits for verified IDs to be merged and deployed; an edited manifest alone cannot advance CI admission |
| Source transfer | Only approved commit/ref is accepted; absent credentials or moved source produces a blocker |
| Exact-SHA validation | Older green branch runs are rejected; CI, status context, policy, and source agree |
| Webhook and protection | Real source push triggers expected validation; failing authorized probe prevents protected merge |
| Cancellation | No later writes; in-flight uncertain outcomes are inspected and retained resources are reported |
| Credential isolation | Repository-selected commands cannot reach worker credentials; logs, JSON output, journal, and backups contain no credential values |
| Optional mirror outage | Local Git/CI capabilities remain independently reportable |

Record operation attempts, queue and execution time, recovery outcomes, policy
rejections, and evidence identities from the start. Agent token accounting
belongs to the evaluation harness. Compare interfaces later using equivalent
effective permissions, identical tasks/failures, and reported maintenance cost.

## References

- [CI execution and authority](../../runbooks/ci-pipelines.md)
- [Application CI](../../runbooks/application-ci.md)
- [Agent workspace boundaries](../../runbooks/agent-workspaces.md)
- [Sovereign application lifecycle RFC](../../research/2026-10-08-sovereign-application-lifecycle-rfc.md)
