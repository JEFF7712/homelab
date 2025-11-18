# Chromebook Homelab Server
Repurposed an old Chromebook into a reliable 24/7 Linux server running
Dockerized services, monitoring dashboards, and secure Cloudflare Zero
Trust tunnels. This project demonstrates lightweight DevOps, Linux
administration, service orchestration, and production-style
observability on minimal hardware.

![Demo](./assets/homelab.gif)

## Overview

This project converts an old and unused Chromebook into a fully functional
homelab server. After wiping ChromeOS and installing a lightweight Linux
distribution, the device now hosts a set of containerized services
accessible through secure Cloudflare Tunnels and monitored through a
full observability stack.

The machine runs continuously, exposes multiple custom subdomains, and
provides real-time insight into its own CPU, RAM, disk, temperature,
network throughput, workloads, and uptime.

## Key Features

-   ChromeOS wiped and replaced with Lubuntu
-   Docker-based orchestration for Grafana, Glance, Pi-hole, and n8n
-   Cloudflare Tunnels with Zero Trust authentication
-   Real-time system metrics dashboards: CPU, memory, disk I/O, network, temperature
-   Container-level analytics
-   GitOps automated deployments (CI + deploy pipeline)
-   SSH automation through **Tailscale** (no port forwarding, private network)  
-   Remote SSH administration
-   Hardware monitoring + system optimization for 24/7 uptime

## Architecture

```
Developer → Git Push → CI Workflow → Deploy Workflow → Homelab (via Tailscale)
```

```
                      +------------------------------+
                      |     Cloudflare Zero Trust    |
                      |  Auth + Secure Tunnel Proxy  |
                      +---------------+--------------+
                                      |
                                 *.rupan.dev
                                      |
                              Cloudflare Tunnel
                                      |
                    +-----------------+------------------+
                    |        Lubuntu Chromebook Server   |
                    |                                    |
                    |  + Docker / Docker Compose         |
                    |  + Prometheus (metrics DB)         |
                    |  + Grafana (dashboards)            |
                    |  + Glance (status dashboard)       |
                    |  + n8n (automation workflows)      |
                    |  + node_exporter / cAdvisor        |
                    |                                    |
                    |  Tailscale SSH (GitHub → Server)   |
                    +------------------------------------+
```

---

## Services Included

### Grafana

Dashboards for CPU, RAM, disk, network, temperatures, and containers.

### Prometheus

Time-series DB scraping node_exporter.

### Glance

Lightweight status dashboard for services + system state.

### n8n

Automation engine for workflows, webhooks, and tasks.

### Pi-hole

Network-wide ad blocking. (I am only using per device though as I was unable to access router DHCP options)

---

## GitOps Workflow (CI + Automated Deployment)

This homelab uses a GitOps-style pipeline so all infrastructure changes flow through Git and deploy automatically to the server.

The server itself is never edited directly.  
All changes occur through Git commits.

### How It Works

1. All Docker and service configs live in the repo  
2. Every push triggers the **CI workflow (`ci.yml`)**  
3. CI validates main Compose file
4. If CI succeeds, the **Deploy workflow (`deploy.yml`)** runs  
5. GitHub Actions brings up a temporary Tailscale node  
6. The workflow SSHes into the homelab (over Tailscale) and runs:

```
cd ~/homelab
git pull --ff-only
./scripts/deploy.sh
```

### Why This Matters

- Automated, repeatable deployments  
- No manual edits on the server  
- Zero exposed ports  
- Private, encrypted SSH via Tailscale  
- Eliminates configuration drift  

This provides a clean, reliable GitOps process suitable for homelab infrastructure.

---

## Tools & Technologies

-   Linux (Lubuntu)
-   Docker / Docker Compose
-   Cloudflare Tunnels + Zero Trust
-   Tailscale SSH
-   Grafana
-   Prometheus
-   node_exporter
-   cAdvisor  
-   Glance
-   SSH, systemd, Bash
-   GitHub Actions (CI + Deploy workflows)  

---

## Future Improvements

-   Add more services
-   Add automated backups
-   Add Portainer
-   Add Blackbox Exporter
-   Get an actual PC 😂

---

## Resources
- [Chrultrabook](https://docs.chrultrabook.com/) tools and guides for converting Chromebooks into full Linux laptops
- [Lubuntu](https://lubuntu.me/) lightweight Ubuntu-based Linux distribution
- [Docker](https://www.docker.com/) container platform used to run services
- [Tailscale](https://tailscale.com/) private, secure SSH access
- [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/) secure remote access to local services
- [Grafana](https://grafana.com/) visualization and monitoring dashboards
- [Prometheus](https://prometheus.io/) metrics collection and time-series monitoring
- [Pi-hole](https://pi-hole.net/) network-wide ad blocking


