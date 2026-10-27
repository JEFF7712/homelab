# Merge policy

The merge policy posts a `policy/merge` commit status on every open PR to
`main`. It replaces routine human review of website interface changes
(renderer, schema, ejection) with a check the agent cannot change, and keeps
the operator for changes that need more authority.

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
- Changes under `scripts/verifier/`, `flake/modules/merge-policy.nix`,
  `config/ci/forgejo-protection.json` or `CODEOWNERS` govern merges and
  always need the operator.

Replaying every merged website PR through the policy on 2026-10-26 approved
41 of 44. The three it held were real authority expansions: a Cilium egress
policy (uwagent), LAN exposure (bus-display) and a synced secret plus a
protection change (the Forgejo migration).

## Outcomes and recovery

| Status | Comment outcome | Who acts |
| --- | --- | --- |
| `success` | none | nobody; the PR merges when validation passes |
| `failure` | `rejected` | the agent: `just ship` prints the findings, the agent fixes and pushes |
| `failure` | `escalated` | the operator: approve that exact commit, or ask for a narrower change |

`just ship` escalates on its own after three rejected heads on one PR by
requesting the operator's review. Authority findings (site or claim
deletion, unvetted kinds, wider ingress, non-ClusterIP services, host
networking, policy paths) escalate immediately. An operator approval
overrides for the commit it names. Every push or branch update produces a new
head with no status, which the policy judges afresh; an earlier approval
carries over only when the PR's own diff from its merge base is
byte-identical (same `git patch-id`), which is what updating a branch from
main produces. A new change or a conflict resolution needs a new approval.
After three evaluation errors on one head the policy posts a failure saying
so, rather than leaving the PR silently blocked.

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
   The `ci-host` route is held on the approvals dashboard because
   `scripts/verifier/` and the module are authority paths.
4. Shadow period: the status is posted but not required. Compare its
   verdicts with outcomes on real PRs (`journalctl -u merge-policy`).
5. Operator: add `policy/merge` to main's required status checks in Forgejo,
   and the agent adds it to `status_check_contexts` in
   `config/ci/forgejo-protection.json` so `just ship` and promotion
   auto-merge wait for it.
6. Agent: drop the `scripts/websites/` and `tests/test_website_*` lines from
   `CODEOWNERS`, ending routine human review of renderer changes.

## Operations

- Logs: `journalctl -u merge-policy` on homelab-04, one line per decision.
- Read-only check from a workstation with any read token:
  `python -m scripts.verifier.service --token-file <file> --state <dir> --once --dry-run`
  prints the decision for every open PR without posting.
- The service holds the policy token only through `LoadCredential`; it runs
  as the unprivileged `merge-policy` user with a strict sandbox.
- If the service is down while `policy/merge` is required, PRs wait rather
  than merge unchecked. Restart it with `systemctl restart merge-policy`.
