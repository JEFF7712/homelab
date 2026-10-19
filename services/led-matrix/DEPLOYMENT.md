# Homelab deployment

The hybrid pipeline uses Spotify Web API polling as the primary low-latency source
(~1.5s interval while playing) for sub-second responsiveness when pausing/skipping,
with Home Assistant WebSocket events as a seamless fallback if Spotify encounters
rate limits (HTTP 429) or quota errors. Frames render via local Pillow and stream
acknowledged TCP frames to Pico W `10.0.20.136:9999`.

## Resources

- Namespace `spotify-matrix` with restricted pod security.
- OPNsense reserves `.136` and permits only the five Kubernetes node addresses
  to reach the Pico on TCP 9999, before the existing private-VLAN deny rule.
- One renderer, with `Recreate` updates to avoid competing Pico connections.
- Non-root UID/GID 1000, read-only root filesystem, no Linux capabilities or
  Kubernetes API token, and a 16 MiB memory-backed temporary directory.
- ExternalSecret `spotify-matrix-ha`, reading `SPOTIFY_MATRIX_HA_TOKEN`,
  `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, and `SPOTIFY_REFRESH_TOKEN` from
  the existing SOPS-backed `homelab-values` store. The token mounts read-only
  at `/run/secrets/ha/token` and Spotify credentials are provided as environment variables.
- Readiness checks HA/Spotify connectivity and recent Pico acknowledgements. Liveness
  checks the render loop's health-file timestamp. Idle playback remains ready.
- Cilium policy allows DNS, HA TCP 8123, the Pico's TCP 9999, HTTPS artwork
  from `*.scdn.co` and `*.spotifycdn.com`, and Spotify endpoints `api.spotify.com`
  and `accounts.spotify.com`. No ingress is required.
- Flux depends on HA, ESO, and secrets. The image is pinned to the locally built
  manifest digest and registered as an unpublished candidate in the registry lock.

HA currently uses host networking. Egress to cluster hosts and remote nodes on
TCP 8123 allows HA failover. A namespace endpoint rule also covers a future
ordinary HA pod. This permits port 8123 on cluster nodes, rather than arbitrary
node ports. The policy needs live cluster qualification; local Docker testing
does not exercise Cilium or Kubernetes service routing.

The renderer connects to HA's existing BGP service VIP `10.0.40.13:8123`.
The first rollout found DNS requests stalled at the node's transparent DNS
proxy. Using the stable service VIP avoids that dependency for HA metadata and
its relative artwork proxy URLs, while retaining HA backend failover.

## Publication and rollout

For subsequent fixes, use the protected `matrix-publish` operation described in
[Matrix releases](../../docs/runbooks/matrix-release.md). It uses the matrix's
explicit publisher credential, verifies the image digest, and emits a focused
Flux promotion patch. It does not use the laptop's default Docker login.

Repository `AGENTS.md` requires authorization before publishing or changing
shared infrastructure. The local build and qualification do not constitute a
deployment. Deployment was authorized on 2026-10-16:

1. Review the OPNsense CI plan and apply the prepared DHCP reservation for
   `28:cd:c1:0d:31:05` at `10.0.20.136`. Confirm the reservation and renewal.
   During preparation the Pico moved from `.131` to `.136`; USB console status,
   ARP, and its `R565` TCP handshake confirmed the new address. The renderer
   and Cilium policy explicitly use `.136`. The HA CLI default also uses `.136`.
2. Provision a dedicated local-only read-only HA account and long-lived token for the
   renderer. HA long-lived tokens are account credentials, not entity-scoped
   capabilities. Keep the token out of chat, shell arguments, image layers,
   plaintext Git, and logs. The operator token used for local qualification is
   not a production credential.
3. Add `SPOTIFY_MATRIX_HA_TOKEN` to `gitops/secrets/homelab-values.sops.yaml`
   using SOPS, without changing unrelated values.
4. Provision the project-specific `publisher-spotify-matrix` registry identity
   using `docs/runbooks/local-registry.md`. The candidate lock entry prepares
   its repository-specific access policy. Do not reuse another application's
   publisher credential. A new scoped publisher was provisioned with protected rollback files,
   preserving all unrelated live registry users and policy entries.
5. Publish the qualified image and both lock-listed tags to
   `registry.rupan.dev/apps/spotify-matrix`. Verify the remote manifest digest
   equals the Deployment pin and record publication evidence in the lock.
6. Review and publish the focused GitOps change, then let Flux reconcile.
   Keep all other senders stopped, including laptop test services and USB CDC
   data clients. The Pico prioritizes an open USB data connection over TCP.

The image must be published and its remote digest verified before cluster
rollout. A local tag alone does not make it available to cluster nodes.
Token rotation requires restarting the renderer after ESO updates the mounted
Secret, because the process reads its HA credential at startup.

## Rebuilding

Use the locked service environment. Unset inherited `PYTHONPATH` for `uv`
commands so Nix's Python packages cannot override the service virtual environment.
Export hashed runtime requirements, then download the CPython 3.12 Linux wheels:

```bash
env -u PYTHONPATH uv export --frozen --no-dev --no-emit-project \
  --format requirements-txt --output-file requirements.txt > /dev/null
