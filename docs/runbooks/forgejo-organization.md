# Forgejo organization teams

`JEFF7712` is a Forgejo organization (owned by the `rupan` site administrator)
that holds every repository, `homelab` included. Its teams have access to all
of its repositories, including ones created later, so a new project needs no
collaborator grant and no token change.

Desired state: `config/forgejo/organization.json`. Reconciler:
`scripts/forgejo_org.py` (`plan` reads only; `apply` creates or corrects the
declared teams, sets their members exactly, then re-plans and fails on any
remaining drift). Teams absent from the config, including Owners, are never
touched.

| Team | Members | Access |
| --- | --- | --- |
| `agents` | `homelab-agent` | write, may create repositories |
| `publishers` | `homelab-publisher` | write (`forgejo_publish_token`) |
| `ci-readers` | `homelab-ci-source` | read (Woodpecker clone identity) |
| `monitors` | `repo-monitor` | read |

Units are code, issues, pull requests and releases. Tokens of these accounts
use repository access "All": team membership, not the token, is the
boundary. Every repository in `JEFF7712` is writable by agents and CI;
branch protection still decides who merges to protected branches. Keep a
repository out of agents' reach by putting it under a different owner.

Per-repository collaborator grants made before the teams (for example on
`homelab` and `nixos-config`) remain and are now redundant for these four
accounts. Other accounts (`homelab-renovate`, `homelab-policy`,
`projectctl-observer`, `nixos-agent`) keep their explicit grants.

## New projects

Agents create project repositories in the organization as `homelab-agent`
(`POST /api/v1/orgs/JEFF7712/repos`), then add the project to
`config/ci/projects.json` and, for a deployed application,
`config/ci/applications.json`.

## Running the reconciler

It needs an organization-owner or site-administrator token with
`write:organization` and `read:user`, expiring within a day. Save it at
`~/.config/forgejo/org-bootstrap-token` (mode 0600), then:

```sh
python -m scripts.forgejo_org plan --token-file ~/.config/forgejo/org-bootstrap-token
python -m scripts.forgejo_org apply --token-file ~/.config/forgejo/org-bootstrap-token
```

Revoke the token and delete the file afterwards. No standing credential can
change the teams; drift is corrected only by such a run.
