{ lib, servers }:
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
in
lib.concatStringsSep "\n" (
  [
    "apiVersion: v1"
    "kind: ConfigMap"
    "metadata:"
    "  name: coredns-custom"
    "  namespace: kube-system"
    (if names == [ ] then "data: {}" else "data:")
  ]
  ++ lib.concatMap block names
  ++ [ "" ]
)
