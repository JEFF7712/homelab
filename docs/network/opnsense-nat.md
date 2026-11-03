# OPNsense Outbound NAT Mode

OPNsense's outbound NAT has four modes: Automatic, Hybrid, Manual, Disabled.

The `reconcile_outbound_nat` function in `opnsense_reconciler/reconcile.py` manages Source NAT rules declaratively. It assumes rules can be created via the API, which only works when the mode is **Hybrid** (manual rules alongside auto-generated ones) or **Manual** (rules you fully own).

## Setup

After first booting OPNsense, or any time you wipe its config:

1. Open the OPNsense web UI on the management VLAN.
2. Navigate to **Firewall > NAT > Outbound**.
3. Change the **Mode** dropdown to **Hybrid Outbound NAT rule generation (automatically added rules with ability to add manual)**.
4. Click **Save**, then **Apply changes** in the top-right banner.

This is a one-time setup. The reconciler does not change this setting (the API has no surface for it).

## Why it matters

- **Automatic**: OPNsense creates rules for every internal subnet automatically. Adding manual rules through the API returns `HTTP 500` because the controller refuses to mix manual and automatic rules.
- **Hybrid**: OPNsense keeps auto-generating rules for every subnet it discovered before the mode change, but accepts manual rules added through the API or UI. The reconciler's `add_rule`/`set_rule` calls work.
- **Manual**: No auto rules; you must declare every subnet that needs internet. The reconciler's rules work, but every subnet needs an entry in `opnsense_reconciler/outbound-nat-rules.json`.

## Reconciler behavior

The reconciler filters out rules whose description starts with `Auto created rule` (auto-generated entries from Hybrid mode) so it does not refuse to apply when they coexist with manual rules.

## Verifying

After a CI apply, check **Firewall > NAT > Outbound** in the UI. You should see:

- One or more rules under **Automatically generated rules** (the pre-existing auto rules for clients/infrastructure/etc).
- The rules declared in `opnsense_reconciler/outbound-nat-rules.json` under the manual section, with the same `description` strings as the JSON.

If declared rules are absent, inspect the mode, CI apply result, and reconciler errors. An empty section alone does not identify the cause.
