{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homelab.k3s;
in
{
  options.homelab.k3s = {
    enable = lib.mkEnableOption "the homelab k3s server role";

    primaryInterface = lib.mkOption {
      type = lib.types.str;
      description = "Physical interface attached to infrastructure VLAN 30.";
    };

    role = lib.mkOption {
      type = lib.types.enum [
        "server"
        "agent"
      ];
      default = "server";
      description = "Run k3s in server or agent (worker) mode.";
    };

    nodeIp = lib.mkOption {
      type = lib.types.str;
      description = "Stable VLAN 30 address used by this k3s server.";
    };

    clusterInit = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Initialize the embedded-etcd k3s cluster on this server.";
    };

    serverAddress = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Existing k3s API endpoint for joining this server.";
    };

    tokenFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Runtime-provisioned file containing the k3s cluster token.";
    };

    bootstrapCilium = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Bootstrap the pinned Cilium CNI through the k3s Helm controller.";
    };

    registry = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "Load the node's private registry configuration from a runtime secret file.";
      };

      configFile = lib.mkOption {
        type = lib.types.str;
        default = "/persist/secrets/k3s-registries.yaml";
        description = "Runtime-provisioned k3s registries.yaml containing node pull credentials.";
      };

      enforceLocalImages = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "Disable fallback to configured upstream registry endpoints after local content is populated.";
      };

      embeddedRegistry = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "Enable k3s embedded distributed registry (Spegel) peer-to-peer image caching on servers.";
      };
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.clusterInit || (cfg.serverAddress != null && cfg.tokenFile != null);
        message = "Joining k3s servers require serverAddress and tokenFile.";
      }
      {
        assertion = !(cfg.clusterInit && cfg.serverAddress != null);
        message = "The k3s cluster-init server must not join another server.";
      }
      {
        assertion = !cfg.bootstrapCilium || (cfg.role == "server" && cfg.clusterInit);
        message = "Cilium bootstrap must run only on the cluster-init server.";
      }
      {
        assertion = !(cfg.role == "agent" && cfg.clusterInit);
        message = "An agent node cannot initialize a cluster.";
      }
      {
        assertion = !cfg.registry.enforceLocalImages || cfg.registry.enable;
        message = "Local-only image enforcement requires the private registry configuration.";
      }
    ];

    boot = {
      loader = {
        efi.canTouchEfiVariables = true;
        systemd-boot.enable = true;
      };
    };

    networking = {
      useDHCP = false;
      nftables = {
        tables = lib.mkIf (cfg.role == "agent") {
          k3s-agent-proxy = {
            family = "ip";
            content = ''
              chain output {
                type nat hook output priority -100; policy accept;
                ip daddr 127.0.0.1 tcp dport 6443 redirect to :6444
              }
            '';
          };
        };
      };
      firewall = {
        enable = true;
        extraReversePathFilterRules = "ip saddr 10.42.0.0/16 accept";
        extraInputRules = ''
          ip saddr 10.0.10.0/24 tcp dport 22 accept
          ip saddr 10.0.10.0/24 tcp dport 6443 accept
          ip saddr 10.0.30.0/24 tcp dport { 22, 179, 2379, 2380, 5001, 6443, 6444, 9100, 10250, 4240 } accept
          ip saddr 10.0.30.0/24 udp dport 8472 accept
          ip saddr 10.42.0.0/16 tcp dport { 6443, 9100, 10250 } accept
          ip saddr { 10.0.20.0/24, 10.0.30.0/24, 10.0.40.0/24, 10.42.0.0/16 } tcp dport { 8123, 21063 } accept
          ip saddr { 10.0.10.0/24, 10.0.20.0/24, 10.0.30.0/24, 10.0.40.0/24, 10.0.50.0/24, 10.42.0.0/16 } tcp dport 8888 accept
          ip saddr 10.0.30.0/24 udp dport 5353 accept
          ip saddr 10.0.20.0/24 udp dport 4002 accept
          ip6 saddr { fd42:20::/64, fd42:30::/64 } udp dport { 5353, 5540 } accept
        '';
      };
    };

    systemd.network = {
      enable = true;
      networks."30-infrastructure" = {
        matchConfig.Name = cfg.primaryInterface;
        networkConfig = {
          Address = "${cfg.nodeIp}/24";
          DNS = "10.0.30.10";
          Gateway = "10.0.30.1";
          IPv6AcceptRA = true;
        };
        linkConfig.RequiredForOnline = "routable";
      };
    };

    services.k3s = {
      enable = true;
      role = cfg.role;
      clusterInit = if cfg.role == "server" then cfg.clusterInit else false;
      inherit (cfg) tokenFile;
      serverAddr = lib.mkIf (cfg.serverAddress != null) cfg.serverAddress;
      extraFlags =
        (
          if cfg.role == "server" then
            [
              "--node-ip=${cfg.nodeIp}"
              "--advertise-address=${cfg.nodeIp}"
              "--flannel-backend=none"
              "--disable-network-policy"
              "--cluster-cidr=10.42.0.0/16"
              "--service-cidr=10.43.0.0/16"
              "--disable-kube-proxy"
              "--disable=servicelb"
              "--disable=traefik"
              "--disable=local-storage"
            ]
          else
            [
              "--node-ip=${cfg.nodeIp}"
            ]
        )
        ++ [
          "--kubelet-arg=container-log-max-size=10Mi"
          "--kubelet-arg=container-log-max-files=5"
          "--kubelet-arg=image-gc-high-threshold=80"
          "--kubelet-arg=image-gc-low-threshold=70"
          "--kubelet-arg=eviction-hard=memory.available<500Mi,nodefs.available<10%,imagefs.available<10%"
        ]
        ++ lib.optionals (cfg.role == "server") [
          "--etcd-snapshot-dir=/persist/etcd-snapshots"
          "--etcd-snapshot-retention=14"
          "--etcd-snapshot-schedule-cron=0 3 * * *"
        ]
        ++ lib.optional cfg.registry.enforceLocalImages "--disable-default-registry-endpoint"
        ++ lib.optional (
          cfg.role == "server" && cfg.registry.enable && cfg.registry.embeddedRegistry
        ) "--embedded-registry";
    };

    systemd.services.k3s = {
      unitConfig.ConditionPathExists =
        lib.optional (!cfg.clusterInit) cfg.tokenFile
        ++ lib.optional cfg.registry.enable cfg.registry.configFile;
      serviceConfig.LoadCredential = lib.mkIf cfg.registry.enable "registries.yaml:${cfg.registry.configFile}";
      preStart = lib.mkIf cfg.registry.enable ''
        install -d -m 0700 /etc/rancher/k3s
        install -m 0600 "$CREDENTIALS_DIRECTORY/registries.yaml" /etc/rancher/k3s/registries.yaml
      '';
    };

    services.k3s.manifests = lib.mkIf (cfg.role == "server" && cfg.bootstrapCilium) {
      cilium.content = {
        apiVersion = "helm.cattle.io/v1";
        kind = "HelmChart";
        metadata = {
          name = "cilium";
          namespace = "kube-system";
        };
        spec = {
          bootstrap = true;
          chart = "cilium";
          createNamespace = false;
          repo = "https://helm.cilium.io";
          targetNamespace = "kube-system";
          version = "1.20.1";
          valuesContent = builtins.toJSON (
            {
              bgpControlPlane.enabled = true;
              gatewayAPI.enabled = true;
              gatewayAPI.gatewayClass.create = false;
              ingressController.enabled = true;
              ipam.operator.clusterPoolIPv4PodCIDRList = [ "10.42.0.0/16" ];
              ipv4NativeRoutingCIDR = "10.42.0.0/16";
              k8sServiceHost = "127.0.0.1";
              k8sServicePort = 6443;
              kubeProxyReplacement = true;
              operator.replicas = 2;
              securityContext.capabilities.ciliumAgent = [
                "CHOWN"
                "KILL"
                "NET_ADMIN"
                "NET_RAW"
                "IPC_LOCK"
                "SYS_MODULE"
                "SYS_ADMIN"
                "SYS_RESOURCE"
                "DAC_OVERRIDE"
                "FOWNER"
                "SETGID"
                "SETUID"
                "SYSLOG"
                "NET_BIND_SERVICE"
              ];
            }
            // lib.optionalAttrs cfg.registry.enable {
              image.override = "registry.rupan.dev/upstream/quay.io/cilium/cilium@sha256:ae9ea21f7427fe24bc6ea7247eb552157a1b0a431744045d3f641545ca71d11b";
              envoy.image.override = "registry.rupan.dev/upstream/quay.io/cilium/cilium-envoy@sha256:75b8094c7127736a2ffd2dce3945e0931cb6df21b0372ff661940eca26730b91";
              operator.image.override = "registry.rupan.dev/upstream/quay.io/cilium/operator-generic@sha256:6c3885fc7b629099fdbe2a5c87869c86feb825fa18fae299eac0f61918d16ecf";
            }
          );
        };
      };
      bgp-peer.content = {
        apiVersion = "cilium.io/v2alpha1";
        kind = "CiliumBGPPeerConfig";
        metadata.name = "homelab-peer-config";
        spec.families = [
          {
            afi = "ipv4";
            safi = "unicast";
            advertisements.matchLabels.advertise = "bgp";
          }
        ];
      };
      bgp-cluster.content = {
        apiVersion = "cilium.io/v2alpha1";
        kind = "CiliumBGPClusterConfig";
        metadata.name = "homelab-opnsense";
        spec = {
          nodeSelector.matchExpressions = [
            {
              key = "node-role.kubernetes.io/control-plane";
              operator = "In";
              values = [ "true" ];
            }
          ];
          bgpInstances = [
            {
              name = "homelab";
              localASN = 64512;
              localPort = 179;
              peers = [
                {
                  name = "opnsense-vlan30";
                  peerAddress = "10.0.30.1";
                  peerASN = 64513;
                  peerConfigRef.name = "homelab-peer-config";
                }
              ];
            }
          ];
        };
      };
      bgp-advertisement.content = {
        apiVersion = "cilium.io/v2alpha1";
        kind = "CiliumBGPAdvertisement";
        metadata = {
          name = "homelab-lb";
          labels.advertise = "bgp";
        };
        spec.advertisements = [
          {
            advertisementType = "Service";
            service.addresses = [ "LoadBalancerIP" ];
            selector.matchLabels.bgp-advertise = "true";
          }
        ];
      };
      lb-pool.content = {
        apiVersion = "cilium.io/v2alpha1";
        kind = "CiliumLoadBalancerIPPool";
        metadata.name = "vlan40-pool";
        spec = {
          blocks = [
            {
              start = "10.0.40.10";
              stop = "10.0.40.19";
            }
          ];
        };
      };
      bgp-canary.content = {
        apiVersion = "v1";
        kind = "List";
        items = [
          {
            apiVersion = "apps/v1";
            kind = "Deployment";
            metadata = {
              name = "bgp-canary";
              namespace = "default";
            };
            spec = {
              replicas = 1;
              selector.matchLabels.app = "bgp-canary";
              template = {
                metadata.labels.app = "bgp-canary";
                spec.containers = [
                  {
                    name = "web";
                    image =
                      if cfg.registry.enable then
                        "registry.rupan.dev/upstream/docker.io/library/nginx@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10"
                      else
                        "docker.io/library/nginx:1.27-alpine";
                    ports = [ { containerPort = 80; } ];
                  }
                ];
              };
            };
          }
          {
            apiVersion = "v1";
            kind = "Service";
            metadata = {
              name = "bgp-canary";
              namespace = "default";
              labels.bgp-advertise = "true";
            };
            spec = {
              type = "LoadBalancer";
              loadBalancerClass = "io.cilium/bgp-control-plane";
              selector.app = "bgp-canary";
              ports = [ { port = 80; } ];
            };
          }
        ];
      };
    };

    systemd.tmpfiles.rules = [
      "d /persist/etc/ssh 0700 root root -"
      "d /persist/secrets 0700 root root -"
      "d /persist/etcd-snapshots 0700 root root -"
    ];

    environment.persistence."/persist" = {
      hideMounts = true;
      directories = [
        "/etc/rancher/node"
        "/var/lib/nixos"
        "/var/lib/rancher/k3s"
        "/var/lib/systemd"
      ];
      files = [ "/etc/machine-id" ];
    };

    environment.systemPackages = with pkgs; [
      btrfs-progs
      curl
      ethtool
      kubectl
      nfs-utils
      tcpdump
    ];
  };
}
