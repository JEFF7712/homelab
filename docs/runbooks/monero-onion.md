# Monero onion P2P

`homelab-04` runs a Tor v3 onion service for monerod's anonymous P2P listener
on port 18084. Tor connects to `127.0.0.1:18084`; the onion address is read
from `/var/lib/tor/onion/monero/hostname` at startup and supplied to monerod
through `/run/monero-onion.env`. Tor's state under `/var/lib/tor` persists on
`/persist`, so rebooting does not change the onion address.

The service does not require an inbound router port. The clearnet P2P listener
on port 18080 still supports outbound connections. Its NAT forward cannot
receive traffic through the apartment building's upstream router.

After deployment, check `tor.service`, `monero-onion-env.service`, and
`monero.service` on `homelab-04`. Read the onion address from
`/var/lib/tor/onion/monero/hostname`; do not copy its private key. Use a Tor
client on a separate host to connect to the onion address on port 18084, then
check monerod's `get_info` response and logs for anonymous inbound activity.
If startup fails, check the three units' journals and the persistent Tor
directory before restarting monerod. The `monero-onion-env` unit deliberately
blocks monerod startup if Tor has not generated a hostname.
