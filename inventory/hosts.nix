{
  bootstrapHost = "homelab-01";
  hosts = {
    adguard-netbird-01 = {
      deploymentType = "appliance-dns";
      deploymentOrder = 6;
      address = "10.0.30.10";
      network = "infrastructure";
    };
    homelab-01 = {
      deploymentType = "k3s-stateful";
      deploymentOrder = 3;
      address = "10.0.30.11";
      network = "infrastructure";
      k3s = true;
    };
    homelab-02 = {
      deploymentType = "k3s-worker";
      deploymentOrder = 1;
      address = "10.0.30.12";
      network = "infrastructure";
      k3s = true;
    };
    homelab-03 = {
      deploymentType = "k3s-worker";
      deploymentOrder = 2;
      address = "10.0.30.13";
      network = "infrastructure";
      k3s = true;
    };
    homelab-04 = {
      deploymentType = "k3s-worker";
      deploymentOrder = 4;
      address = "10.0.30.14";
      network = "infrastructure";
      k3s = true;
    };
    homelab-05 = {
      deploymentType = "k3s-worker";
      deploymentOrder = 5;
      address = "10.0.30.15";
      network = "infrastructure";
      k3s = true;
    };
    nas-01 = {
      deploymentType = "appliance-nas";
      deploymentOrder = 7;
      address = "10.0.30.20";
      network = "infrastructure";
    };
  };
}
