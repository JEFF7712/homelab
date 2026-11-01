# Cloudflare Access

The desired applications and policies are declared in `tofu/cloudflare-access/`.
This is a separate OpenTofu stack from the tunnel/WAF stack. DNS and identity
provider configuration remain outside this stack; application IdP references,
OIDC application settings, destinations, policies and session settings are owned
here. The hostname inventory is in [cloudflare-tunnel](cloudflare-tunnel.md#access-policy-organization).

## Source and state

- `applications.tf.json`: the 21 applications, including wildcard owner login,
  Tally's dashboard and two public tracker paths, Grafana/Immich OIDC and WARP
  enrollment. These files are native OpenTofu JSON configuration, edited directly.
- `policies.tf.json`: four reusable policies and the two existing
  application-specific owner policies. Application attachments reference the
  policy resources, so policy definitions have one owner.
- `imports.tf.json`: the existing resource IDs. Keep import blocks for recovery;
  they have no effect once resources are in state. New resources do not need
  import blocks. Do not replace IDs to conceal a destructive plan.
- `.terraform.lock.hcl`: the same pinned Cloudflare 5.28 provider as the tunnel.
- Backend: `https://s3.internal:3902/state/cloudflare-access`, with its own
  locks, lineage and version history in the existing TLS state service on nas-01.
  The original `opnsense` and `cloudflare` states remain required at server startup;
  Access state is initially optional so it can be imported after deployment.

No token values or state files belong in git. Saved plans and state can contain
sensitive attributes and remain in the existing private CI/backend storage.

## Initial activation

The migration must import existing resources without changing their behavior.
The initial declarations were verified using an isolated local backend and a
temporary, account-scoped **Access: Apps and Policies Read** token: 27 imports,
zero additions/updates/deletions, followed by a no-change plan against imported
state. This does not substitute for the production import below.

1. Obtain the operator's exact-change approval for the PR: it adds CI operations
   and changes the CI/state controls named in `AGENTS.md`. Do not bypass
   `policy/merge` or merge the protected controls on the agent's own authority.
2. Provision separate account-scoped tokens in Cloudflare and store them as
   Woodpecker secrets. `cloudflare_access_plan_api_token` has only **Access:
   Apps and Policies Read**, available to `push`, `cron`, and `deployment` events.
   `cloudflare_access_deploy_api_token` has only **Access: Apps and Policies Write**,
   available to `deployment` events. No PR receives either secret. Do not widen
   the tunnel token. Keep recovery copies SOPS-encrypted for the existing operator
   recipients; never print the tokens or include them in logs or PR descriptions.
3. Deliver with `just ship`. Routes deploy the updated CI host and NAS state
   service before the Access plan/apply operations. Preserve the existing backend
   database; deployment adds an accepted state name without replacing its data.
4. The Access plan contains import blocks. `scripts.cloudflare.access` refuses
   any plan mixing imports with resource mutations. A bootstrap apply therefore
   only records existing objects in the new backend. The explicit
   `cloudflare-access-import` operation also provides an import-only retry path
   using the read-only Cloudflare token and the backend deploy identity.
5. Run `cloudflare-access-drift` after the import. It must report no outstanding
   imports and no resource changes. Verify anonymous requests to Tally's dashboard,
   `/t.js`, and `/api/send`, and check the owner-only login hostnames. Complete an
   authenticated browser login when checking the user login flow itself.

If the first plan has changes, stop before applying. Inspect provider defaults,
policy attachments, and SaaS/session settings; fix the declarations and repeat
the no-change proof. Do not use `ignore_changes`, empty policy lists or forced
replacement to make the plan quiet.

## Normal changes and drift

Edit the owning JSON configuration, run `just check-changed` and `just fmt-check`,
then deliver with `just ship`. The Access route runs `cloudflare-access-plan` and
`cloudflare-access-apply`; apply consumes its source-bound saved plan. Source
validation stays offline. Credentialed plans also run on current-main pushes.

The nightly schedule runs `cloudflare-access-drift`. It compares the complete
live application/policy inventory with declared imports and state, so an extra
dashboard-created application or reusable policy is drift even if OpenTofu does
not manage it. It then refreshes and plans all managed resources. Missing objects,
unexpected objects, changed configuration, incomplete reads and outstanding
imports fail the operation. Scheduled drift detection does not auto-apply.

The evaluated-plan gate enforces:

- Every managed object belongs to the expected Cloudflare account.
- Applications have resolved policies and unique destinations.
- The wildcard, approvals, Sovereign and Tally dashboard require owner-only Allow
  rules. Specific paths on those hosts and on `pod.rupan.dev` cannot bypass them.
- Existing OIDC and WARP enrollment applications retain owner-only policies.
- Only Tally's `/t.js` and `/api/send` exceptions bypass its owner login, and both
  remain public to anonymous trackers.
- Wildcard-host bypasses, deletions, replacements and incomplete plans fail.

New policies whose IDs are not yet known must be created in a separate change
before attaching them to an application; unresolved attachments fail closed.
Actual authorization changes continue through the existing operator approval
path in `scripts/ci/deployment_risk.py`. Renaming applications or importing
unchanged resources does not grant new access.

## Recovery

Recover backend state from the existing platform backup of the NAS state SQLite
database, which includes all stacks, locks and version history. Do not overwrite
the original states to restore Access. After restoring, verify authority and
credentials, then run a no-change Access plan before resuming applies.

If only Access state is lost while live Cloudflare resources remain intact,
the retained import blocks can adopt the original resources again. Inspect the
inventory first and require the import-only gate. Newly created resources without
import blocks need their observed IDs added before recovering an empty backend.

For GitLab disaster recovery, provision the protected production variables
`CLOUDFLARE_ACCESS_PLAN_API_TOKEN` and `CLOUDFLARE_ACCESS_DEPLOY_API_TOKEN` with the
same separate scopes; the recovery pipeline includes the Access plan and uses
the same catalog. The NAS backend, TLS verification and exclusive active
production authority remain required.
