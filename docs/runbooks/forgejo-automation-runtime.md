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
