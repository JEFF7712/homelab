# Merge policy

The merge policy posts a `policy/merge` commit status on every open PR to
`main`. It replaces human review with automated website checks. The deployed
service runs with `--autonomous`: enrolled same-repository PR authors
(`homelab-agent`, `rupan`, `JEFF7712`) use the task’s standing authority.

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
- Authority findings, including merge-policy changes and intentional resource
  changes, are recorded without approval for enrolled same-repository authors.
  Repair findings still block delivery and must be fixed by the agent. Unknown
  authors and forks retain the conservative authority policy.

Replaying every merged website PR through the policy on 2026-10-26 approved
41 of 44. The three it held were real authority expansions: a Cilium egress
policy (uwagent), LAN exposure (bus-display) and a synced secret plus a
protection change (the Forgejo migration).

## Outcomes and recovery

| Status | Comment outcome | Who acts |
| --- | --- | --- |
| `success` | none | nobody; the PR merges when validation passes |
| `failure` | `rejected` | the agent: `just ship` prints the findings, the agent fixes and pushes |
| `failure` | `escalated` | legacy mode or external PR: exact-head operator review |
| `success` | `authorized` | enrolled same-repository author: authority findings recorded, no review |

`just ship` returns repair findings to the agent without escalating after an
arbitrary number of rejected heads. The agent repairs, commits and retries.
The `CODEOWNERS` file no longer creates mandatory operator review requests.
Validation and `policy/merge` remain required status checks.

The legacy deployed service can still require an exact-head operator approval
for the migration PR itself. That one-time UI step installs autonomous authority;
it is not a reason to bypass the existing branch protection. After deployment,
authority-only findings from enrolled same-repository authors pass automatically.
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
   The legacy `ci-host` route can require a transition approval before the
   autonomous policy is first deployed.
4. Shadow period: the status is posted but not required. Compare its
   verdicts with outcomes on real PRs (`journalctl -u merge-policy`).
5. Operator: add `policy/merge` to main's required status checks in Forgejo,
   and the agent adds it to `status_check_contexts` in
   `config/ci/forgejo-protection.json` so `just ship` and promotion
   auto-merge wait for it.
6. Agent: leave `CODEOWNERS` without mandatory operator review patterns.
   Activate `--autonomous` through the validated CI-host deployment.

## Operations

- Logs: `journalctl -u merge-policy` on homelab-04, one line per decision.
- Read-only check from a workstation with any read token:
  `python -m scripts.verifier.service --token-file <file> --state <dir> --once --dry-run`
  prints the decision for every open PR without posting.
- The service holds the policy token only through `LoadCredential`; it runs
  as the unprivileged `merge-policy` user with a strict sandbox.
- If the service is down while `policy/merge` is required, PRs wait rather
  than merge unchecked. Restart it with `systemctl restart merge-policy`.
