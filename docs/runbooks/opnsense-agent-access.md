# Read-only OPNsense access for agents

## Ownership and scope

The source for the custom privilege is
[`opnsense_reconciler/acl/ACL.xml`](../../opnsense_reconciler/acl/ACL.xml).
Install it at
`/usr/local/opnsense/mvc/app/models/Homelab/AgentDiagnostics/ACL/ACL.xml`
on the OPNsense router. The separate vendor/module path avoids modifying
the FRR plugin's ACL definition. The protected `opnsense-apply` operation
owns routine installation and repair through
[`opnsense_reconciler/agent_acl.py`](../../opnsense_reconciler/agent_acl.py)
and its PHP filesystem worker. Publishing or activating this path requires
explicit shared-infrastructure authorization.

The privilege ID is `page-homelab-agent-bgp-readonly`; the UI name is
**Homelab: Read-only BGP diagnostics**. It grants exactly:

| Endpoint | Purpose |
| --- | --- |
| `/api/quagga/diagnostics/bgpsummary` | Runtime BGP peer status |
| `/api/quagga/diagnostics/search_bgproute4` | IPv4 BGP routing table |

OPNsense ACL patterns authorize paths, not HTTP methods. These two installed
FRR controller actions only read diagnostics; the agent client should use GET.
Do not add wildcards or grant **Routing**: that built-in privilege also
authorizes routing configuration and service control. Keep the separate
**System: Deny config write** privilege as an additional restriction.

This privilege does not grant access to configuration backups, running
configuration, user management, service controls, or other diagnostics.
The BGP table alone does not establish kernel routing or data-plane health;
follow the [BGP proof runbook](opnsense-bgp-proof.md) for complete acceptance.

## Declarative deployment and drift

Edit `opnsense_reconciler/acl/ACL.xml` in source, validate it, and use the
approved current-main `opnsense-apply` operation. Its catalog commands
generate an ACL plan before firewall mutation, then apply it and check
convergence before continuing with the API reconciler. The plan and verification reports are operation
artifacts. The SSH private key is injected only into this deployment
operation, never automatic plans, inventory jobs, PR validation, or local
agent status commands.

The required protected file secret is `opnsense_acl_ssh_key`, exposed as
`OPNSENSE_ACL_SSH_KEY`. Provision a dedicated CI deployment identity for it;
never substitute the `Agent` API key or store this private key in the
shared `homelab-values` Secret. Register its public key through the
router's administrative credential bootstrap. Restrict the authorized key
to the CI host source address and disable forwarding and PTY access.
The current CI host is `homelab-04`, `10.0.30.14`. This identity has root
filesystem authority and belongs exclusively to the protected deployment
environment.

OpenTofu owns its explicit network permission in
`tofu/opnsense/homelab.auto.tfvars`: TCP/22 from `10.0.30.14/32` to
`192.168.1.1/32`, ingress on infrastructure VLAN `opt3`. The existing
reachability was verified during preparation, so the pre-mutation plan can
authenticate before the saved firewall plan reinforces that narrow rule.
A restore that lacks SSH reachability requires administrative bootstrap
recovery under the [OPNsense recovery runbook](opnsense-recovery.md) before
this operation can run. Retain the reviewed firewall rule during recovery;
do not broaden the allowed source subnet to bootstrap SSH.

The reviewed router host key is pinned in
[`opnsense_reconciler/acl/known_hosts`](../../opnsense_reconciler/acl/known_hosts).
The client always verifies this pin and never uses trust-on-first-use or
disabled host verification. Key rotation requires an independently verified
fingerprint and a reviewed source change.

An authorized operator can run a read-only drift check with
`python -m opnsense_reconciler.agent_acl check --output .agent-state/acl-check.json`.
An aligned resource exits zero; drift exits 2; inspection failures exit 1.
Without an explicit key file, inspection may use the operator's existing
SSH identity. Apply requires both an explicit deployment key file and a
saved plan. There is no background drift timer: checks run explicitly and
before and after each approved apply.

The plan records the current file bytes, hash, owner, group, permissions,
parent directory metadata, and the cached privilege. Apply rejects a plan
if either source content or observed router state has changed. It locks
concurrent reconciliations, saves root-only recovery data under
`/conf/backup/homelab-agent-acl/<state-fingerprint>-<source-sha256>.json`, atomically replaces
the file, refreshes the ACL cache, and verifies the result. A verification
or installation failure restores prior file bytes and permissions and
refreshes the cache again. A fully aligned apply performs no writes.

Activation order: publish reviewed source, provision the CI-only SSH
credential and public-key registration, deploy the updated immutable
Woodpecker policy bundle on `homelab-04`, then request the protected
OPNsense deployment against fresh validation. Do not run `opnsense-apply`
between publishing the catalog and provisioning its required secret.
GitLab disaster-recovery operators must provision the corresponding
`OPNSENSE_ACL_SSH_KEY` file variable before using the same catalog there.
The preparation helper requires this secret explicitly and fails if absent.

This does not remove existing operator root SSH or administrator API
access. Those access boundaries require a separate credential and agent
isolation change; agent instructions alone cannot enforce declarative writes.

## Initial setup and emergency recovery

1. Confirm the router's identity against its existing trusted SSH host key.
   Inspect the installed OPNsense and `os-frr` versions, the FRR ACL, and the
   two controller actions before installing. The preparation on 2026-10-15
   observed OPNsense `26.7.3_11` and `os-frr` `1.55` at `192.168.1.1`.
2. Review the XML and existing destination. If a file already exists,
   inspect and retain its exact bytes for rollback before replacing it.
   Otherwise record that this is a new file. Retain a current encrypted
   OPNsense configuration backup before assigning privileges to a user.
