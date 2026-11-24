# LAN gateway

On the LAN, selected `*.rupan.dev` names resolve to an in-cluster HTTPS router
instead of the Cloudflare Tunnel. The URL stays the same at home and away;
local traffic no longer leaves the house.

## How it works

1. `config/lan-gateway.json` owns the host list and the gateway VIP
   (`10.0.40.19`).
2. `inventory/lan-gateway.nix` turns each host into an OPNsense Unbound host
   override for that VIP. AdGuard carries no override, and CoreDNS forwards
   `rupan.dev` to AdGuard first (Unbound only if AdGuard fails), so pods keep
   resolving the public names. A plain `/etc/resolv.conf` forward picked
   Unbound half the time and pods timed out on the VIP.
3. `python -m scripts.lan_gateway` renders `gitops/lan-gateway/nginx.conf` from
   the tunnel rules in `gitops/cloudflare/ingress-config.yaml`. Each host keeps
   its tunnel rules in order, path rules included (Vaultwarden's `/admin` stays
   a 404).
4. `gitops/lan-gateway/` runs two nginx replicas in the `cloudflare` namespace,
   behind the BGP VIP. Application network policies already admit that
   namespace, so the router reaches exactly what the tunnel reaches.
5. cert-manager issues one `*.rupan.dev` certificate through the existing
   `letsencrypt-prod` DNS-01 ClusterIssuer. Reloader restarts the router on
   renewal.

Unknown names get a refused TLS handshake. Port 80 redirects to HTTPS.

## Which hosts qualify

Only hosts whose Cloudflare Access applications all use the `bypass` policy.
Access never sees LAN traffic, so a gated host would lose its login, and
several gated apps (the arr stack, qBittorrent) trust "local" source addresses
and would open completely. `tests/test_lan_gateway.py` enforces this.

Not in the list, deliberately:

- Access-gated hosts: `approvals`, `git`, `games`, `sovereign`, `tally`, and
  everything under the `*.rupan.dev` default policy.
- Names with an older static override in `inventory/dns.nix` (`jellyfin`,
  `nix`, `registry`, `npm`, `restic`).
- The apex `rupan.dev`: an override would answer MX and TXT lookups with no
  data on the LAN.
- The Flux webhook host.

## Change the host list

1. Edit `config/lan-gateway.json`. Set `"forwardClientAddress": false` for an
   origin that rejects proxies it does not trust (Home Assistant).
2. To retire a host, set `"enabled": false`. Do not delete the entry: the
   OPNsense reconciler refuses to remove overrides it did not create, and a
   disabled entry also drops the router's server block.
3. Render and test:

   ```sh
   python -m scripts.lan_gateway
   nix develop ./flake -c python -m unittest tests.test_lan_gateway -v
   just tofu-render    # regenerates opnsense_reconciler/unbound-host-overrides.json
   just check-changed
   ```

4. Ship with `just ship`. The change routes to the OPNsense operations
   (Unbound overrides) and Flux (router).

A tunnel rule change for a listed host needs `python -m scripts.lan_gateway`
too; `scripts/checks/gitops.sh` fails while the rendered config is stale.

## Verify

From a LAN client:

```sh
dig +short photos.rupan.dev @10.0.20.1           # 10.0.40.19
curl -sI https://photos.rupan.dev | head -1       # valid certificate, app response
curl -s -o /dev/null -w '%{http_code}\n' https://vault.rupan.dev/admin   # 404
```

In the cluster:

```sh
kubectl -n cloudflare get certificate lan-gateway        # READY True
kubectl -n cloudflare get svc lan-gateway                # EXTERNAL-IP 10.0.40.19
kubectl -n cloudflare logs deploy/lan-gateway --tail=50
```

If the Service stays `<pending>`, another LoadBalancer Service already holds
`10.0.40.19` through automatic allocation; pick a free pool address and update
`config/lan-gateway.json`, `gitops/lan-gateway/service.yaml`, the rendered
overrides, and the `/healthz` target in
`opnsense_reconciler/lb-data-plane-targets.json` together.

`vip-dataplane-watch` probes `http://10.0.40.19/healthz` through OPNsense every
maintenance run and posts to ntfy when the VIP stops answering; see
[OPNsense BGP proof](opnsense-bgp-proof.md#vips-time-out-while-bgp-is-up).

## Recovery

Clients fall back to the tunnel only when the override is gone. If the router
is broken, set every host to `"enabled": false` and ship. The overrides then
stop answering and names resolve publicly again. A client can also bypass
the override at once by using a public resolver.
