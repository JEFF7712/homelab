terraform {
  required_version = ">= 1.9.0"
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.28.0"
    }
  }
}

# Separate Access-only credentials; never reuse the tunnel token.
provider "cloudflare" {}