env -u PYTHONPATH uv run --with pip python -m pip download \
  --require-hashes --only-binary=:all: --python-version 312 \
  --platform manylinux_2_28_x86_64 --platform manylinux_2_27_x86_64 \
  --platform manylinux2014_x86_64 --abi cp312 --abi abi3 --abi none \
  --dest wheelhouse -r requirements.txt
docker buildx build --load --provenance=false \
  --tag registry.rupan.dev/apps/spotify-matrix:ha-reviewed \
  --metadata-file build-metadata.json .
```

The Dockerfile pins the Python base by digest. Local preparation used the same
pinned digest from Docker Hub through `PYTHON_BASE` because mirror credentials
were rejected. Application installation uses only the hashed wheelhouse, with
no package index access during the Docker build. The deny-by-default Docker
context includes only the three runtime modules, requirements, and wheels.
The image has neither `.env` nor a Spotify token cache.

If rebuilt image bytes change, qualify that image again and update the
Deployment, inventory, and lock to its actual manifest digest before publication.

## Acceptance and recovery

The renderer sends at most four TCP frames before waiting for display replies.
This lets short acknowledgement delays pass without stopping frame submission,
while limiting outstanding data to 32 KiB. Health frame counts and readiness
advance only on actual Pico acknowledgements. `max_frame_ms` records the longest
render and send iteration since startup for diagnosing visible pauses.

On 2026-10-16, the initial local container delivered 1,294 acknowledged frames
over 65.02 seconds at 19.90 FPS, with zero failed frames or HA reconnects.
Maximum render-to-ack time was 85.0 ms. Real HA metadata and artwork loaded;
readiness passed with UID 1000, a read-only filesystem, and dropped capabilities.
This qualifies image manifest
`sha256:519fb77a8ebb14a5efc2ce66136c5cecd684c7fb6ccb76e3d3df16065f5b90fb`
on the laptop against the live Pico at `.136`. Frame spacing can vary with Wi-Fi.
All qualification containers were stopped and removed afterward.

The renderer uses `subscribe_entities` with an explicit entity list. The
initial cached state and later compact deltas arrive on the same subscription,
avoiding a state-read race and requiring only read permission. The earlier
`subscribe_trigger` draft required HA administrator access and was replaced
before deployment. Attribute removals clear stale artwork. The replacement
container delivered 1,287 acknowledged frames in 65.03 seconds at 19.79 FPS
with zero frame failures or HA reconnects using the dedicated read-only account.

Offline tests exercise HA event filtering, heartbeat traffic, disconnect and
resubscribe behavior, stale metadata, bounded artwork caching, credential
isolation, Pico handshakes, and transport reconnection. Local container tests
use the real HA entity and Pico, without invoking Spotify refresh services.

After deployment, verify ExternalSecret synchronization, Flux readiness, image
pulls, HA subscription, artwork loading, and acknowledged Pico delivery. Test
playing, pause, track changes, idle playback, and recovery after a renderer
restart. Confirm cluster-to-Pico routing and Cilium DNS/artwork behavior.
Power off the laptop and confirm playback changes still reach the matrix.
These are deployment acceptance steps, not claims about the local test.

To stop or roll back the deployment, revert its Flux inclusion through GitOps
and reconcile the change. Confirm the sender exits; the Pico returns to its
firmware idle UI after eight seconds without a complete frame. Keep the legacy
Spotify poller disabled so rollback cannot resume consuming its API quota.
