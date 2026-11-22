# Vaultwarden

Bitwarden-compatible password manager at `https://vault.rupan.dev`, owned by
`gitops/vaultwarden/` (Flux Kustomization `vaultwarden`). Use the official
Bitwarden apps and browser extension with the self-hosted server URL
`https://vault.rupan.dev`.

## Architecture

- **App:** Deployment `vaultwarden/vaultwarden`, one replica with the
  `Recreate` strategy, image
  `registry.rupan.dev/upstream/docker.io/vaultwarden/server` (declared in
  `gitops/vaultwarden/registry-sources.yaml`, locked in
  `registry/images.lock.json`). It runs as UID 1000 on a read-only root
  filesystem and listens on 8080.
- **Data:** SQLite, attachments, Sends and the JWT signing key live on the
  `vaultwarden-data` claim (`nfs-cluster`, nas-01 `/tank/cluster`, never
  pruned). `ENABLE_DB_WAL=false` because SQLite WAL needs shared memory, which
  NFS does not provide. Never run a second replica.
- **Backups:** the claim directory is covered by the `tank/cluster` sanoid
  snapshots and the nightly external restic backup of the frozen
  `tank/cluster@backup-nightly` snapshot
  ([external-backup-recovery](external-backup-recovery.md)). It is not yet in
  the hourly off-site `platform-backup` set. Vault items are encrypted on the
  client with the master password, so a backup without that password is
  unreadable.
- **Accounts:** `SIGNUPS_ALLOWED=false`. Only invited emails can register.
  Password hints are disabled.
- **Admin page:** `ADMIN_TOKEN` is generated in-cluster once by ESO
  (`vaultwarden-admin`, `refreshPolicy: CreatedOnce`) and is not stored in
  SOPS. The tunnel answers `vault.rupan.dev/admin` with 404, and the
  NetworkPolicy only admits the `cloudflare` namespace on 8080, so the admin
  page is reachable only through `kubectl port-forward` or `kubectl exec`.
- **Edge:** tunnel rules `vault.rupan.dev` (path `^/admin`) `->
  http_status:404`, then `vault.rupan.dev -> http://vaultwarden.vaultwarden:80`.
  The Access app `Bypass - Vaultwarden` bypasses the wildcard owner login,
  because the Bitwarden apps cannot complete a Cloudflare Access login. The
  hostname is in `tofu/cloudflare/personal-apps.nix`, so it is
  country-restricted like the other personal apps. Vaultwarden's own login
  rate limits use `CF-Connecting-IP`.

## Inviting a user

Without SMTP, an invite only records the email; the person then registers
at `https://vault.rupan.dev/#/signup` with that email. The token never leaves
the pod:

```sh
kubectl -n vaultwarden exec deploy/vaultwarden -- sh -c '
  jar=/tmp/admin.jar
  curl -fsS -c "$jar" -o /dev/null --data-urlencode "token=$ADMIN_TOKEN" http://127.0.0.1:8080/admin
  curl -fsS -b "$jar" -H "Content-Type: application/json" \
    -d "{\"email\":\"person@example.com\"}" http://127.0.0.1:8080/admin/invite
  rm -f "$jar"'
```

For the full admin page, `kubectl -n vaultwarden port-forward
deploy/vaultwarden 8080` and open `http://127.0.0.1:8080/admin`; read the
token with `kubectl -n vaultwarden get secret vaultwarden-admin -o
jsonpath='{.data.ADMIN_TOKEN}' | base64 -d`.

After registering, turn on two-step login (Settings, Security, Two-step
login) and save the recovery code outside the vault.

## Restore

1. Scale the Deployment to zero:
   `kubectl -n vaultwarden scale deploy/vaultwarden --replicas=0`.
2. Copy `db.sqlite3`, `attachments/`, `sends/` and `rsa_key.pem` from a
   `tank/cluster` snapshot (or the external restic repository) into the claim
   directory on nas-01, preserving UID 1000.
3. Scale back to one replica and log in from a client.
