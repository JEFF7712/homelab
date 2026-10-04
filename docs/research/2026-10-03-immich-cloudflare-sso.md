# Immich Cloudflare OAuth

## Configuration

Cloudflare Access SaaS OIDC application `d694f134-6774-4247-80e2-19d0e32690eb`
uses the existing owner-only email policy and email PIN identity provider.
The client secret and preserved Immich storage template are encrypted together
in `gitops/secrets/immich-oidc-config.sops.yaml`. Flux decrypts the Secret;
Immich loads `/config/immich.json` through `IMMICH_CONFIG_FILE`.

This file becomes the authoritative system configuration. Immich disables
admin UI settings edits while it is configured. Preserve existing configuration
when updating the encrypted file. The previous database configuration remains
available if the environment variable and config mount are reverted.

Cloudflare discovery does not advertise `code_challenge_methods_supported`.
Immich 3.1.0 consequently does not enable PKCE. This client uses the confidential
authorization-code flow with `client_secret_post`, state validation, RS256 ID
tokens and TLS discovery. Grafana's independent PKCE client is unchanged.

Registered redirects:

- `https://photos.rupan.dev/auth/login`
- `https://photos.rupan.dev/user-settings`
- `https://photos.rupan.dev/api/oauth/mobile-redirect`

The mobile override maps Immich's custom callback to the HTTPS redirect above.
Immich forwards the authorization response to `app.immich:///oauth-callback`.
The existing hostname already has an Access bypass, allowing native Immich
API tokens to work. No Access bypass or tunnel route was changed.

Auto-registration is disabled. Existing account email linking remains enabled
by Immich. Password login remains enabled for recovery. If auto-launch is
enabled, `/auth/login?autoLaunch=0` exposes the password login screen.

## Baseline and acceptance

Before rollout, the sole owner account was
`48ef9e7c-5a34-4315-9846-9328fb14ccf6`, email `rupanpandyan@gmail.com`, admin,
with no OAuth identity. The database contained 6,905 assets.

Real-device mobile login and upload require testing in the installed phone app;
a browser or protocol-level redirect test does not establish that acceptance.

Browser OAuth linked the existing owner account, retained administrator access
and rendered 23 authenticated photo thumbnails. The asset count remained 6,905.
Unauthenticated `/api/users/me` returned 401. The mounted file passed the
installed Immich configuration schema and retained `storageTemplate.enabled`.

Mobile authorization returned 201 with the configured HTTPS callback, `code`
response type and state. The public mobile redirect returned 307 to the Immich
custom scheme while preserving code and state. A real mobile code exchange and
upload are still unverified. A second browser authorization prompted for email
verification again, so cross-application Cloudflare session reuse is not proven.

Auto-launch is enabled after browser acceptance. Bump the server pod annotation
`homelab.rupan.dev/oidc-config-revision` with config changes so Immich restarts
with the new cached configuration.

## References

- [Immich OAuth](https://docs.immich.app/administration/oauth/)
- [Immich configuration file](https://docs.immich.app/install/config-file/)
