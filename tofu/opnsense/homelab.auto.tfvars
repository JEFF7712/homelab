vlans = {
  management = {
    description = "management"
    parent      = "igb0"
    tag         = 10
  }
  clients = {
    description = "clients"
    parent      = "igb0"
    tag         = 20
  }
  infrastructure = {
    description = "infrastructure"
    parent      = "igb0"
    tag         = 30
  }
  load-balancers = {
    description = "load-balancers"
    parent      = "igb0"
    tag         = 40
  }
  guest-iot = {
    description = "guest-iot"
    parent      = "igb0"
    tag         = 50
  }
  netbird = {
    description = "netbird"
    parent      = "igb0"
    tag         = 60
  }
}

dhcpv4_subnets = {
  management = {
    description = "management"
    dns_servers = ["10.0.10.1"]
    pools       = ["10.0.10.100-10.0.10.199"]
    routers     = ["10.0.10.1"]
    subnet      = "10.0.10.0/24"
  }
  clients = {
    description = "clients"
    dns_servers = ["10.0.20.1"]
    pools       = ["10.0.20.100-10.0.20.249"]
    routers     = ["10.0.20.1"]
    subnet      = "10.0.20.0/24"
  }
  infrastructure = {
    description = "infrastructure"
    dns_servers = ["10.0.30.10", "10.0.30.1"]
    pools       = ["10.0.30.100-10.0.30.199"]
    routers     = ["10.0.30.1"]
    subnet      = "10.0.30.0/24"
  }
  guest-iot = {
    description = "guest-iot"
    dns_servers = ["10.0.50.1"]
    pools       = ["10.0.50.100-10.0.50.249"]
    routers     = ["10.0.50.1"]
    subnet      = "10.0.50.0/24"
  }
}

dhcpv4_reservations = {
  ap_downstairs = {
    description = "Downstairs NETGEAR R6400v2 access point"
    hostname    = "ap-downstairs"
    ip_address  = "10.0.20.3"
    mac_address = "80:cc:9c:21:88:e7"
    subnet_id   = "542fde27-1972-42e1-9376-057b5cae2f5e"
  }
  bambu_a1 = {
    description = "Bambu Lab A1 3D printer"
    hostname    = "bambu-a1"
    ip_address  = "10.0.20.124"
    mac_address = "28:84:85:4d:92:e8"
    subnet_id   = "542fde27-1972-42e1-9376-057b5cae2f5e"
  }
  feit_switch_01 = {
    description = "Feit Electric smart switch"
    hostname    = "feit-switch-01"
    ip_address  = "10.0.20.178"
    mac_address = "70:89:76:4d:66:e3"
    subnet_id   = "542fde27-1972-42e1-9376-057b5cae2f5e"
  }
  tuya_switch_02 = {
    description = "Tuya smart switch"
    hostname    = "tuya-switch-02"
    ip_address  = "10.0.20.170"
    mac_address = "70:89:76:5c:2d:7e"
    subnet_id   = "542fde27-1972-42e1-9376-057b5cae2f5e"
  }
  govee_floor_lamp = {
    description = "Govee H6004 floor lamp"
    hostname    = "govee-floor-lamp"
    ip_address  = "10.0.20.166"
    mac_address = "d4:13:68:01:65:91"
    subnet_id   = "542fde27-1972-42e1-9376-057b5cae2f5e"
  }
  govee_ceiling_1 = {
    description = "Govee H6004 ceiling light 1"
    hostname    = "govee-ceiling-1"
    ip_address  = "10.0.20.167"
    mac_address = "d4:13:68:01:65:ab"
    subnet_id   = "542fde27-1972-42e1-9376-057b5cae2f5e"
  }
  govee_ceiling_2 = {
    description = "Govee H6004 ceiling light 2"
    hostname    = "govee-ceiling-2"
    ip_address  = "10.0.20.168"
    mac_address = "d4:13:68:78:d5:36"
    subnet_id   = "542fde27-1972-42e1-9376-057b5cae2f5e"
  }
  govee_tulip_lamp = {
    description = "Govee H6004 tulip lamp"
    hostname    = "govee-tulip-lamp"
    ip_address  = "10.0.20.169"
    mac_address = "d4:13:68:4d:d9:d4"
    subnet_id   = "542fde27-1972-42e1-9376-057b5cae2f5e"
  }
  roku_desk_lamp = {
    description = "Roku BC1000X desk lamp (LAN control, WAN blocked)"
    hostname    = "roku-desk-lamp"
    ip_address  = "10.0.20.117"
    mac_address = "7c:67:ab:0a:83:ab"
    subnet_id   = "542fde27-1972-42e1-9376-057b5cae2f5e"
  }
  roku_floor_lamp = {
    description = "Roku BC1000X floor lamp (LAN control, WAN blocked)"
    hostname    = "roku-floor-lamp"
    ip_address  = "10.0.20.116"
    mac_address = "7c:67:ab:16:23:b7"
    subnet_id   = "542fde27-1972-42e1-9376-057b5cae2f5e"
  }
}

