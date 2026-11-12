# projectctl

The CLI onboards an existing Python repository through a restricted SSH gateway
and a trusted worker. It provides adopt, plan, request, status,
approval-context, doctor, and cancel. Every command returns a versioned JSON
envelope. Request journals intent without executing infrastructure writes.
Status and doctor are read-only. A request in a preapproved routine scope is
queued for automatic execution. Other scopes require signed operator approval.
See the [original protocol design](../superpowers/specs/2026-10-15-projectctl-protocol-design.md).

Implementation lives in scripts/projectctl/. The worker uses supported Forgejo
and Woodpecker APIs, rechecks authorization on protected main before every write,
and emits an enrollment artifact for protected review. It cannot merge that
artifact, deploy policy, or execute repository code. GitHub mirrors, templates,
application deployment, secret creation, and automatic deletion are deferred.

## Routine onboarding

The operator preapproves a caller's exact project in config/projectctl/callers.json
by setting routine to the existing Forgejo repository ID, default branch and
CI contract. The deployed worker and protected-main entry must agree. The pilot
scope is private JEFF7712/autocompress, ID 24, main, python-uv-v1. The account
and token grants remain confined to that project. This authorization permits
only fixed clone-reader, CI enrollment/settings, registry-reader, branch-protection
and CI-trigger
operations. It cannot create repositories, change visibility or branch, publish,
delete, deploy, select arbitrary commands, or expand permissions. Changes to
the scope itself require protected platform review and deployment.

Submit an adopt plan and request normally. The systemd reconciler runs every
30 seconds with the same isolated identity and LoadCredential credentials as
manual dispatch. Routine requests need no signing packet, approval PR or manual
dispatch. The original catalog revision and entry digest are saved once in the
journal. Retries cannot silently replace that baseline. Removed or changed
protected scopes stop subsequent writes, and policy changes invalidate old plans.
The journal upgrades existing version-2 databases without dropping requests.

A plan's policy digest binds the projectctl modules, the deployed bindings, the
routine scope and the CI contract digest. The contract digest is the SHA-256 of
config/ci/projectctl-contracts.json, which declares the step, image and commands
of each contract the worker can enroll. The policy service renders and stamps
those workflows from that file, so unrelated CI policy deployments no longer
invalidate waiting requests. Editing the contract file, the projectctl modules
or the scopes still does, and those requests must be cancelled and re-planned.

Supported contracts are the sandbox lanes declared in that file: python-uv-v1,
node-build-v1, node-lint-build-v1 and node-vitest-v1. adopt defaults to
python-uv-v1 only when the checkout has a pyproject.toml; pass --ci otherwise.
Repositories already in the application lane (a catalog tier other than
sandbox or none, such as static-site-v1 or node-service-v1) are refused with
RESOURCE_CONFLICT rather than demoted. Those ship through the application
release workflow, not projectctl.

The worker still emits catalog artifacts for the normal protected review and
deployment workflow. The agent handles that promotion, then the worker resumes
automatically. Worker credentials cannot merge platform PRs or deploy hosts.
Status distinguishes waiting for policy or CI from success. An uncertain write
outcome returns the request to waiting with backoff, and the reconciler retries
it up to five attempts; each retry inspects actual state first and creation is
bound to an allocation receipt, so a lost response is reconciled rather than
replayed. After the last attempt the request blocks for inspection. Resource
conflicts, revoked authority and changed policy block immediately. Cancellation
retains resources. Blocked requests require diagnosis and explicit resubmission
or dispatch.

Routine readiness checks exact-SHA stamped CI, matching commit status, a real
push webhook, and configured status checks applying to administrators with direct
push disabled. When CI and protection pass but no push pipeline exists for the
exact SHA, readiness replays the repository's Woodpecker push hook through
Forgejo's hook test delivery, using the provisioner. It selects the hook whose
/api/hook token names the repository's Forgejo id and refuses any other, so an
agent never needs to push or replay webhooks by hand. The worker creates or strengthens the standard branch rule only
after CI admission is deployed. It preserves additional status contexts, approval
requirements and signed-commit restrictions. It does not claim independent proof
of merge rejection. The signed
failing-PR probe is a separate platform qualification audit, not a per-project
onboarding gate. Signed scopes retain their stronger request-bound proof path.

## Deployment and credential ownership

