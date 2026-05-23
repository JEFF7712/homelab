# Renovate Dashboard

Small status dashboard for the homelab Renovate automation.

It reads:

- `/config/renovate-agent-repositories.txt`
- `/config/renovate-agent-gitlab-infra-repositories.txt`
- the `renovate-agent-state` ConfigMap
- live GitHub and GitLab PR/MR APIs

It can approve allowlisted GitHub Renovate PRs by adding the same label/comment
used by the Renovate reviewer.

Local checks:

```sh
python -m py_compile app.py
docker build -t jeff7712/homelab-renovate-dashboard:0.0.1 .
```

The Kubernetes deployment lives in `../../renovate-dashboard.yaml`. GitLab CI
publishes pinned `0.0.$CI_PIPELINE_IID` Docker tags, and homelab Renovate should
open updates for that manifest when new dashboard image tags are published.
