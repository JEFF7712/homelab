# Switch configuration with OpenTofu

`tofu/switches/inventory.json` owns the existing ports, VLAN membership and
PVIDs on the upstairs TL-SG108E v6 and downstairs TL-SG105E v5. The firmware
and MAC checks bind each stack to the verified device. The port map remains
in [switch-port-map.md](../network/switch-port-map.md).

The pinned `davidlukac/tplinkeasysmart` 0.32.4 provider uses native UDP from
`adguard-netbird-01`; its layer 2 route and reply firewall are declared in
`flake/modules/adguard-netbird-appliance.nix`. Running this provider directly
from a routed agent workspace cannot read the settings reliably.

## Plan and apply

Edit the inventory, validate with `nix develop ./flake -c python -m unittest
tests.test_switches tests.test_ci_offline_inputs -v`, then `just ship`.
Inventory edits select `switches-plan` and `switches-apply`. Worker or blueprint
changes also select fleet deployment first. CI uses its existing SSH deployment
identity and verified host keys; it sends data requests to the installed worker,
never executable source over SSH. The management host refuses a blueprint or
worker digest that differs from CI's source.

Each switch has a separate TLS HTTP state namespace:
`switches-upstairs` and `switches-downstairs`, on the existing NAS state service.
The first plan imports all existing objects and rejects any settings update.
After applying those imports, both stacks must show zero changes. A plan uses
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
review. Changes to the worker's safety controls or CI authority require exact
operator approval under `AGENTS.md`.

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
perform that explicit cosmetic change in a separate source change, checking
management addressing, VLANs, PVIDs and link state before and after. Finish with
zero-change plans on both switches.