3. Create only the missing vendor/module directories, owned by `root:wheel`,
   mode `0755`. Stage and validate the XML with PHP SimpleXML, then install
   the reviewed bytes atomically with ownership `root:wheel`, mode `0644`.
   Compare the installed SHA-256 with the source.
4. Load `/usr/local/opnsense/mvc/script/load_phalcon.php` in PHP and refresh
   the OPNsense ACL cache through `OPNsense\Core\ACL::persist(false)`.
   Check its return value and
   confirm `getPrivList()` contains the new ID and exact two patterns.
   Do not restart routing or the web server.
5. In **System > Access > Users**, edit the dedicated API user. Remove
   administrative group memberships and broad privileges. Assign only
   **System: Deny config write** and
   **Homelab: Read-only BGP diagnostics**, then save.
6. Confirm **System > Access > Privileges** shows exactly the two endpoint
   patterns for the custom privilege. Check all inherited privileges too;
   narrower privileges do not cancel broader grants.

The `read-only` user was provisioned on 2026-10-15 with the custom BGP
privilege and the `read-only` group's `user-config-readonly` restriction.
User enrollment remains an administrative bootstrap step; routine ACL file
changes follow the protected deployment operation above.

## Acceptance

Use the dedicated user's API key and secret with HTTP Basic authentication
and the OPNsense CA. Preserve TLS certificate verification and address the
firewall as `https://OPNsense.internal`, matching its certificate.

Verify both GET requests authenticate and return the expected JSON. Then
check the installed ACL engine denies this user access to at least
`/api/quagga/bgp/set`, `/api/quagga/bgp/add_neighbor`,
`/api/quagga/service/reconfigure`, and `/api/core/backup/providers`.
Check denial through `isPageAccessible()` on the router; do not execute
potentially mutating requests just to test rejection. Retain only sanitized
results, never API secrets, Authorization headers, or raw configuration.

Store API credentials encrypted in
`gitops/secrets/homelab-values.sops.yaml`, under distinct
`stringData.OPNSENSE_READONLY_API_KEY` and
`stringData.OPNSENSE_READONLY_API_SECRET` properties. This follows the
encrypted storage pattern documented for [NetBird](netbird.md).
Use `sops set --value-stdin` when importing: pass the JSON-encoded value
through stdin, never through command arguments or shell history. Preserve
the downloaded credential file with mode `0600`. Limit decrypted access
to trusted operator hosts. Storing the pair locally does not publish it
or update the cluster Secret.

`sops exec-env` does not flatten the Kubernetes Secret's nested `stringData`;
the probe extracts only the two required properties explicitly.
The secret migration helper can prune properties without an ExternalSecret
consumer; retain them through its overrides input on migration.

## Agent status command

Run `just status network` for node pings and BGP diagnostics. Add `--json`
for structured output or `--record` to retain sanitized evidence. Its
overall timeout includes credential decryption and all network requests.

The BGP worker accepts an explicit environment pair named
`OPNSENSE_READONLY_API_KEY` and `OPNSENSE_READONLY_API_SECRET`; setting only
one fails without falling back. Otherwise it extracts just those two
properties from the encrypted source using SOPS. It never falls back to
`OPNSENSE_API_KEY`, CI deploy keys, or the administrator API account.

Set `OPNSENSE_CA_FILE` to override the CA file. By default the probe uses
`secrets/keys/opnsense-ca.pem` and, unless already configured for SOPS,
the local `secrets/keys/flux-age.key` identity. In an isolated Git worktree,
these ignored operator files may come from the primary checkout identified
by Git's common directory. Environment-injected identities and normal SOPS
identity resolution remain supported. No credentials are decrypted during
agent startup, `just doctor`, or offline checks.

The probe makes only the two GET requests in this ACL, checks the desired
peers and ASNs from `opnsense_reconciler/bgp-neighbors.json`, and requires
every route in `opnsense_reconciler/bgp-expected-routes.json`. Missing peers,
wrong ASNs, missing routes, incomplete or malformed responses, API failures,
and exhausted time budgets prevent a successful result. It does not check
FRR service status, the kernel FIB, or the data plane. API response bodies,
decrypted values, and Authorization headers are excluded from output.

## Persistence and recovery

The installed ACL is a managed file outside `config.xml`. It should survive
ordinary reboots, but a configuration export does not contain it, and a
reinstall, restore, or upgrade can require reconciliation. Run the drift
check and protected apply from this repository to restore it. Verify the
file, cached privilege, user assignments, and endpoint behavior after
firmware or FRR upgrades. Until the ACL file is restored, its user privilege
ID grants no matching diagnostic paths.

The root-only recovery JSON retains the exact prior file and directory
metadata even when a failed operation cannot upload CI artifacts. For
ordinary rollback, deploy the previous approved source through a fresh
plan. If interrupted before automatic rollback, an authorized console
operator can restore `before.file.content` (base64) and metadata from the
recovery file, then refresh the ACL cache. If `before.file` is null, remove
only the newly installed file. Inspect recovery files before retention
cleanup; they are not included in a normal `config.xml` export.

For rollback, first remove the custom privilege from the API user. Inspect
the installed file and restore the retained prior file, or remove only the
new ACL file if no prior file existed. Refresh and verify the ACL cache
again. Remove directories only if they were created by this installation
and are empty. Keep administrative console access available throughout.

## References

- [OPNsense ACL extension format](https://docs.opnsense.org/development/components/acl.html)
- [FRR built-in routing privilege](https://github.com/opnsense/plugins/blob/master/net/frr/src/opnsense/mvc/app/models/OPNsense/Quagga/ACL/ACL.xml)
- [OPNsense ACL matcher and cache](https://github.com/opnsense/core/blob/master/src/opnsense/mvc/app/models/OPNsense/Core/ACL.php)
