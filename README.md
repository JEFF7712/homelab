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
-   Remote SSH administration
-   Hardware monitoring + system optimization for 24/7 uptime

## Architecture

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
                    |  + node_exporter                   |
                    +------------------------------------+

## Services Included

### Grafana

Dashboards for CPU, RAM, disk, network, temperatures, and containers.

### Prometheus

Time-series DB scraping node_exporter.

### Glance

Lightweight status dashboard for services + system state.

### n8n

Automation engine for workflows, webhooks, and tasks.


## GitOps Workflow (CI + Automated Deployment)

This homelab uses a GitOps-style pipeline so all infrastructure changes flow through Git and deploy automatically to the server.

### Workflow Summary

1. All configs live in this repo  
2. Push to `main` triggers CI validation  
3. If CI passes, GitHub Actions SSHes into the server via tailscale  
4. The server pulls the repo and redeploys via Docker Compose  
5. The live server always matches what is in Git  

This removes all manual editing and prevents configuration drift.

### CI Validation

GitHub Actions automatically:

- Validates Docker Compose stacks with `docker compose config`
- Ensures all configs are syntactically correct before deployment

If CI fails, the Deploy workflow is never triggered.

### Automated Deployment

When validation succeeds:

- GitHub Actions connects to the server via tailscale ssh
- Executes the deploy script that redeploys all services:
```
cd ~/homelab
git pull --ff-only
./scripts/deploy.sh
```
## Tools & Technologies

-   Linux (Lubuntu)
-   Docker / Docker Compose
-   Cloudflare Tunnels + Zero Trust
-   Grafana
-   Prometheus
-   node_exporter
-   Glance
-   SSH, systemd, Bash
-   lm-sensors

## Future Improvements

-   Add more services
-   Add backups (restic/Borg)
-   Add Portainer
-   Add Blackbox Exporter
-   Host APIs or microservices
-   Get an actual PC 😂

## Resources
- [Chrultrabook](https://docs.chrultrabook.com/) tools and guides for converting Chromebooks into full Linux laptops
- [Lubuntu](https://lubuntu.me/) lightweight Ubuntu-based Linux distribution
- [Docker](https://www.docker.com/) container platform used to run services
- [Grafana](https://grafana.com/) visualization and monitoring dashboards
- [Prometheus](https://prometheus.io/) metrics collection and time-series monitoring
- [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/) secure remote access to local services
