# Merge policy

The merge policy posts a `policy/merge` commit status on every open PR to
`main`. The operator gates spending and enforcement code
(`scripts/verifier/enforcement.py`); agents decide everything else. A head
passes on its own unless it changes enforcement code, unmarks a catalog
mutation, destroys stored data or opens host-level access. Those need the
operator's approval of that exact change on the
[approvals dashboard](approvals.md), the one place the operator approves
anything. Forgejo reviews grant nothing. Mistakes the agent can repair go back to it; constrained
exposure and unconventional implementations are allowed and recorded.

## What it decides

`scripts/verifier/websites.py` compares the committed website manifests at
the PR's merge base and head. It reads git objects and parses YAML; it never
runs the PR's renderer or kustomize, so what it judges is exactly what Flux
applies.

- A site's change is intended when its `site.yaml` or a handwritten file in
  its directory changes, or the site is new. Generated files changing without
  that (a renderer change) must leave the site's objects identical.
- Every new or changed object must be a vetted kind (Namespace,
  NetworkPolicy, Deployment, Service, PodDisruptionBudget,
  PersistentVolumeClaim, ConfigMap) and meet that kind's invariants:
  restricted namespace, isolated ingress, ClusterIP services, digest-pinned
  `registry.rupan.dev` images, non-root read-only hardened containers with
  bounded limits, prune-protected `nfs-cluster` claims. The check is a
  ratchet: an existing object may keep a problem it already had, so old debt
  never blocks a promotion, but it may not gain one.
- A head behind main fails with `behind main with overlapping changes: ...`
  when main's changes since the merge base overlap the PR's, by the scope
  rule in `scripts/verifier/freshness.py` (see
  [ship merge queue](ship.md#merge-queue)). The service re-judges every open
  PR whenever main moves. This outcome posts no comment: `just ship` updates
  the branch and revalidates.
- Enforcement code needs the operator: `ENFORCEMENT_PATHS` in
  `scripts/verifier/enforcement.py`, which covers the merge policy and that
  list, `scripts/ci/{policy,state,operation,deployment_risk}.py`, the
  Tofu state service, the approvals service, branch protection, the Woodpecker
  module and repository settings, `.sops.yaml` and the spending caps in
  `config/spend.json`. The list is closed on purpose: without it an agent
  could remove the spending gate in one merge. The agent guard, pre-tool
  hook, `CODEOWNERS`, `AGENTS.md` and service code are the agents' own.
- In `config/ci/operations.json`, unmarking a mutation needs the operator,
  because an unmarked operation skips the deployment gate. New agent
  operations and granting existing ones to agents are recorded notices.
- Website findings: hardening regressions are repairs; network exposure of a
  hardened site (wider ingress, a non-ClusterIP Service), helper kinds
  (CiliumNetworkPolicy, HPA, monitors) and removing a stateless site are
  recorded notices; host-level access (host network, host ports, privileged
  or host-path pods) reaches the node, and so the enforcement code on the CI
  host, and needs the operator, as do deleting a claim or a stateful site
  (until restore drills publish evidence) and kinds that grant identity,
  secrets or routing. StatefulSets, Jobs and
  CronJobs get the same pod checks as Deployments.

Replaying every merged website PR through the policy on 2026-10-26 approved
41 of 44. The three it held were real authority expansions: a Cilium egress
policy (uwagent), LAN exposure (bus-display) and a synced secret plus a
protection change (the Forgejo migration).

## Outcomes and recovery

| Status | Comment outcome | Who acts |
| --- | --- | --- |
| `success` | none | routine change or an operator-approved exact change; merge when validation passes |
| `failure` | `rejected` | the agent: `just ship` prints the findings, the agent fixes and pushes |
| `failure` | `escalated` | authority change: the operator approves the exact head on the approvals dashboard, regardless of author |
| `success` | `authorized` | nonblocking notices recorded; no operator review required |

`just ship` returns repair findings to the agent, which fixes, commits and
retries. After three rejected heads on one PR the bounded recovery is spent:
ship comments `@rupan` on the PR and stops.

## Operator approval

When a head escalates, the service files a merge request with the approvals
service (`POST /api/merges` on `127.0.0.1:8020`, authenticated with its own
`homelab-policy` Forgejo token, which the dashboard checks against Forgejo). The
request names the PR, the head, the findings and the head's versions of the
changed files a deployment checks. ntfy pushes the link; the status's details
link and the policy comment point at it; `just ship` prints it and waits.

- One request per head. A newer head supersedes a pending request for an older
  one; pending requests expire after seven days.
- The policy asks `GET /api/merges/<pr>` on every judgement and passes a head
  the operator approved, or a byte-identical update of an approved head (the
  same patch-id from its merge base, which updating the branch from main
  produces). Any new change, including a conflict resolution, files a new
  request.
- Approving merges nothing by itself: the policy re-judges within its interval
  and posts `success`, and ship merges.
- The approval also covers deploying those file versions. When the CI policy
  later holds the deployment for a grant, ship sends the deployed commit's file
  identities; the dashboard checks they reproduce the grant digest and approves
  the grant without a passkey when approved merges from the last seven days
  approved exactly those versions. Anything else (other versions, extra files,
  several unreleased protected changes) still asks for a passkey.
- If the approvals service is unreachable, judgements count as evaluation
  errors: nothing is posted for two attempts, then the head fails with the
  error. A passing status never flips because the dashboard is down. Restart
  `approvals` on homelab-04 to recover; nothing merges around it. The `CODEOWNERS` file creates no
review requests; validation and `policy/merge` remain required status checks.
After three evaluation errors on one head the service still reports failure
with diagnostic evidence, rather than leaving the PR silently pending.

## Activation

Steps 1 to 4 are done (2026-10-26; shadow verdicts matched on every open PR). Steps 5 and 6 land together. In order:

1. Operator, in Forgejo: create the `homelab-policy` user and add it to
   `JEFF7712/homelab` as a collaborator with write access, which commit
   statuses require. As that user, create a token with exactly `read:user`,
   `read:repository`, `write:repository` and `write:issue`.
2. Operator: store the token in the host secrets without printing it:
   `sops --set '["merge-policy-token"] "<token>"' secrets/hosts/homelab-04.sops.yaml`
   (or `sops secrets/hosts/homelab-04.sops.yaml` and add the key), then commit
   the encrypted file. Agents never see the plaintext.
3. Agent: set `homelab.mergePolicy.enable = true` for homelab-04 and ship.
4. Shadow period: the status is posted but not required. Compare its
   verdicts with outcomes on real PRs (`journalctl -u merge-policy`).
5. Operator: add `policy/merge` to main's required status checks in Forgejo,
   and the agent adds it to `status_check_contexts` in
   `config/ci/forgejo-protection.json` so `just ship` and promotion
   auto-merge wait for it.
6. Agent: leave `CODEOWNERS` without mandatory operator review patterns; the
   boundary is enforced by this status and the deployment holds, not review
   requests.

## Operations

- Logs: `journalctl -u merge-policy` on homelab-04, one line per decision.
- Read-only check from a workstation with any read token:
  `python -m scripts.verifier.service --token-file <file> --state <dir> --approvals http://ci.internal:8020 --once --dry-run`
  prints the decision for every open PR without posting.
- The service holds the policy token only through `LoadCredential`; it runs
  as the unprivileged `merge-policy` user with a strict sandbox.
- If the service is down while `policy/merge` is required, PRs wait rather
  than merge unchecked. Restart it with `systemctl restart merge-policy`.
