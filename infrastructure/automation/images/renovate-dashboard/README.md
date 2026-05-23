# Renovate Dashboard

Small read-only status dashboard for the homelab Renovate automation.

It reads:

- `/config/renovate-agent-repositories.txt`
- `/config/renovate-agent-gitlab-infra-repositories.txt`
- the `renovate-agent-state` ConfigMap
- live GitHub and GitLab PR/MR APIs

Local checks:

```sh
python -m py_compile app.py
docker build -t jeff7712/homelab-renovate-dashboard:0.0.1 .
```

The Kubernetes deployment lives in `../../renovate-dashboard.yaml`.
