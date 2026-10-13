# NetBird management API for agents

Peer client config and dashboard control plane are separate. `flake/modules/adguard-netbird/netbird.nix` owns the `adguard-netbird-01` peer (interface `wt0`, port 51820, routing features `server`). Everything in the NetBird dashboard (peers, routes, policies, groups, users, setup keys, networks) is managed through the NetBird Management public API, not through NixOS options. Read this runbook before calling that API or proposing route, policy, or peer changes.

## When agents should use it

- Peer approval, rename, or group assignment from the dashboard side.
- Route, policy, group, or setup-key audits and changes.
- Debugging remote LAN access where the on-host client looks healthy but dashboard state (approval, routing peer, policy) is suspect.
- Any task that would otherwise mean clicking around in `https://app.netbird.io`.

Start read-only (`GET`), show the plan, then ask for authorization before any `POST`/`PUT`/`DELETE`, per the infrastructure rules in `AGENTS.md`.

## Credential

- SOPS property `NETBIRD_API_KEY` in `gitops/secrets/homelab-values.sops.yaml` (decrypted by Flux into Secret `homelab-values` in namespace `secret-source`; see `gitops/clusters/homelab-01/secrets.yaml` and `gitops/eso/secretstore.yaml`).
- Runtime env var name is also `NETBIRD_API_KEY`.
- Prefer a service-user personal access token (dashboard: Team -> Service Users) over a personal user token, so automation survives personnel changes. API reference: `https://docs.netbird.io/api`.
- Never commit the plaintext value. `gitleaks` gates plaintext secrets, and `.sops.yaml` only allows `gitops/secrets/*.sops.yaml`.

## Store the key (owner, privileged host only)

The key itself never goes through chat, issues, or logs. From the repo root with the age identity present:

```sh
export SOPS_AGE_KEY_FILE=$PWD/secrets/keys/flux-age.key
sops --set '["stringData"]["NETBIRD_API_KEY"] "paste-key-here-keeping-the-double-quotes"' gitops/secrets/homelab-values.sops.yaml
```

The value after the path must be JSON, so a string value keeps its surrounding double quotes. A bare token without them fails with `Value for --set is not valid JSON`. If the key itself contains `"` or `\`, skip hand-quoting and let Python escape it (also keeps the key out of shell history):

```sh
export SOPS_AGE_KEY_FILE=$PWD/secrets/keys/flux-age.key
python3 -c "import getpass, json, subprocess; v = getpass.getpass('Paste NetBird API key: '); subprocess.run(['sops', '--set', '[\"stringData\"][\"NETBIRD_API_KEY\"] ' + json.dumps(v), 'gitops/secrets/homelab-values.sops.yaml'], check=True)"
```

Prefer interactive edit (`sops gitops/secrets/homelab-values.sops.yaml`) if `--set` is unavailable in the installed SOPS version. Verify presence without printing the value:

```sh
SOPS_AGE_KEY_FILE=$PWD/secrets/keys/flux-age.key sops -d gitops/secrets/homelab-values.sops.yaml | grep -c NETBIRD_API_KEY
```

Expect `1`. If the output is `0`, the edit did not land.

Retention caveat: `scripts/ci/migrate_secrets.py` rebuilds `homelab-values.sops.yaml` from properties referenced by `ExternalSecret` manifests. No cluster workload consumes `NETBIRD_API_KEY` yet, so a future `--overrides`-less migrate would prune it. On the next migrate, re-supply it via the overrides file (see `docs/runbooks/forgejo-disaster-recovery.md`), or add an `ExternalSecret` reference once a real in-cluster consumer exists. Normal rotation does not run migrate.

## Use the key (agents)

Load the key from SOPS into the environment; do not paste it into commands or files:

```sh
export SOPS_AGE_KEY_FILE=$PWD/secrets/keys/flux-age.key
sops exec-env gitops/secrets/homelab-values.sops.yaml 'curl -s https://api.netbird.io/api/peers -H "Authorization: Token $NETBIRD_API_KEY" | head -c 1000'
```

Auth scheme is `Authorization: Token <key>` (PAT), not `Bearer`. Base URL is `https://api.netbird.io`. Cloud rate limit is 120 requests per minute with a 1200-request burst.

Useful read-only endpoints:

| Task | Request |
| --- | --- |
| List peers | `GET /api/peers` |
| List routes | `GET /api/routes` |
| List policies | `GET /api/policies` |
| List groups | `GET /api/groups` |
| List users | `GET /api/users` |
| List setup keys | `GET /api/setup-keys` |
| List networks | `GET /api/networks` |

Full schema is at `https://docs.netbird.io/api`; treat that as authoritative for request bodies on writes.

## Rotate

1. Create the replacement token in the dashboard (Team -> Service Users or Users -> Access Tokens). Copy it once.
2. Update the SOPS property with the store command above.
3. Confirm the new value authenticates with one `GET /api/peers` call via `sops exec-env`.
4. Revoke the old token in the dashboard.

No cluster restart is needed: nothing in `gitops/` consumes this property yet. If a consumer is added later, it follows the standard `homelab-values` -> `ClusterSecretStore homelab-secrets` -> `ExternalSecret` path (example: `gitops/media/secrets.yaml`) and gets the new value within the `1h` refresh interval.
