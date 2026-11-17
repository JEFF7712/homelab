# Agent environment and access

`just agent-context` is injected by the repository client adapters at session
startup. It reports the current hostname, tools on PATH and local credential
hints. These are observations, not proof of authentication or authorization.
Absent hints do not prove access is unavailable (for example, kubectl can use an
exec credential plugin and SSH can use an identity file without an agent).
Startup never reads secret contents or contacts a service. Run `just doctor`
for local readiness and `just doctor --live` for read-only authentication checks
when the task needs infrastructure access. Use `nix develop ./flake` for missing
repository tools. MCP tools are client-specific; discover them through the
client's tool inventory rather than assuming another client has the same tools.

The personal development VM has its own credentials and network namespace.
Laptop sign-ins and SSH agents are not forwarded. Private-network reachability
is selective: a blocked application IP does not imply the application is down
or that Kubernetes access is unavailable. The authoritative grants are in
`flake/modules/agent-workspace-network.nix` and the owning host configuration;
see [agent workspaces](agent-workspaces.md). Do not expand firewall access to
work around an application diagnostic.

| Capability | Existing route and discovery |
| --- | --- |
| Kubernetes / Flux | Current kubectl context; `KUBECONFIG` or `~/.kube/config`. Run `kubectl config current-context`, then `just status cluster`. Confirm target identity before mutations. |
| Fleet SSH | Local SSH configuration and identities; inspect `~/.ssh/config` without printing private keys. `just doctor --live` checks fleet logins. |
| Forgejo | `git-agent` SSH alias for delivery; `tea` login in `~/.config/tea/config.yml` for API authentication. Never print the token. |
| CI / deployment | `WOODPECKER_TOKEN` or `~/.config/woodpecker/token`; use `just ship`, which routes deployment through CI. |
| SOPS secrets | `SOPS_AGE_KEY`, `SOPS_AGE_KEY_FILE`, or `~/.config/sops/age/keys.txt`; recipient membership still determines decryption access. Read only the fields needed into the consuming process. |
| OPNsense | [Read-only agent access](opnsense-agent-access.md), `just status network`; use the declared HTTPS endpoint with CA verification. |
| Home Assistant | Local token discovery and Kubernetes route below; desired state is `home-assistant/`. |

## Home Assistant from a VM

The CLI defaults to `http://10.0.40.13:8123`. That private address may be blocked
from a workspace even while the Kubernetes API is reachable. First inspect the
current cluster context and the workload:

```sh
kubectl config current-context
kubectl -n home-assistant get pods
kubectl -n home-assistant port-forward --address 127.0.0.1 deployment/home-assistant 18123:8123
```

Keep the port-forward running in a separate process while making requests. Set
`HASS_URL=http://127.0.0.1:18123` for the HA CLI, for example:

```sh
HASS_URL=http://127.0.0.1:18123 just ha-inventory
```

The CLI resolves authentication from `HASS_TOKEN`, `HASS_TOKEN_FILE`, then the
checkout-local `.agent-state/home-assistant/token`. A cluster credential source
also exists at Secret `home-assistant/home-assistant-token`, key `token`, if the
current identity has permission to read it. Feed it directly into the consuming
process or a mode-0600 temporary file; never print it or include it in a command
argument. A tunnel provides transport, not authentication. Stop your own
port-forward after use. If Kubernetes access or RBAC denies this route, report
the specific failure; do not infer service health from that denial.

Use [HA configuration](home-assistant-configuration.md) for source reconciliation
and deployment. Use the authenticated WebSocket API through the same tunnel for
runtime traces. `just ha-inventory` verifies API access and inventory, but does
not itself retrieve automation traces.
