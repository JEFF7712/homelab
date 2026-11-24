{
  lib,
  servers,
  publicUpstreams,
}:
let
  names = builtins.filter (name: servers.${name} != [ ]) [
    "registry"
    "internal"
  ];
  block =
    name:
    let
      records = servers.${name};
    in
    [
      "  ${name}.server: |"
      "    ${lib.concatMapStringsSep " " (record: "${record.domain}:53") records} {"
      "        errors"
      "        cache 30"
      "        hosts {"
    ]
    ++ map (record: "            ${record.answer} ${record.domain}") records
    ++ [
      "            fallthrough"
      "        }"
      "        forward . /etc/resolv.conf"
      "    }"
    ];
  # OPNsense Unbound answers LAN-gateway names with a private VIP that pods
  # cannot reach, and a plain resolv.conf forward picks a resolver at random.
  # Ask AdGuard (public answers plus the static rewrites) first; Unbound
  # serves only when AdGuard fails.
  public = [
    "  public.server: |"
    "    rupan.dev:53 {"
    "        errors"
    "        cache 30"
    "        forward . ${lib.concatStringsSep " " publicUpstreams} {"
    "            policy sequential"
    "        }"
    "    }"
  ];
in
lib.concatStringsSep "\n" (
  [
    "apiVersion: v1"
    "kind: ConfigMap"
    "metadata:"
    "  name: coredns-custom"
    "  namespace: kube-system"
    "data:"
  ]
  ++ lib.concatMap block names
  ++ public
  ++ [ "" ]
)
