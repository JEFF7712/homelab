# Outside-in uptime monitor

`home-pc-01` runs Gatus (`flake/hosts/home-pc-01/uptime-monitor.nix`) and
probes every public hostname from another network every two minutes, seeing
what a visitor sees: DNS, Cloudflare, the tunnel and the backend together. The
list lives in `config/uptime/external-endpoints.json`;
`tests/test_uptime_monitor.py` fails when a hostname in
`gitops/cloudflare/ingress-config.yaml` is neither probed nor listed under
`excluded`, so add new sites there when exposing them.

An endpoint is up when it answers below HTTP 500 within 10 s with a
certificate valid for more than 72 h. Access-gated sites answer with a login
redirect, which still proves the path; tunnel and backend failures surface as
5xx (502, 530, 1033).

## Where to look

- **Public status page:** <https://uptime.rupan.dev>, open to anyone (Access
  app `Bypass - Uptime status`). A second Gatus instance (`gatus-public`, port
  8081, no metrics) lists only the hosts under `public` in the endpoint file:
  sites and apps anyone can already open without a Cloudflare Access login.
  Admin, private and Access-gated hosts never appear there; a test enforces it.
  It reaches the tunnel through the `home-pc-01-status` relay
  (`10.0.30.10:2203`) and the `cloudflare/uptime` Service, so a total home
  outage also takes this page down; the PC keeps the history either way.
- **Private status page and 30-day history:** `http://100.94.53.129:8080` (or
  `home-pc-01.netbird.cloud:8080`) from a `homelab-admins` NetBird device.
  Gatus keeps its history in `/var/lib/gatus/data.db` on the PC.
- **Prometheus:** job `home-pc-01-uptime`, scraped through the
  `home-pc-01-uptime` relay on `adguard-netbird-01` (`10.0.30.10:2202` to the
  PC's 8080, cluster network only; see [NetBird](netbird.md)). Per-endpoint
  `gatus_results_endpoint_success`, response times and certificate expiry,
  plus recorded uptime ratios `homelab:external_endpoint_up:ratio_24h` and
  `ratio_7d` (Prometheus keeps 7 days).

## Alerts (`uptime.rules`)

| Alert | Meaning |
|---|---|
| `ExternalEndpointDown` (warning) | One site has failed for 10 minutes while most others answer: look at that service and its tunnel route. |
| `ExternalHomelabUnreachable` (critical) | More than half the sites fail together: suspect the uplink, Cloudflare or cloudflared. `network-monitor` and its Healthchecks.io heartbeat cover the inside view. |
| `ExternalUptimeMonitorDown` (warning) | No scrape for an hour: the PC is off or off NetBird, or the relay is down. |

Alerts reach ntfy through the home Alertmanager, so a total home outage is
reported by the `network-monitor` Healthchecks.io heartbeat, not by this probe.
