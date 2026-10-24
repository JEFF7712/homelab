{ lib, hosts }:
let
  services = {
    forgejo = {
      address = hosts.nas-01.address;
      port = 3000;
    };
    woodpecker = {
      address = hosts.homelab-04.address;
      port = 8000;
    };
    approvals = {
      address = hosts.homelab-04.address;
      port = 8020;
    };
  };
in
lib.mapAttrs (name: service: ''
  apiVersion: v1
  kind: Service
  metadata:
    name: ${name}
    namespace: cloudflare
  spec:
    ports:
      - name: http
        port: ${toString service.port}
        targetPort: ${toString service.port}
  ---
  apiVersion: v1
  kind: Endpoints
  metadata:
    name: ${name}
    namespace: cloudflare
  subsets:
    - addresses:
        - ip: ${service.address}
      ports:
        - name: http
          port: ${toString service.port}
'') services
