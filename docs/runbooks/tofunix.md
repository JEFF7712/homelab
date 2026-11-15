# Tofunix configuration rendering

Nix owns OPNsense, switch and Cloudflare resource declarations. OpenTofu still owns
state, imports, plans and provider execution. Rendering never contacts production
APIs or needs credentials. `just tofu-render` and `just tofu-check` cover all
stacks, shared fleet/DNS outputs, and the generated switch port map. The existing
`just opnsense-render` and `just opnsense-check` names invoke these same unified
commands. See [OPNsense configuration](opnsense-configuration.md) for owning sources.

## Sources and outputs

| Source | Generated output |
| --- | --- |
| Shared inventory and `tofu/opnsense/` | OPNsense JSON, reconciler inputs and shared fleet/DNS/service outputs |
| Switch port roles/endpoints and `tofu/switches/port-map.md.in` | `docs/network/switch-port-map.md` |
| `tofu/switches/inventory.nix`, typed by `default.nix` | `tofu/switches/inventory.json` |
| `tofu/switches/resources.nix`, `imports.nix` | `tofu/switches/main.tf.json` |
| `tofu/cloudflare/resources.nix`, `personal-apps.nix`, `checks.nix` | `tofu/cloudflare/main.tf.json` |
| `tofu/cloudflare-access/inventory.nix`, `resources.nix` | Access `applications.tf.json`, `policies.tf.json`, `imports.tf.json` |

Run `just tofu-render` after changing Nix source, and commit generated outputs
with it. Run `just tofu-check` to reject stale outputs. JSON comparisons ignore
formatting and key order. Rendering validates the complete output set, OPNsense inventory and switch
runtime inventory before writing anything. A validation failure in either stack prevents
writes to both. Port roles and cable endpoints are typed Nix metadata; they are
excluded from `inventory.json`, which remains the worker data contract. CI repeats this check and builds
`checks.tofu-types` and `checks.tofu-config`. Use the pinned `nix develop ./flake`
environment for these commands and the focused tests.

Small backend, provider/version, encryption and variable HCL declarations stay
in place. Provider lock files, resource addresses, instance keys and import IDs
must remain stable across refactors. Runtime credentials remain in protected
environment variables or file secrets. Nix source, generated JSON and evaluated
values enter the public Nix store: never add credentials to them.

The switch worker still uses independent encrypted states and exact blueprint
and saved-plan checks. Changing generated `main.tf.json` or `providers.tf` routes
the management-host runtime deployment before switch operations. Switch inventory
JSON remains the data request format. Shared routed VLAN IDs follow
`inventory/networks.nix`; default VLAN 1 and WAN transit VLAN 99 are switch-only.
The Cloudflare tunnel still decodes `gitops/cloudflare/ingress-config.yaml` at
OpenTofu runtime, preserving its authoritative ordered ingress list.

## Provider updates

`tofu/providers.nix` selects only the resource/data schemas consumed by these
stacks. Its versions must match the provider locks and `nix/ci-providers.json`.
After updating those pins and the provider mirror, run
`just tofu-provider-options`, then render and validate again. The OpenTofu gate
regenerates these checked-in provider options and rejects stale schemas. Normal
configuration evaluation needs no schema downloads or import-from-derivation.
Provider documentation uses a placeholder for the upstream example Access
service-token secret, so generated documentation does not trigger secret scanning.

The pinned Tofunix 3.0.1 generator needs two small adaptations: schema generation
allows deferred expressions for nested provider attributes as it already does
for ordinary attributes, and rendering translates lifecycle `preconditions` /
`postconditions` into OpenTofu's singular keys. Type regression checks verify
nested literal rejection, deferred ingress support and the switch identity
precondition. Provider schemas cannot replace OpenTofu validation or live plans.

## Refactor acceptance and recovery

For a representation-only migration, compare generated switch inventory and
Access declarations semantically with the previous commit. Compare evaluated
Cloudflare ingress and WAF policy values, then run focused tests, OpenTofu
validation and protected no-change plans for both switches and Cloudflare
stacks. A static check alone does not establish parity with live state.

Deliver with `just ship`. Inspect plans before applying any unexpected setting
change. Representation-only changes require no resource imports or state moves.
For recovery, revert the source/rendering change through the same delivery path;
preserve backend lineage, encryption keys and provider locks. Never delete state
to recover from a rendering or blueprint mismatch.