firewall_aliases = {
  iot_bambu_a1 = {
    name        = "iot_bambu_a1"
    type        = "host"
    content     = ["10.0.20.124"]
    description = "Bambu Lab A1 reserved LAN address"
    enabled     = true
  }
  iot_tuya_sw01 = {
    name        = "iot_tuya_sw01"
    type        = "host"
    content     = ["10.0.20.178"]
    description = "Feit smart switch reserved LAN address"
    enabled     = true
  }
  iot_tuya_sw02 = {
    name        = "iot_tuya_sw02"
    type        = "host"
    content     = ["10.0.20.170"]
    description = "Tuya smart switch reserved LAN address"
    enabled     = true
  }
  iot_bambu_ports = {
    name        = "iot_bambu_ports"
    type        = "port"
    content     = ["8883", "990", "2024", "2025", "6000"]
    description = "Bambu Lab LAN mode service ports"
    enabled     = true
  }
  roku_bulbs_lo = {
    name        = "roku_bulbs_lo"
    type        = "network"
    content     = ["10.0.20.112/29"]
    description = "Roku bulb local API segment"
    enabled     = true
  }
  roku_lan_bulbs = {
    name        = "roku_lan_bulbs"
    type        = "host"
    content     = ["10.0.20.116", "10.0.20.117"]
    description = "Roku BC1000X lamps pinned to LAN-only control"
    enabled     = true
  }
  netbird_allowed_dests = {
    name        = "netbird_allowed_dests"
    type        = "network"
    content     = ["10.0.30.0/24", "10.0.40.0/24"]
    description = "NetBird peers may reach infrastructure and load-balancer VIPs only"
    enabled     = true
  }
  govee_lan_ports = {
    name        = "govee_lan_ports"
    type        = "port"
    content     = ["4001", "4002", "4003"]
    description = "Govee LAN discovery, response, and control ports"
    enabled     = true
  }
  govee_bulbs = {
    name        = "govee_bulbs"
    type        = "host"
    content     = ["10.0.20.166", "10.0.20.167", "10.0.20.168", "10.0.20.169"]
    description = "Govee H6004 smart bulbs"
    enabled     = true
  }
  spotify_connect_ports = {
    name        = "spotify_connect_ports"
    type        = "port"
    content     = ["38801", "38802", "38803", "38804", "38805", "38806", "38807", "38808", "38809", "38810", "38901", "38902", "38903", "38904", "38905", "38906", "38907", "38908", "38909", "38910"]
    description = "Spotify Connect zeroconf ports for Music Assistant players"
    enabled     = true
  }
}

