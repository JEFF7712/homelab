# Forgejo automation runtime

Application code is authoritative in homelab ConfigMaps:
`gitops/automation/forgejo-dashboard-script.yaml` and
`gitops/automation/renovate-reviewer-script.yaml`. Flux deploys this source.

The dashboard uses the retained upstream Python runtime. It imports only
standard-library modules, including the bundled IANA timezone data. The
reviewer uses retained Renovate 42.70.2, which supplies Python 3.12.3,
Git 2.52.0 and the Renovate CLI. Its mounted ConfigMap remains the source of
application behavior. Neither workload requires its old GitLab-produced
custom image. Those image digests remain available for rollback.

Before rollout, verify the pinned images with networking disabled, compile
and import the real scripts, and confirm Python timezone handling and CLI
versions. After rollout, probe the dashboard, verify its Forgejo backend,
and observe a reviewer execution without granting the bot authority to
approve its own changes. Preserve its existing human-approval boundary.
Runtime changes do not alter the application's secrets or state PVC.

These replacements use existing mirrored artifacts and homelab CI source
validation. They do not add a new image producer or platform service.

## Dependency automerge

Renovate owns automatic merges through Forgejo native PR automerge, using
merge commits. `renovate.json` enables it for minor, patch, pin and digest
updates. Later package rules retain the exclusions for majors, container
images, Helm charts and Nix inputs. Both `ci/woodpecker/validation-v2` and
`policy/merge` remain required by branch protection. Failed validation and
an overlapping change behind main must be repaired or rebased before merging;
a successful `ci/woodpecker/pr-validation-reused` is not a substitute for the
required validation context.

The reviewer reports eligible bot PRs as native automerge candidates without
requesting human approval. This classification changes notifications only;
it never authorizes a reviewer merge. Other PRs retain exact-head operator
approval, and reviewer merges also wait for both required checks. Missing,
pending or failed checks keep the PR waiting. Rebase invalidates approval
because the approval command includes the full source commit SHA.

For acceptance, run `python -m unittest tests.test_forgejo_automation -v` in
the pinned shell, then `just check-changed` and `just fmt-check`. After shipping,
compare the live `renovate-reviewer-script` ConfigMap with the committed source
and inspect the next `renovate-major-reviewer` execution. Roll back a regression
by reverting the source change and shipping it through Flux; do not patch the
live ConfigMap. Container promotion and Nix fleet deployment are separate
follow-up workflows and remain excluded from automatic merges.
