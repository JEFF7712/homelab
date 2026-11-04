# Switch configuration with OpenTofu

`tofu/switches/inventory.nix` owns the existing ports, VLAN membership and
PVIDs and descriptions on the upstairs TL-SG108E v6 and downstairs TL-SG105E v5. The firmware
and MAC checks bind each stack to the verified device. The port map remains
in [switch-port-map.md](../network/switch-port-map.md).

The `davidlukac/tplinkeasysmart` provider version is pinned in
`tofu/switches/.terraform.lock.hcl`. It uses native UDP from
`adguard-netbird-01`; its layer 2 route and reply firewall are declared in
`flake/modules/adguard-netbird-appliance.nix`. Running this provider directly
from a routed agent workspace cannot read the settings reliably.

The typed inventory shares routed VLAN IDs with `inventory/networks.nix`.
VLAN 1 and WAN transit VLAN 99 remain switch-only declarations. Membership
and PVID assertions run during Nix evaluation; the independent Python runtime
validator still checks adopted identities and firmware before any operation.
`resources.nix` uses Tofunix to generate `main.tf.json`; `inventory.json` is also
generated. Never edit either JSON output directly. See
[Tofunix rendering](tofunix.md) for rendering, provider schemas and migration checks.
The worker consumes generated JSON with the existing provider settings,
backend and lock file. Its source/blueprint digest checks are unchanged.

## Read-only drift checks

`switches-drift` runs the installed worker's plan operation on both switches,
using the existing read-only backend identity and placeholder switch credentials.
It never calls apply. It records `artifacts/switches/drift.json`, fails when either
switch has changes, outstanding imports, an incomplete review or a check error,
and continues to inspect the other switch after a failure.

A failing run sends a bounded summary to the existing `homelab-alerts` ntfy topic.
The notification distinguishes drift from inability to inspect a switch. A failed
notification cannot make the drift operation pass. Do not automatically apply a
drift report: inspect the plan and owning source through the normal workflow.
The `nightly` CI schedule runs this check alongside the existing Access and
OPNsense checks. Enrollment is an exact-approval change to `SCHEDULES` in the
CI policy; changing the schedule never grants apply permission.

Port roles and cable endpoints live beside port settings in `inventory.nix`.
They generate the [port map](../network/switch-port-map.md) and topology through
`just tofu-render` and `just topology`. Runtime JSON excludes these metadata
fields. Switch-to-switch cable references must be reciprocal, and no two ports
may claim the same remote endpoint. Unverified OPNsense WAN/LAN assignments remain
visible in the port map but are omitted as physical diagram edges.

## Plan and apply

Initial activation changes CI authority and the state backend. Use
`just ship --deploy deploy-ci-host,deploy-switch-runtime,switches-apply` so only
the CI host, management host and NAS are activated, after exact operator
approval. Keep their normal preflight, health and rollback checks enabled.

Edit the Nix inventory, run `just tofu-render`, then validate with `nix develop ./flake -c python -m unittest
tests.test_switches tests.test_ci_offline_inputs -v`, then `just ship`.
Inventory edits select `switches-plan` and `switches-apply`. Worker or blueprint
changes also select the scoped management-host and NAS deployment first. CI uses
its existing SSH deployment identity and verified host keys; it sends data requests to the installed worker,
never executable source over SSH. The management host refuses a blueprint or
worker digest that differs from CI's source. If dependency automation merges a
provider update without a runtime rollout, resume that merge with
`just ship --resume <merge-sha> --deploy deploy-switch-runtime,switches-plan`
before applying settings. Review the new saved plan, then resume with
`--deploy switches-apply`.

Each switch has a separate TLS HTTP state namespace:
`switches-upstairs` and `switches-downstairs`, on the existing NAS state service.
The first plan imports all existing objects and rejects any settings update.
Apply finishes with a fresh plan and fails unless each switch shows zero
changes. A plan uses
placeholder switch credentials; actual admin credentials are loaded only when
applying an exact saved plan that contains settings changes.

CI stores only receipts and review summaries. Encrypted plans stay in private
management-host workspaces for 24 hours of validity. Apply checks the source
revision, inventory, installed worker and blueprint, plan bytes and current state
against the receipt, then rechecks the plan before applying. Changed state or an
expired receipt requires a new plan. Operations are serialized locally and in CI;
the backend also locks each state. Failed or partial imports require inspection
before retrying; never delete production state to force adoption.

The workflow prevents resource destruction, VLAN mode changes, disruptive
provider overrides, hardware or firmware substitutions, and creation of physical
ports or PVIDs. A rejected maintenance change needs a separate procedure and
review. Changes to CI enforcement, including the state service, require exact operator
approval under `AGENTS.md`.

## Encryption and recovery

Native OpenTofu AES-GCM encryption is enforced for both state and saved plans.
Separate PBKDF2 passphrases are delivered through `TF_ENCRYPTION`; they are not
configuration variables. The state service rejects plaintext switch state.
`switch-tofu-state-keys.json` and `switch-tofu-credentials.json` live in the
existing `secrets/hosts/adguard-netbird-01.sops.yaml`, with its existing operator
and host recipients, and are decrypted by sops-nix into root-only runtime files.
No operator private key is installed on the management host.

Restore the NAS state database and the corresponding SOPS key material together.
The existing platform backup covers the database and its historical versions.
Losing an encryption key makes its state and plans unreadable. Keep key names
and encryption method identifiers stable; key rotation needs an explicit state
migration. If a saved plan workspace is lost or expired, create a fresh plan.
Never recover by disabling encryption or replacing production state with an
empty file. Preserve backend lineage and serial checks.

Original encrypted vendor configuration backups and the firmware recovery bundle
are retained in the operator's private switch migration directory. They precede
the firmware upgrade; a configuration restore is a separate disruptive operation.
The provider's config-backup resource is not used: its format handling is not
valid for both switch models.

## Reversible write proof

All 13 ports are in use. Do not change port settings for a smoke test. The
operator authorized normalizing the existing underscore descriptions to hyphens,
then temporarily changing and restoring the normalized description. The current
provider rejects underscores on writes, so the original descriptions cannot be
restored through its system resource. Import the network settings first, then
set `description` in the inventory to `Upstairs-TL-SG108E` and
`Downstairs-TL-SG105E` in a separate source change. The optional system resource
imports the existing description and updates only that field. After adoption,
keep it managed; removing it is blocked by `prevent_destroy`. Perform the
temporary name change and restoration through subsequent inventory changes,
checking management addressing, VLANs, PVIDs and link state before and after. Finish with
zero-change plans on both switches.
