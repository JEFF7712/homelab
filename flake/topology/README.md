# Declared infrastructure topology

`main.svg` shows hosts, extracted interfaces and selected services. `network.svg`
groups interfaces by declared network. `topology.json` provides the same nodes
and interfaces, OPNsense VLAN assignments, declared BGP peers, source identity,
and explicit coverage gaps.

The networkd adapter reads named units and static addresses/gateways, accepting
both scalar and list forms. Wildcard interface matches and bridge membership
are not expanded into physical topology.

These are configuration artifacts, not a live connectivity or health check.
NixOS facts come from the seven canonical `flake/hosts/` configurations. The
topology module extends them only for diagram evaluation; production host
configurations do not import it. Deployment variants are excluded.
OPNsense VLANs and gateway addresses come from
`opnsense_reconciler/assignments.json`; BGP intent comes from
`opnsense_reconciler/bgp-neighbors.json`. A matching declared interface gateway
assigns the corresponding network. Physical connections are never inferred.

## Coverage gaps

- OPNsense is external; only its declared VLAN interfaces are shown.
- Switch ports, cabling and the WAN handoff are unknown. The external switch
  fabric is deliberately disconnected in the diagram.
- Cloudflare is an external marker; tunnel routes are not extracted.
- Kubernetes workloads and service routes, NetBird overlay paths and custom VM
  guests are not modeled. Extracted virtual interfaces may be unassigned.
- BGP peers are supplied as structured intent, not drawn as physical links or
  claimed to be established sessions.
- Service extraction is partial. Zot, Woodpecker, k3s and NetBird have explicit
  adapters for this repository; other services use upstream extractors.

## Build and CI artifacts

Run `just topology` from the repository root. The result is exposed at
`artifacts/topology/`, containing both SVGs, the JSON manifest and this file.
The command builds documentation only and requires no fleet credentials.

Full CI validation builds `checks.x86_64-linux.topology`. Its immutable Nix
output is also included in the existing protected Attic cache publication.
Scoped host and OPNsense source checks also rebuild `artifacts/topology/`.
Retrieve that artifact by building `packages.x86_64-linux.topology` from the
desired source revision with the repository's configured cache. Sandbox PR
validation does not receive publication credentials. No new publishing service
or operation is introduced. Local dirty/path builds can have a null source
revision; their source NAR hash still identifies the input snapshot.
