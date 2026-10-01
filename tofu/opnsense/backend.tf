terraform {
  backend "s3" {
    bucket                      = "homelab-tofu-state"
    key                         = "opnsense/terraform.tfstate"
    region                      = "homelab"
    endpoint                    = "http://s3.internal:3900"
    skip_credentials_validation = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true
    use_path_style              = true
  }
}
