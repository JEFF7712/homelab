{ config, ... }:
{
  networks = {
    management = {
      description = "management";
      parent = "igb0";
      tag = 10;
      interfaceName = "management";
      gateway = "10.0.10.1";
      cidr = "10.0.10.0/24";
      dhcp = {
        dns_servers = [ config.networks.management.gateway ];
        pools = [ "10.0.10.100-10.0.10.199" ];
        subnetId = "b0644f73-95b1-478b-8485-4fbfaa293116";
      };
    };
    clients = {
      description = "clients";
      parent = "igb0";
      tag = 20;
      interfaceName = "clients";
      gateway = "10.0.20.1";
      cidr = "10.0.20.0/24";
      dhcp = {
        dns_servers = [ config.networks.clients.gateway ];
        pools = [ "10.0.20.100-10.0.20.249" ];
        subnetId = "542fde27-1972-42e1-9376-057b5cae2f5e";
      };
    };
    infrastructure = {
      description = "infrastructure";
      parent = "igb0";
      tag = 30;
      interfaceName = "infrastructure";
      gateway = "10.0.30.1";
      cidr = "10.0.30.0/24";
      dhcp = {
        dns_servers = [
          config.hosts.adguard-netbird-01.address
          config.networks.infrastructure.gateway
        ];
        pools = [ "10.0.30.100-10.0.30.199" ];
        subnetId = "c0848ac2-1f08-4326-bd04-2d36d61ba722";
      };
    };
    load-balancers = {
      description = "load-balancers";
      parent = "igb0";
      tag = 40;
      interfaceName = "load_balancers";
      gateway = "10.0.40.1";
      cidr = "10.0.40.0/24";
    };
    guest-iot = {
      description = "guest-iot";
      parent = "igb0";
      tag = 50;
      interfaceName = "guest_iot";
      gateway = "10.0.50.1";
      cidr = "10.0.50.0/24";
      dhcp = {
        dns_servers = [ config.networks.guest-iot.gateway ];
        pools = [ "10.0.50.100-10.0.50.249" ];
        subnetId = "ed37170d-230a-4d70-81af-8a5b06077962";
      };
    };
    netbird = {
      description = "netbird";
      parent = "igb0";
      tag = 60;
      interfaceName = "netbird";
      gateway = "10.0.60.1";
      cidr = "10.0.60.0/24";
    };
  };
}
