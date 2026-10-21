# Approvals dashboard

`https://approvals.rupan.dev` handles exceptional high-risk CI operations.
Routine fleet, CI-host, OPNsense, Cloudflare and workspace deployments are
automatic. Authorization-system changes and destructive changes still require
the operator.
An agent running `just ship --deploy <ops>` without an operator token files a
request, prints its link, and waits. ntfy pushes the same link. Approving
asks for the operator's passkey; the dashboard then triggers the deployment
as the operator and `ship` follows the pipeline.

## Design

- **Service:** `scripts/approvals/` on homelab-04, module
  `flake/modules/approvals.nix`, port 8020, state in
  `/persist/approvals/approvals.sqlite`.
- **Filing:** `POST /api/requests` with the caller's own Woodpecker token as
  the bearer. The service resolves the login through Woodpecker, accepts
  catalog mutations, including otherwise automatic operations rejected by the
  deployed source-baseline or saved-plan risk checks. The parent must be a main
  pipeline with successful source validation; unrelated operation failures do
  not invalidate that validation. One pending request exists per operation and parent.
- **Approving:** needs a WebAuthn assertion with user verification from an
  enrolled passkey, made on the public origin against a single-use challenge
  bound to that request. An agent driving the operator's browser can click
  Approve but cannot produce the assertion. A request triggers at most once.
- **Execution:** the service holds the operator's Woodpecker token and calls
  the same deployment endpoint the Woodpecker UI does. CI's deployment policy
  still checks the operator identity, the current main commit and the parent
  pipeline, so a request approved after main moved fails rather than
  deploying an old commit; `ship` then files a fresh request on the new main.
- **Expiry:** pending requests expire after six hours.

The page on `ci.internal:8020` redirects to the public origin: passkeys are
bound to `approvals.rupan.dev`, so approval only works there. Agents use
`http://ci.internal:8020` for the API.

## Gates

1. Cloudflare Access application `approvals`, hostname `approvals.rupan.dev`,
   with an **Allow** policy on the owner email. Never Bypass. Created in the
   dashboard like the other private hosts (see
   [cloudflare-tunnel.md](cloudflare-tunnel.md)).
2. The passkey on every approval. Access is the outer gate; a mis-scoped
   Access policy still cannot approve anything.

## First-time setup

These steps are the operator's, once:

1. Deploy homelab-04 with the module: the `ci-host` route
   (`deploy-ci-host-dry-run`, `deploy-ci-host`) from the Woodpecker UI.
2. Create a Woodpecker token while signed in as the operator and store it
   on homelab-04 as `/persist/secrets/approvals-operator-token`
   (root, 0600). systemd passes the service a private copy.
3. Optional ntfy delivery: `/persist/secrets/approvals.env` (root, 0600)
   with `NTFY_TOPIC=<topic>` and `NTFY_TOKEN=<token with publish access>`.
   Then `sudo systemctl restart approvals`.
4. DNS `CNAME approvals.rupan.dev ->
   0f08d8c5-6f2c-409e-ba80-dc0601e0227e.cfargotunnel.com` and the Access app
   above, in the Cloudflare dashboard; then the `cloudflare` route
   (`cloudflare-plan`, `cloudflare-apply`) pushes the tunnel rule.
5. Enroll a passkey: `ssh homelab-04 sudo approvals-enroll` prints a
   single-use link valid for 15 minutes. Open it and create the passkey. Run
   it again to add a second device.

## Operations

- Status: `systemctl status approvals`, `journalctl -u approvals`,
  `curl http://ci.internal:8020/healthz`.
- Revoke a passkey: stop the service and delete its row from the
  `credentials` table, or delete `approvals.sqlite` and re-enroll.
- Rotate the operator token: replace the file and restart the service.
- If the dashboard is down, held operations still run from the Woodpecker UI
  as the operator.
