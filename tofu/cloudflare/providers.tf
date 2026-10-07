terraform {
  required_version = ">= 1.9.0"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.27.0"
    }
  }
}

# The API token is read from CLOUDFLARE_API_TOKEN so it never lands in HCL or
# state. Scope it to Account > Cloudflare Tunnel > Edit for this stack; DNS and
# Access are deliberately not granted here.
provider "cloudflare" {}
