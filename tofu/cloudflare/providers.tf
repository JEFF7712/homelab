terraform {
  required_version = ">= 1.9.0"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.28.0"
    }
  }
}

# The API token is read from CLOUDFLARE_API_TOKEN so it never lands in HCL or
# state. Plan needs Tunnel Read, Zone Read and Zone WAF Read; apply needs
# Tunnel Edit, Zone Read and Zone WAF Edit. Scope the zone permissions to
# rupan.dev. DNS and Access are deliberately not granted here.
provider "cloudflare" {}