`flake/hosts/homelab-04/default.nix` imports
`flake/profiles/projectctl-pilot.nix`, enabling the worker and mapping the five
properties in `secrets/projectctl.sops.json` through sops-nix. The observer files
are owned by `projectctl-worker`; provisioner and registry credentials remain
root-owned and are delivered to trusted services through `LoadCredential`.
`flake/modules/projectctl.nix` owns the restricted SSH gateway, verifier,
dispatch template and recurring reconciler, with state in `/persist/projectctl`.

Confirm the active generation, `projectctl-verify-credentials.service`,
`projectctl-reconcile.timer`, caller scope and current request status before
claiming readiness. A declared profile does not prove that a request enrolled
successfully. Use the current source and [host deployment workflow](nixos-host-deployment.md)
for changes; do not reactivate a temporary trial generation.

The October 17–19 trial logs, generation paths and inactive credential directories
are historical evidence available in Git history. They do not describe current
installation paths or extend the task's authorization. Keep recovery credentials
and operator keys independent of the worker host.

The encrypted bundle contains these five dedicated credentials:

| File | Required authority |
| --- | --- |
| forgejo-read | Nonadministrator observer; homelab and scoped project read access; read:repository, read:user, read:organization where required |
| forgejo-write | Nonadministrator provisioner; repository admin on scoped projects only; write:repository and read:user API scopes |
| woodpecker-read | Dedicated nonadministrator observer with pilot read access, using the supported profile PAT |
| woodpecker-write | Dedicated nonadministrator provisioner with pilot repository administration, using its profile PAT |
| registry-password | New Zot identity projectctl-pull, read only on required validation and clone image repositories |