firewall_filters = {
  management-allow-any = {
    description = "Allow management VLAN to all destinations"
    enabled     = true
    sequence    = 100
    interface   = { interface = ["opt2"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "any"
      quick       = true
      log         = false
      source      = { net = "10.0.10.0/24", port = "" }
      destination = { net = "any", port = "" }
    }
  }
  clients-allow-dns = {
    description = "Allow clients to OPNsense DNS"
    enabled     = true
    sequence    = 200
    interface   = { interface = ["opt1"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "TCP/UDP"
      quick       = true
      log         = false
      source      = { net = "10.0.20.0/24", port = "" }
      destination = { net = "10.0.20.1", port = "53" }
    }
  }
  clients-allow-load-balancers = {
    description = "Allow clients to Kubernetes services"
    enabled     = true
    sequence    = 210
    interface   = { interface = ["opt1"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "any"
      quick       = true
      log         = false
      source      = { net = "10.0.20.0/24", port = "" }
      destination = { net = "10.0.40.0/24", port = "" }
    }
  }
  clients-allow-govee-responses = {
    description = "Allow Govee LAN responses to Home Assistant"
    enabled     = true
    sequence    = 215
    interface   = { interface = ["opt1"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "UDP"
      quick       = true
      log         = false
      source      = { net = "govee_bulbs", port = "" }
      destination = { net = "10.0.30.0/24", port = "4002" }
    }
  }
  clients-allow-spotify-connect = {
    description = "Allow clients to Music Assistant Spotify Connect players"
    enabled     = true
    sequence    = 216
    interface   = { interface = ["opt1"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "TCP"
      quick       = true
      log         = false
      source      = { net = "10.0.20.0/24", port = "" }
      destination = { net = "10.0.30.15", port = "spotify_connect_ports" }
    }
  }
  clients-allow-music-assistant = {
    description = "Allow clients to Music Assistant party view"
    enabled     = true
    sequence    = 217
    interface   = { interface = ["opt1"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "TCP"
      quick       = true
      log         = false
      source      = { net = "10.0.20.0/24", port = "" }
      destination = { net = "10.0.30.15", port = "8095" }
    }
  }
  clients-allow-ledfx = {
    description = "Allow clients to LedFx web interface"
    enabled     = true
    sequence    = 218
    interface   = { interface = ["opt1"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "TCP"
      quick       = true
      log         = false
      source      = { net = "10.0.20.0/24", port = "" }
      destination = { net = "10.0.30.15", port = "8888" }
    }
  }
  clients-allow-mdns = {
    description = "Allow client mDNS queries to the relay"
    enabled     = true
    sequence    = 219
    interface   = { interface = ["opt1"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "UDP"
      quick       = true
      log         = false
      source      = { net = "10.0.20.0/24", port = "5353" }
      destination = { net = "224.0.0.251/32", port = "5353" }
    }
  }
  clients-block-private = {
    description = "Block clients from other private VLANs"
    enabled     = true
    sequence    = 220
    interface   = { interface = ["opt1"] }
    filter = {
      action      = "block"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "any"
      quick       = true
      log         = true
      source      = { net = "10.0.20.0/24", port = "" }
      destination = { net = "10.0.0.0/8", port = "" }
    }
  }
  clients-allow-internet = {
    description = "Allow clients to the Internet"
    enabled     = true
    sequence    = 230
    interface   = { interface = ["opt1"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "any"
      quick       = true
      log         = false
      source      = { net = "10.0.20.0/24", port = "" }
      destination = { net = "any", port = "" }
    }
  }
  clients-block-roku-bulbs-internet = {
    description = "Block LAN-only Roku lamps from the Internet (freeze firmware)"
    enabled     = true
    sequence    = 225
    interface   = { interface = ["opt1"] }
    filter = {
      action      = "block"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "any"
      quick       = true
      log         = true
      source      = { net = "roku_lan_bulbs", port = "" }
      destination = { net = "any", port = "" }
    }
  }
  infrastructure-allow-dns = {
    description = "Allow infrastructure to OPNsense DNS"
    enabled     = true
    sequence    = 300
    interface   = { interface = ["opt3"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "TCP/UDP"
      quick       = true
      log         = false
      source      = { net = "10.0.30.0/24", port = "" }
      destination = { net = "10.0.30.1", port = "53" }
    }
  }
  infrastructure-allow-opnsense-api = {
    description = "Allow the private runner to reach the OPNsense API"
    enabled     = true
    sequence    = 305
    interface   = { interface = ["opt3"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "TCP"
      quick       = true
      log         = false
      source      = { net = "10.0.30.0/24", port = "" }
      destination = { net = "10.0.10.1", port = "443" }
    }
  }
  infrastructure-allow-load-balancers = {
    description = "Allow infrastructure to Kubernetes service VIPs"
    enabled     = true
    sequence    = 310
    interface   = { interface = ["opt3"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "any"
      quick       = true
      log         = false
      source      = { net = "10.0.30.0/24", port = "" }
      destination = { net = "10.0.40.0/24", port = "" }
    }
  }
  infrastructure-allow-bgp = {
    description = "Allow infrastructure BGP to OPNsense"
    enabled     = true
    sequence    = 311
    interface   = { interface = ["opt3"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "TCP"
      quick       = true
      log         = true
      source      = { net = "10.0.30.0/24", port = "" }
      destination = { net = "10.0.30.1", port = "179" }
    }
  }
  infrastructure-allow-roku-bulbs = {
    description = "Allow roku-bridge to Roku bulbs local API"
    enabled     = true
    sequence    = 314
    interface   = { interface = ["opt3"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "TCP"
      quick       = true
      log         = true
      source      = { net = "10.0.30.10/32", port = "" }
      destination = { net = "roku_bulbs_lo", port = "88" }
    }
  }
  infrastructure-allow-mdns-relay = {
    description = "Allow relayed client mDNS queries into infrastructure VLAN"
    enabled     = true
    sequence    = 309
    interface   = { interface = ["opt3"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "UDP"
      quick       = true
      log         = false
      source      = { net = "10.0.20.0/24", port = "5353" }
      destination = { net = "224.0.0.251/32", port = "5353" }
    }
  }
  infrastructure-allow-govee-queries = {
    description = "Allow Home Assistant Govee LAN queries"
    enabled     = true
    sequence    = 312
    interface   = { interface = ["opt3"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "UDP"
      quick       = true
      log         = false
      source      = { net = "10.0.30.0/24", port = "" }
      destination = { net = "10.0.20.0/24", port = "govee_lan_ports" }
    }
  }
  infrastructure-allow-govee-multicast = {
    description = "Allow Home Assistant Govee multicast discovery"
    enabled     = true
    sequence    = 313
    interface   = { interface = ["opt3"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "UDP"
      quick       = true
      log         = false
      source      = { net = "10.0.30.0/24", port = "" }
      destination = { net = "239.255.255.250/32", port = "4001" }
    }
  }
  infrastructure-allow-matter-bulbs = {
    description = "Allow Home Assistant Matter traffic to clients VLAN"
    enabled     = true
    sequence    = 318
    interface   = { interface = ["opt3"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet6"
      protocol    = "TCP/UDP"
      quick       = true
      log         = true
      source      = { net = "fd42:30::/64", port = "" }
      destination = { net = "fd42:20::/64", port = "" }
    }
  }
  infrastructure-allow-bambu-lan = {
    description = "Allow infrastructure to Bambu Lab A1 LAN mode services"
    enabled     = true
    sequence    = 315
    interface   = { interface = ["opt3"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "TCP"
      quick       = true
      log         = false
      source      = { net = "10.0.30.0/24", port = "" }
      destination = { net = "iot_bambu_a1", port = "iot_bambu_ports" }
    }
  }
  infrastructure-allow-tuya-local = {
    description = "Allow infrastructure to Feit switch Tuya LAN protocol"
    enabled     = true
    sequence    = 316
    interface   = { interface = ["opt3"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "TCP"
      quick       = true
      log         = false
      source      = { net = "10.0.30.0/24", port = "" }
      destination = { net = "iot_tuya_sw01", port = "6668" }
    }
  }
  infrastructure-allow-tuya-local-sw02 = {
    description = "Allow infrastructure to second Tuya switch LAN protocol"
    enabled     = true
    sequence    = 317
    interface   = { interface = ["opt3"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "TCP"
      quick       = true
      log         = false
      source      = { net = "10.0.30.0/24", port = "" }
      destination = { net = "iot_tuya_sw02", port = "6668" }
    }
  }
  infrastructure-block-private = {
    description = "Block infrastructure from initiating to other private VLANs"
    enabled     = true
    sequence    = 319
    interface   = { interface = ["opt3"] }
    filter = {
      action      = "block"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "any"
      quick       = true
      log         = true
      source      = { net = "10.0.30.0/24", port = "" }
      destination = { net = "10.0.0.0/8", port = "" }
    }
  }
  infrastructure-allow-internet = {
    description = "Allow infrastructure to the Internet"
    enabled     = true
    sequence    = 330
    interface   = { interface = ["opt3"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "any"
      quick       = true
      log         = false
      source      = { net = "10.0.30.0/24", port = "" }
      destination = { net = "any", port = "" }
    }
  }
  load-balancers-allow-any = {
    description = "Allow Kubernetes service VIP traffic"
    enabled     = true
    sequence    = 400
    interface   = { interface = ["opt4"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "any"
      quick       = true
      log         = false
      source      = { net = "10.0.40.0/24", port = "" }
      destination = { net = "any", port = "" }
    }
  }
  guest-iot-allow-dns = {
    description = "Allow guest and IoT devices to OPNsense DNS"
    enabled     = true
    sequence    = 500
    interface   = { interface = ["opt5"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "TCP/UDP"
      quick       = true
      log         = false
      source      = { net = "10.0.50.0/24", port = "" }
      destination = { net = "10.0.50.1", port = "53" }
    }
  }
  guest-iot-allow-music-assistant = {
    description = "Allow guest and IoT devices to Music Assistant party view"
    enabled     = true
    sequence    = 505
    interface   = { interface = ["opt5"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "TCP"
      quick       = true
      log         = false
      source      = { net = "10.0.50.0/24", port = "" }
      destination = { net = "10.0.30.15", port = "8095" }
    }
  }
  guest-iot-allow-ledfx = {
    description = "Allow guest and IoT devices to LedFx web interface"
    enabled     = true
    sequence    = 506
    interface   = { interface = ["opt5"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "TCP"
      quick       = true
      log         = false
      source      = { net = "10.0.50.0/24", port = "" }
      destination = { net = "10.0.30.15", port = "8888" }
    }
  }
  guest-iot-block-private = {
    description = "Block guest and IoT devices from private VLANs"
    enabled     = true
    sequence    = 510
    interface   = { interface = ["opt5"] }
    filter = {
      action      = "block"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "any"
      quick       = true
      log         = true
      source      = { net = "10.0.50.0/24", port = "" }
      destination = { net = "10.0.0.0/8", port = "" }
    }
  }
  guest-iot-allow-internet = {
    description = "Allow guest and IoT devices to the Internet"
    enabled     = true
    sequence    = 520
    interface   = { interface = ["opt5"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "any"
      quick       = true
      log         = false
      source      = { net = "10.0.50.0/24", port = "" }
      destination = { net = "any", port = "" }
    }
  }
  netbird-allow-infrastructure = {
    description = "Allow NetBird peers to infrastructure and LB VIPs"
    enabled     = true
    sequence    = 600
    interface   = { interface = ["opt6"] }
    filter = {
      action      = "pass"
      direction   = "in"
      ip_protocol = "inet"
      protocol    = "any"
      quick       = true
      log         = true
      source      = { net = "10.0.60.0/24", port = "" }
      destination = { net = "netbird_allowed_dests", port = "" }
    }
  }
}

firewall_nat_port_forwards = {
  wan-monero-p2p = {
    description    = "WAN Monero P2P to homelab 04"
    enabled        = true
    sequence       = 100
    interface      = ["wan"]
    ip_protocol    = "inet"
    protocol       = "tcp"
    log            = false
    nat_reflection = "default"
    source         = { net = "any", port = "" }
    destination    = { net = "wanip", port = "18080" }
    target         = { ip = "10.0.30.14", port = "18080" }
  }
}
