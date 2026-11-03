terraform {
  required_version = ">= 1.9.0"

  required_providers {
    tplinkeasysmart = {
      source  = "registry.terraform.io/davidlukac/tplinkeasysmart"
      version = "= 0.32.4"
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

variable "switch_name" {
  type        = string
  description = "The switch whose independent state is being managed."

  validation {
    condition     = contains(["upstairs", "downstairs"], var.switch_name)
    error_message = "Select upstairs or downstairs."
  }
}

variable "adopt_existing" {
  type        = bool
  default     = false
  description = "Import existing objects; the runner permits only no-op import plans."
}

locals {
  inventory = jsondecode(file("${path.module}/inventory.json"))
  switch    = local.inventory.switches[var.switch_name]
}

# Credentials are deliberately absent from HCL and plan variables. The provider
# reads TPLINK_EASYSMART_USERNAME/PASSWORD from the execution environment.
provider "tplinkeasysmart" {
  host = local.switch.host
  mac  = local.switch.mac
}

data "tplinkeasysmart_system_info" "switch" {}

resource "tplinkeasysmart_vlan_mode" "switch" {
  mode = "802.1q"

  lifecycle {
    prevent_destroy = true
    precondition {
      condition = (
        data.tplinkeasysmart_system_info.switch.mac_address == local.switch.mac &&
        data.tplinkeasysmart_system_info.switch.ip_address == local.switch.host &&
        data.tplinkeasysmart_system_info.switch.model == local.switch.model &&
        data.tplinkeasysmart_system_info.switch.hardware_version == local.switch.hardware &&
        data.tplinkeasysmart_system_info.switch.firmware_version == local.switch.firmware
      )
      error_message = "Switch identity or firmware differs from the adopted inventory."
    }
  }
}

resource "tplinkeasysmart_port" "ports" {
  for_each = local.switch.ports

  port             = tonumber(each.key)
  enabled          = each.value.enabled
  speed            = each.value.speed
  flow_control     = each.value.flow_control
  allow_disruptive = false

  depends_on = [tplinkeasysmart_vlan_mode.switch]
  lifecycle {
    prevent_destroy = true
  }
}

resource "tplinkeasysmart_vlan_8021q_group" "vlans" {
  for_each = local.switch.vlans

  vlan             = tonumber(each.key)
  name             = each.value.name
  tagged_ports     = each.value.tagged_ports
  untagged_ports   = each.value.untagged_ports
  allow_disruptive = false

  depends_on = [tplinkeasysmart_vlan_mode.switch]
  lifecycle {
    prevent_destroy = true
  }
}

resource "tplinkeasysmart_vlan_8021q_pvid" "pvids" {
  for_each = local.switch.ports

  port             = tonumber(each.key)
  pvid             = each.value.pvid
  allow_disruptive = false

  depends_on = [tplinkeasysmart_vlan_8021q_group.vlans]
  lifecycle {
    prevent_destroy = true
  }
}

import {
  for_each = var.adopt_existing ? { mode = "vlan_mode" } : {}
  to       = tplinkeasysmart_vlan_mode.switch
  id       = each.value
}

import {
  for_each = var.adopt_existing ? local.switch.ports : {}
  to       = tplinkeasysmart_port.ports[each.key]
  id       = each.key
}

import {
  for_each = var.adopt_existing ? local.switch.vlans : {}
  to       = tplinkeasysmart_vlan_8021q_group.vlans[each.key]
  id       = each.key
}

import {
  for_each = var.adopt_existing ? local.switch.ports : {}
  to       = tplinkeasysmart_vlan_8021q_pvid.pvids[each.key]
  id       = each.key
}

output "switch" {
  value = {
    name     = var.switch_name
    model    = data.tplinkeasysmart_system_info.switch.model
    mac      = data.tplinkeasysmart_system_info.switch.mac_address
    firmware = data.tplinkeasysmart_system_info.switch.firmware_version
    ports    = data.tplinkeasysmart_system_info.switch.port_count
    vlans    = length(tplinkeasysmart_vlan_8021q_group.vlans)
  }
}