Woodpecker uses user PATs and forge-derived permissions. Separate observer and
provisioner accounts are required; do not claim its PATs are endpoint-scoped.
Its [3.18 user model](https://raw.githubusercontent.com/woodpecker-ci/woodpecker/v3.18.0/server/model/user.go)
omits false admin and admin_env fields. The verifier accepts those omitted false
values, rejects administrator or environment-administrator identities and invalid
flag types, and still requires an explicit false Forgejo is_admin field.
Woodpecker 3.18 requires repository write permission even for registry metadata
GET requests. The trusted worker uses its provisioner credential for those
fixed lookups. Gateway diagnosis continues using the observer credential;
provisioner credentials require the worker's systemd credential directory.
Forgejo also requires repository administration to read branch-protection rules.
It requires the same authority to inspect clone collaborator permissions. The
trusted worker uses its pilot provisioner for those fixed lookups. Observer
diagnostics report protection as unknown when access is denied and preserve
the other observed capabilities. They never gain provisioner credentials.
Forgejo's Specific repositories tokens permit only repository/issue scopes and
cannot add collaborators or perform administrative repository changes. They
also cannot use read:user, which the worker's identity check requires. For this
implementation, select All (public, private, and limited) as the repository
access category, then select only the API scopes listed above. That category
does not mean selecting every API scope. The account's own permissions form
the repository boundary: observer read access to homelab and each project
scoped in config/projectctl/callers.json; provisioner administration on those
projects only. Give neither account organization ownership or other repository
grants. Adding a scope requires granting both collaborators on that repository. Set each account's repository creation
limit to zero and disable organization creation. A future project-creation
identity requires separate review. See [Forgejo token scopes](https://forgejo.org/docs/v15.0/user/authentication/token-scope/)
and [Woodpecker's API](https://woodpecker-ci.org/api).

The existing homelab-ci-source remains the CI clone identity, subject to
verifying its read-only pilot access. The worker receives no clone password.
Inspect the projectctl-pull identity and effective Zot ACL using the
[registry rotation runbook](projectctl-registry-rotation.md). Woodpecker's registry readonly field does not establish remote
registry authorization. Configuring actions=["read"] is an operator assertion,
not a substitute for examining the effective Zot policy.

For an existing pilot registry entry using the legacy node identity, onboarding
updates only that repository's entry at the configured registry address to
projectctl-pull. An entry using another identity remains a resource conflict.
The service identity and registry password never enter the operation journal.

## Trusted setup

These commands are for the operator after review and deployment authorization.
They use fish syntax and run from the reviewed checkout in nix develop ./flake.
Nothing in the test suite installs credentials or activates the pilot.

Homelab-04 uses protected /persist files and encrypted recovery copies under
secrets/. Its root filesystem is tmpfs. Inspection found no existing projectctl
credential directory or host-side SOPS key convention to reuse. Follow the
existing Woodpecker convention: decrypt independently, transfer directly through
SSH, and load write credentials through systemd. No age identity is installed
on homelab-04.

1. Create dedicated observer/provisioner Forgejo accounts using the trusted UI.
   Use projectctl-observer and projectctl-provisioner, unique passwords, and
   email aliases you control. Neither is a site administrator. Set maximum
   repository creation to zero and disable organization creation for both.
   Grant exactly the permissions above. With Woodpecker registration closed,
   use the existing human administrator's Settings > Users > Add User page to
   admit one exact Forgejo login at a time. Leave Administrator unchecked.
   Sign in to Woodpecker as that account through Forgejo OAuth in a separate
   browser profile and save its PAT before admitting the next account.
   Woodpecker 3.18 assigns manually admitted users the temporary remote ID 0;
   its unique forge/remote-ID constraint prevents adding a second account
   until the first login replaces that placeholder with the real Forgejo ID.
   If adding the second account reports a duplicate constraint, complete the
   first account's login, then retry adding the second. Verify both entries
   use the same Forgejo source as the existing administrator. Keep registration
   closed and both accounts nonadministrators. Verify that the provisioner can
   activate and repair this repository without a platform-admin override.
   Do not mint all-scopes tokens or access service databases.
2. Create the dedicated Zot reader through the existing registry procedure.
   The owning ACL generator now grants projectctl-pull read access to exactly
   upstream/ghcr.io/astral-sh/uv and upstream/docker.io/woodpeckerci/plugin-git
   when present in the lock. Generate and review its policy before deployment:

   ~~~fish
   just registry-access-control /tmp/projectctl-zot-access-control.json
   jq '.repositories | with_entries(select(.value.policies[]?.users | index("projectctl-pull")))' /tmp/projectctl-zot-access-control.json
   ssh nas-01 'sudo cat /persist/zot/access-control.json' > /tmp/projectctl-zot-current.json
   diff -u /tmp/projectctl-zot-current.json /tmp/projectctl-zot-access-control.json
   ~~~

   Stop if the diff contains unrelated changes. After approving that diff,
   install the policy and create only this bcrypt identity. The password prompt
   does not echo. Retain that password independently, then supply the same value
   to the SOPS collection step below.

   ~~~fish
   set registry_tools (nix build --impure --no-link --print-out-paths --expr 'let f = builtins.getFlake (toString ./flake); in f.inputs.nixpkgs.legacyPackages.x86_64-linux.apacheHttpd')
   nix copy --to ssh://nas-01 $registry_tools
   scp /tmp/projectctl-zot-access-control.json nas-01:/tmp/projectctl-zot-access-control.json
   ssh nas-01 'sudo test ! -e /persist/zot/htpasswd.before-projectctl && sudo install -o root -g root -m 0600 /persist/zot/htpasswd /persist/zot/htpasswd.before-projectctl'
   ssh -t nas-01 sudo $registry_tools/bin/htpasswd -B /persist/zot/htpasswd projectctl-pull
   ssh nas-01 'sudo chown root:zot /persist/zot/htpasswd; sudo chmod 0640 /persist/zot/htpasswd; sudo install -o root -g zot -m 0640 /tmp/projectctl-zot-access-control.json /persist/zot/access-control.json; sudo systemctl restart zot'
   curl --fail --user projectctl-pull --output /dev/null --write-out '%{http_code}\n' --header 'Accept: application/vnd.oci.image.index.v1+json,application/vnd.oci.image.manifest.v1+json' https://registry.rupan.dev/v2/upstream/ghcr.io/astral-sh/uv/manifests/sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58
   curl --user projectctl-pull --request POST --dump-header /tmp/projectctl-zot-upload.headers --output /dev/null --write-out '%{http_code}\n' https://registry.rupan.dev/v2/upstream/ghcr.io/astral-sh/uv/blobs/uploads/
   ~~~

   Require a successful authenticated read followed by denied upload (401/403).
   The second command intentionally tests a write boundary. If it returns 202,
   stop, correct the ACL and clean up the empty upload using its response
   Location before proceeding. No image or tag should be published. Keep the
   backup root-only and do not overwrite it on a repeated attempt. Follow the
   [registry operator procedure](local-registry.md) for recovery.
   Record its effective read-only ACL, verify a real image pull, and verify
   denied publication against a disposable authorized probe namespace.
3. Collect credentials without echoing them or writing plaintext:

   ~~~fish
   nix develop ./flake
   python -m scripts.projectctl.operator collect-credentials --output secrets/projectctl.sops.json
   ~~~

   The command prompts for all five values, encrypts in memory through SOPS,
   and writes only ciphertext. It refuses an existing destination and identical
   read/write tokens. The .sops.yaml rule uses both operator recovery recipients and the
   target host recipient. Retain the ciphertext through the protected repository workflow.
4. Review the prepared homelab-04 import and enabled pilot profile. Dispatch has
   no automatic timer or activation hook. The profile uses verified HTTPS at
   https://git.rupan.dev for Forgejo and loopback HTTP for Woodpecker. TLS
   verification is never disabled. Obtain worker deployment authorization before
   activation; Zot reader provisioning approval does not cover the worker.
5. Drain active builds before the policy restart. Review the full host change
   and recovery generation, then ship through the protected CI-host route:

   ~~~fish
   just ship --deploy deploy-ci-host
   ~~~

   The shipper runs the matching dry-run prerequisite and files held operations
   on the approvals dashboard. Approve the printed request before activation.
   A successful build alone does not establish host decryption or service health.
6. Deploy the reviewed host generation through the held CI-host route. The
   profile uses sops-nix to decrypt `secrets/projectctl.sops.json` on homelab-04
   using `/persist/etc/ssh/ssh_host_ed25519_key`. Its public age recipient is
   recorded alongside both operator recovery recipients in `.sops.yaml`.
   Credentials are mode-0400 files under `/run/secrets`. Observer tokens belong
   to projectctl-worker for direct API discovery; write tokens and the registry
   password belong to root. Systemd `LoadCredential` gives privileged invocations
   their private credential copies.
   No operator-side plaintext installation is required.

   ~~~fish
   ssh homelab-04 'sudo -n systemctl start projectctl-verify-credentials.service'
   ssh homelab-04 'sudo -n journalctl -u projectctl-verify-credentials.service -n 10 --no-pager'
   ~~~

   Preserve the historical root-only credential backups. Verification and each
   operator dispatch reject administrator identities and require distinct
   observer/provisioner accounts matching between both services. Verification
   does not certify write scopes or registry ACLs. Verify account/token settings
   and independent access tests before live enrollment. Rotate by updating the
   encrypted bundle, deploying, verifying, and only then revoking old tokens.

7. Verify the restricted caller using stdin JSON:

   ~~~fish
   printf '%s\n' '{"schema_version":1,"action":"shell","payload":{}}' | ssh -F /dev/null -T -i ~/.ssh/projectctl-caller -o HostName=10.0.30.14 -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o ForwardAgent=no -o ClearAllForwardings=yes projectctl-worker@homelab-04
   ~~~

   Expect structured INVALID_SPEC. The key uses restrict plus a fixed forced
   command, forbidding PTY, forwarding and user startup files. A remote command
   cannot select another action. Grant no sudo, Docker access, password or
   additional keys. The dedicated local SSH alias projectctl-worker selects
   HostName 10.0.30.14, User projectctl-worker, IdentityFile
   ~/.ssh/projectctl-caller and IdentitiesOnly yes, with forwarding and PTY
   disabled. Verify ssh -G reports only that identity. IdentityFile options are
   additive, so using -i alone with the ordinary homelab-04 alias also selects
   its administrative key.

Do not proceed with live enrollment until these identity and credential checks
are complete. Never copy the operator private key to the worker.

## Onboarding after prerequisites pass

~~~fish
set -gx PROJECTCTL_WORKER projectctl-worker
python -m scripts.projectctl adopt /home/rupan/projects/autocompress --repository JEFF7712/autocompress --project-key autocompress --ci python-uv-v1 --plan --json > /tmp/projectctl-plan.json
python -m scripts.projectctl request /tmp/projectctl-plan.json --idempotency-key autocompress-pilot-1 --json > /tmp/projectctl-request.json
set request_id (jq -r '.data.request_id' /tmp/projectctl-request.json)
python -m scripts.projectctl status $request_id --json
~~~

For a routine scope, the worker proceeds automatically. When a request waits at
policy, export its enrollment artifact (below), apply only the target entry to
current main's config/ci/projects.json (the artifact is a whole catalog at an
older base revision), and ship it with `just ship --deploy
deploy-ci-host-dry-run,deploy-ci-host`. Scope changes in config/projectctl/ route
to the same deployment. The policy service only accepts a deployment against the
current main tip, so when main is busy, retry from the newest main pipeline that
contains the merge rather than an older one. No operator signature is needed.

Doctor runs with read-only credentials, which Forgejo will not show branch
protection to. It reports protection from the latest verified readiness receipt
for that repository, naming the request and observation time. If an older signed request
is still pending, cancel it and generate a fresh plan after the worker upgrade;
implementation changes deliberately invalidate its policy digest.

### Signed scopes and qualification audits

For a scope without routine authorization, the separate operator procedure is:

~~~fish
python -m scripts.projectctl approval-context $request_id --json > /tmp/projectctl-context.json
~~~

Transfer those public documents to the independent operator machine. Compare
them with live Forgejo state and sign there:

~~~fish
read -P 'Operator private key path: ' operator_key
python -m scripts.projectctl.operator approve /tmp/projectctl-request.json --context /tmp/projectctl-context.json --key-file $operator_key --key-id projectctl-operator --ttl 900 | jq '.data' > /tmp/projectctl-approval.json
~~~

Merge the raw signed object as config/projectctl/approvals/REQUEST_ID.json
through protected review. The signature binds the request, principal, platform,
project, operations, plan and policy digests, original catalog revision and entry
digest, and expiry. Endpoint, implementation, public-key, clone-identity or
registry-policy changes invalidate older plans.

Signed requests run only when the operator starts execution:

~~~fish
ssh homelab-04 sudo systemctl start projectctl-dispatch@$request_id.service
python -m scripts.projectctl status $request_id --json
~~~

Export the prepared enrollment artifact on the worker with:

~~~fish
ssh homelab-04 sudo -u projectctl-worker projectctl-operator enrollment $request_id --config /etc/projectctl/config.json
~~~

Review only its target-project catalog diff against current main, preserving
unrelated entries. Merge and deploy policy through the existing operator
workflow, then dispatch the same request again. Renew expired grants for the
same request and plan using the original saved approval context.

Signed-scope final readiness requires the exact planned SHA, stamped server-owned
development-validation workflow, successful python-uv-test, matching Forgejo
status, a real push webhook, protected status checks applying to administrators,
and a signed request-bound protection probe. A manual build alone cannot pass.

For that probe, perform an authorized failing PR merge attempt and close the
PR unmerged. The operator's sign-probe command accepts the strict payload in
scripts/projectctl/proofs.py and signs the attestation of actual merge rejection.
Store its signed object as config/projectctl/probes/REQUEST_ID.json through
protected review. The worker independently checks the failed CI, closed
unmerged PR, planned SHA, rule digest and expiry. Configured rules alone do not
prove enforcement.

## Recovery and validation

The journal resides at /persist/projectctl/journal.sqlite, outside tmpfs root.
Directory mode is 0700 and files are 0600. SQLite commits attempts before writes;
a host lock serializes dispatch. Status stays readable during execution.
Every external write refreshes authorization. Revocation cannot retract a call
already dispatched.

A restarted worker rediscovers outcomes. A durable allocation receipt proves
ownership. A creation response lost before persistence blocks with
RESOURCE_CONFLICT; matching names are insufficient. Deleted/replaced allocations
require operator reconciliation. Cancellation retains resources.

Use projectctl-operator backup DESTINATION --config /etc/projectctl/config.json
as projectctl-worker for a consistent SQLite backup to a protected directory.
The destination must not exist. Restore with dispatch stopped and the original
owner/modes. Preserve artifacts, SOPS ciphertext and both public registries.
Loss of the journal loses automatic allocation ownership evidence. The existing
platform backup has not been qualified to restore this new service.

Withdraw access through an approved caller/module change, stop dispatch,
revoke dedicated tokens, and remove current approval records. Preserve receipts.
A host-generation rollback cannot undo completed API mutations.

~~~fish
nix develop ./flake -c python -m unittest tests.test_projectctl tests.test_projectctl_integrations tests.test_projectctl_module tests.test_development_ci -v
nix develop ./flake -c pyright scripts/projectctl
nix develop ./flake -c just check-changed
nix develop ./flake -c just fmt-check
~~~

Offline tests use loopback service fixtures and deterministic test keys. They
verify source behavior, gateway boundaries, recovery and evaluated Nix settings,
not deployed isolation or live pilot acceptance.

The old scripts/ci/migrate_project.py was removed. Broad token minting,
database-derived tokens, database edits, hardcoded registry credentials and
inline deployment are not retained. Treat the historically committed credential
as exposed: rotate it through the registry procedure and inspect history/log
exposure before rollout. Removing source does not revoke an active credential.
