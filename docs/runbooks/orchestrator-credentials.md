# Orchestrator credentials (gate 5)

The persistent-agent controller and its workers need the credentials in the
plan of [persistent-agent.md](../architecture/persistent-agent.md#credentials).
This is the order to create them for the first live run. Decisions
(2026-11-09): the first worker runtime is **Claude** (one token file), and the
first live tasks target the disposable **`JEFF7712/agent-sandbox`**
repository; the homelab repository follows once those work.

No credential value is pasted into chat, a task export or a log. Every value
lands in a SOPS file under `gitops/secrets/`, encrypted to the Flux age
recipient. None of the orchestrator files is in
`gitops/secrets/kustomization.yaml` until the deployment request adds them.

## 1. Machine credentials (done by an agent)

```sh
python -m scripts.orchestrator.credentials generate
```

writes, with values nobody types:

| File | Namespace / Secret | Keys |
| --- | --- | --- |
| `orchestrator.sops.yaml` | `persistent-agent/orchestrator-secrets` | `client-token`, `alertmanager-intake-token`, `sentinel-intake-token`, `ntfy-token` |
| `orchestrator-intake-alertmanager.sops.yaml` | `observability/orchestrator-intake-alertmanager` | `token` (same value as the controller's) |
| `orchestrator-intake-sentinel.sops.yaml` | `automation/orchestrator-intake-sentinel` | `token` (same value as the controller's) |

Plaintext goes to `sops --encrypt` on stdin only. It refuses to replace
existing files; `--force` rotates all of them, which also discards keys the
operator added to `orchestrator.sops.yaml`, so re-add those after a rotation.

## 2. Forgejo (operator, admin UI)

1. Create the private repository `JEFF7712/agent-sandbox` with a README, and
   protect `main` (no direct pushes).
2. Site Administration → User accounts → create `agent-publisher` and
   `agent-controller`: not admins, "may create organizations" off, repository
   limit 0.
3. agent-sandbox → Settings → Collaborators: `agent-publisher` **Write**,
   `agent-controller` **Read**.
4. As each user, Settings → Applications → Generate new token:
   - `agent-publisher`: `read:repository`, `write:repository`
   - `agent-controller`: `read:user`, `read:repository`, `read:issue`,
     `write:issue`
5. Activate agent-sandbox in Woodpecker. An agent then adds the pipeline that
   reports the `ci/woodpecker/pr/validation` status the verifier requires.

## 3. Claude worker token (operator, laptop)

```sh
claude setup-token
```

It prints a one-year OAuth token for the subscription. Put a calendar
reminder a month before it expires.

## 4. Add the human-issued values (operator, laptop)

```sh
export SOPS_AGE_KEY_FILE=/home/rupan/homelab/secrets/keys/flux-age.key
sops gitops/secrets/orchestrator.sops.yaml
```

Under `stringData`, add `forge-publisher-token` and `forge-controller-token`.
Then create the worker Secret:

```sh
sops gitops/secrets/orchestrator-worker.sops.yaml
```

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: orchestrator-worker-secrets
  namespace: persistent-agent
type: Opaque
stringData:
  claude-oauth-token: <the setup-token value>
```

Check both decrypt without printing them:
`sops -d gitops/secrets/orchestrator.sops.yaml >/dev/null`.

## 5. ntfy publish user (operator, laptop)

ntfy users are declarative: `NTFY_AUTH_USERS`, `NTFY_AUTH_TOKENS` and
`NTFY_AUTH_ACCESS` come from `homelab-values.sops.yaml` through the
`ntfy-auth` ExternalSecret. Add a publish-only user whose token is the one
step 1 generated:

```sh
token=$(sops -d --extract '["stringData"]["ntfy-token"]' gitops/secrets/orchestrator.sops.yaml)
hash=$(htpasswd -nbB x "$(openssl rand -hex 24)" | cut -d: -f2)   # password never used
sops gitops/secrets/homelab-values.sops.yaml
```

Append, comma-separated, to the existing values:

- `NTFY_AUTH_USERS`: `agent-orchestrator:<hash>:user`
- `NTFY_AUTH_TOKENS`: `agent-orchestrator:<token>:orchestrator`
- `NTFY_AUTH_ACCESS`: `agent-orchestrator:orchestrator:wo`

`unset token hash` afterwards. ntfy picks the change up within the
ExternalSecret's one-hour refresh, or after a pod restart.

## 6. Ship

Commit the two SOPS files you changed or created (never plaintext) and ship
them with `just ship`. Tell the agent: it then renders the deployment with
the Claude backend and agent-sandbox, binds it into Flux and runs gate 6.

## Not yet

- **Woodpecker pipeline polling** stays off: Woodpecker has no read-only
  tokens. Enabling it later needs a dedicated non-admin Woodpecker user.
- **Codex** logs in inside the cluster against its own volume once a Codex
  deployment exists (`codex login --device-auth` in a one-off pod).
- **Pi** has no login in the credential plan yet.
- **Supervisor (phase 2):** the budget-capped API key ($10 a month, in
  `config/spend.json`) and the Telegram bot.
