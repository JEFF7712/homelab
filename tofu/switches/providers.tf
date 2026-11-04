terraform {
  required_version = ">= 1.9.0"

  required_providers {
    tplinkeasysmart = {
      source  = "registry.terraform.io/davidlukac/tplinkeasysmart"
      version = "0.32.7"
    }
  }

  # The runner supplies the key through TF_ENCRYPTION. Missing keys fail closed.
  encryption {
    state {
      enforced = true
    }
    plan {
      enforced = true
    }
  }
}
