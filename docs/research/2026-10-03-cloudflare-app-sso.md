# Cloudflare application SSO pilot

## Observed configuration

Read-only Cloudflare API inspection on 2026-10-03 found:

- Account: `5d99d63dea23dde67ccb07e5dfb31107`.
- Authentication domain: `rupan.cloudflareaccess.com`.
- Only configured login method: one-time email PIN,
  `d052562e-7c79-4bcc-bb0c-1808eb5e9c66`.
- No Access for SaaS applications, so no existing OIDC clients.
- The `*.rupan.dev` application allows `rupanpandyan@gmail.com` through reusable
  policy `d41ac5f5-7568-4a0d-a737-9968291d2fa2`.
- Grafana runs version `13.0.1+security-01` and has anonymous access disabled.
- Home Assistant, Music Assistant, `music.rupan.dev`, and `books.rupan.dev`
  have explicit Access bypass policies. Their application login is therefore
  not a duplicate Cloudflare login on those hostnames.

## Deployed Grafana integration

The local changes enable Generic OAuth, automatic login, PKCE, and ID-token
signature validation. Grafana grants server administrator privileges only when
the authenticated email equals the owner email. Strict role mapping rejects
other identities even if an upstream policy later broadens. The subject claim
identifies the new OAuth account; existing local accounts are not automatically
linked by email. The existing admin credentials remain available.

The new ExternalSecret uses the existing GitLab-backed secret store to supply
the client ID, secret, and client-specific endpoints. No credentials are in git.
Publishing before those variables exist would prevent the Grafana pod from
starting because its environment Secret would be missing. Provision credentials
and verify the Secret before publishing the HelmRelease change.

## Cloudflare creation request for review

Create one new application through `POST /accounts/5d99d63dea23dde67ccb07e5dfb31107/access/apps`:

```json
{
  "name": "Grafana OIDC",
  "type": "saas",
  "allowed_idps": ["d052562e-7c79-4bcc-bb0c-1808eb5e9c66"],
  "policies": [{"id": "d41ac5f5-7568-4a0d-a737-9968291d2fa2"}],
  "saas_app": {
    "auth_type": "oidc",
    "app_launcher_url": "https://grafana.rupan.dev",
    "redirect_uris": ["https://grafana.rupan.dev/login/generic_oauth"],
    "scopes": ["openid", "email", "profile"],
    "grant_types": ["authorization_code_with_pkce"],
    "allow_pkce_without_client_secret": false,
    "access_token_lifetime": "15m"
  }
}
```

Before creation, recheck that no client with this name exists and that the
reusable policy still allows only the owner. Verify the new application's
policy and OIDC discovery document before enabling Grafana. Existing wildcard,
bypass, tunnel, DNS, and other application policies need no changes.

The client secret is returned only by the creation response. Persist it directly
to the protected secret store without logging the response. Variables:
`GRAFANA_CLOUDFLARE_OIDC_CLIENT_ID` and
`GRAFANA_CLOUDFLARE_OIDC_CLIENT_SECRET`.

## Deployment and acceptance

The user approved client creation, credential provisioning, and publication on
2026-10-03. The deployment procedure is:

1. Create the reviewed OIDC application and persist its credentials securely.
2. Publish the ExternalSecret and kustomization first through Flux, verify
   `Ready=True`, then publish the HelmRelease change.
3. Verify Flux reconciliation, Grafana readiness, and public health.
4. With a fresh browser session, authenticate through Cloudflare once and
   confirm the OAuth callback reaches Grafana without a Grafana password prompt.
   Confirm the signed-in account and administrator role.
5. Verify an unrelated email is denied and a forged identity header cannot
   authenticate directly to the Grafana origin.
6. Verify recovery at `/login?disableAutoLogin=true` with the existing local
   admin credentials, including access over LAN/NetBird if Cloudflare is down.

Rollback: revert the HelmRelease OAuth changes through Flux first. Once local
admin login is verified, remove the new ExternalSecret and new OIDC client.
Preserve the original wildcard Access policy throughout.

## Subsequent applications

- Immich supports OAuth, but inspect its current settings and account linking
  first. Validate both browser and mobile redirects before enabling auto-launch.
- Navidrome supports trusted proxy authentication. A header-based integration
  needs verified Access JWTs, restricted origin paths, and an explicit mapping to
  existing usernames. Simply trusting an email header would allow spoofing.
- Other apps require individual protocol checks. Do not remove native login
  merely because a public hostname passes through Access.
- The pod-agent dashboard deliberately requires its own authorization token;
  changing that is a separate business authorization design change.

## Local verification

- `nix develop ./flake -c just check-changed`: documentation and GitOps gates passed.
- `nix develop ./flake -c just fmt-check`: passed.
- YAML lint and observability Kustomize build: passed.
- Helm render using the pinned `kube-prometheus-stack` chart `91.4.1`: verified
  the Grafana environment Secret, public callback base, auto-login, strict roles,
  PKCE, ID-token validation, and retained local login.
- JMESPath evaluation: owner maps to `GrafanaAdmin`; unrelated, missing, null,
  and lookalike email claims return no valid role.

## Deployment verification

Completed on 2026-10-03 local time (2026-10-04 UTC):

- Created Access for SaaS application `448b175a-dd78-4d6a-9847-8c122b3a9759`.
  Readback confirmed the existing owner-only reusable policy, exact callback,
  PKCE flow, and secret requirement. OIDC discovery matched all configured
  endpoints.
- Provisioned both protected GitLab variables and verified their values without
  printing credentials. The client secret is masked.
- Published credential wiring in `16b329ea07b7e11b02ef814b5aff571e8b1bf745`.
  Flux applied it and the ExternalSecret reached `SecretSynced`, `Ready=True`
  before Grafana authentication changed. Woodpecker pipeline 20 passed.
- Published OAuth configuration in `ba25213bbd2d665c0797d097b622a3014237e87a`.
  Flux observability applied that revision; Grafana rolled out successfully and
  the HelmRelease reached `Ready=True` at revision 21. Woodpecker pipeline 21
  passed.
- Browser verification: Cloudflare email PIN authentication returned directly
  to Grafana without a Grafana password prompt. `/api/user` returned HTTP 200,
  the owner email, `Generic OAuth`, `isExternal=true`, and
  `isGrafanaAdmin=true` for the newly created OAuth account.
- Direct LAN verification: unauthenticated `/api/user` and forged Cloudflare
  email/JWT headers returned HTTP 401. The recovery login URL remained on the
  LAN origin, and the original local admin credentials authenticated with
  administrator privileges. `/api/health` reported database `ok`.
- After explicit Grafana logout, re-entry returned to Cloudflare's PIN form.
  Cross-application session reuse was not established by this pilot. The
  verified improvement is removal of Grafana's separate password prompt.

The unrelated-email rejection check evaluates the role expression locally and
checks the live Cloudflare policy. It does not claim a completed login with a
second real email account.

## Sources

- [Cloudflare generic OIDC](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/saas-apps/generic-oidc-saas/)
- [Grafana Generic OAuth](https://grafana.com/docs/grafana/latest/setup-grafana/configure-access/configure-authentication/generic-oauth/)
- [Immich OAuth](https://docs.immich.app/administration/oauth/)
- [Navidrome external authentication](https://www.navidrome.org/docs/usage/integration/authentication/)

Local validation and authenticated browser acceptance were both completed.
Existing wildcard and bypass policy definitions, DNS, tunnel ingress, and other
applications were preserved.
