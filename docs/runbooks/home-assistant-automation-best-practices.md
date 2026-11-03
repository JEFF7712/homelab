# Home Assistant Automation Best Practices

Local conventions for `home-assistant/`. For runtime behavior, consult the
[official trigger reference](https://www.home-assistant.io/docs/automation/trigger/),
[run modes](https://www.home-assistant.io/docs/automation/modes/), and
[script actions](https://www.home-assistant.io/docs/scripts/).
The [2026.7 release notes](https://www.home-assistant.io/blog/2026/07/01/release-20267/)
record the purpose-specific trigger renames. Confirm feature availability against
the deployed HA version before introducing new trigger types.

Related: `docs/runbooks/home-assistant-configuration.md` (ownership, Workflow A source-first, Workflow B UI adoption, locking, secrets boundary). Read that first before touching `home-assistant/`. This doc covers how to write good automations, not how to deploy them.

## 1. Mental model

Trigger is a moment, condition is a fact, action is the result.

- Trigger: an event that wakes the automation (state change, sun event, time, zone enter/leave, event). One automation can have many triggers; any one firing starts a run.
- Condition: a gate checked at fire time using current state. No trace at all means the trigger never fired; a trace that stops partway means a condition blocked it.
- Action: what runs when trigger fires and conditions pass. Keep it short. If order, timing, waits, or branching matter, put the sequence in a script and call it from the automation.

Since 2026.7, purpose-specific triggers and conditions are the default building blocks (`battery.became_low`, motion detected, temperature crossed threshold). Prefer them over generic state/numeric_state/template equivalents because they handle `unknown`/`unavailable`, event re-arming, and unit quirks internally. Generic triggers still work and are correct when no purpose-specific block fits. This is additive, not a migration tax. Existing YAML keeps running.

Renames to know (old keys no longer load): `battery.low` is now `battery.became_low`, `timer.time_remaining` is now `timer.remaining_time_reached`, `vacuum.docked` is now `vacuum.returned_to_dock`, `update.update_became_available` is now `update.became_available`, trigger `behavior: any`/`last` is now `each`/`all`. Person/device-tracker `entered_home`/`left_home` triggers and `is_home`/`is_not_home` conditions were removed in 2026.5; use `state` trigger `to: home` / `to: not_home`.

## 2. Native-first decision workflow

For every new automation, follow this order:

1. Check for a purpose-specific trigger/condition matching the intent. Prefer area/floor/label targets over entity lists so the automation follows membership as devices change (for example, motion in the living room area, not three named sensors).
2. Fall back to a generic native trigger/condition (`state`, `numeric_state`, `sun`, `time`, `zone`, `event`, `and`/`or`/`not`).
3. Use a template when native triggers and conditions cannot express the intent.
   Test it with actual entity states and inspect errors in traces/logs; templates
   do not make runtime failures silent by definition.
4. Check for a built-in helper before a template sensor: `min_max` for aggregation, `group` for any-on/all-on, `threshold` for crossing with hysteresis, `derivative` for rate of change, `utility_meter` for consumption. Prefer flow-created Template Helpers (UI editable) over `template:` YAML except for trigger-based templates or custom `attributes:`.
5. Pick the run mode for the intended overlap behavior (section 3). Use `single`
   when dropping overlapping starts is intentional; choose another mode when it is not.
6. Use `entity_id`, never `device_id` (breaks on re-add). Exceptions: Zigbee2MQTT autodiscovered device triggers are acceptable; ZHA buttons use `device_ieee` in an `event` trigger because ZHA has no event entities; prefer `event.received` on `event.*` entities where the integration exposes one.

Common substitutions:

- `{{ states('x') | float > 25 }}` becomes `numeric_state` condition with `above: 25`.
- `{{ is_state('a','on') and is_state('b','on') }}` becomes `condition: and` with state conditions.
- `{{ now().hour >= 9 }}` becomes `condition: time` with `after: "09:00:00"`.
- `wait_template: "{{ is_state(...) }}"` becomes `wait_for_trigger` with a state trigger. Note the semantic difference: `wait_for_trigger` waits for a change, so if the state is already true it will not proceed immediately. Use a preceding `if` check when you need "already true counts".
- `color_temp` (mireds) is removed since 2026.3. Use `color_temp_kelvin`.
- Hardcoded Blueprint entities, free text where a selector belongs, and `!input` inside a template are blueprint authoring pitfalls. Use typed selectors, bind inputs to `variables:` before templating, always set `source_url`.

## 3. Run modes

| Mode | Re-trigger mid-run | Use for |
| --- | --- | --- |
| `single` (default) | Drop new run, log warning | One-shot notifications, morning routines, weekly jobs |
| `restart` | Stop old run, start over | Motion lights with timeout, stalled detection, auto-off delays |
| `queued` | Run in order after current finishes | Sequential device control, notifications that must not be lost |
| `parallel` | Independent concurrent runs | Per-device actions that do not interact |

`queued` and `parallel` cap at `max: 10` by default. Set `max_exceeded: silent` for intentional throttling (for example, `single` plus a trailing `delay:` as a 5 minute throttle).

The classic bug: motion light with `delay:` in `single` mode. The automation runs for the whole delay, drops re-triggers, then turns the light off while someone is still in the room. Fix is `mode: restart` (timer resets on motion) or `wait_for_trigger` on motion-clear with a timeout.

Scripts have modes too. A running `single`-mode script declines a second call
and normally logs a warning; `max_exceeded: silent` suppresses that warning.

## 4. Reliability patterns

- Debounce with `for:` on triggers. `for: "00:05:00"` means the state must hold, which suppresses brief sensor flicker. Recovery from `unknown` or `unavailable`
  can resemble a meaningful state change; guard transitions explicitly. A `for:`
  timer itself resets across automation reloads and HA restarts.
- Harden against `unavailable`/`unknown`. Guard both `trigger.from_state` and `trigger.to_state`, not just `to_state`, otherwise `unavailable -> on` after a restart looks like a real event. The deliberate exception: watchdogs that should fire on `unavailable` (bridge offline, device offline) must not use these guards.
- `numeric_state` triggers re-arm on `unavailable` blips, so an unchanged value can fire with no real crossing. Add an explicit condition rejecting `unavailable`/`unknown` in `from_state`.
- Prefer `wait_for_trigger` (event driven) over `wait_template` (polling) and over bare `delay:` for presence timeouts. Always set `timeout:` plus `continue_on_timeout: true` on long waits so one slow device cannot block the routine forever.
- Assume restart amnesia: any in-flight `delay`, `wait_for_trigger`, or `wait_template` is wiped on restart or automation reload with no log. For must-survive timers use a `timer` helper, split into two automations (start event plus deadline trigger with `for:`), or accept and document the gap. Never use a long wait for safety-critical off actions without a fallback trigger (HA start plus state check).
- Separate critical path from enrichment path. Device control, locks, alarm, and safety shutoffs are critical. Phone summaries, TTS chimes, camera snapshots, and cloud calls are enrichment. Put `continue_on_error: true` on enrichment steps only, never on critical steps. A speaker outage must not decide whether away mode armed.
- Consolidate with `choose` and trigger IDs instead of N near-identical automations, but keep the trigger ID mapping honest: every ID must be consumed by exactly one branch, and every branch must name its IDs. The trigger ID trap (ten triggers, one giant choose, actions firing wrongly with no log error) is the most common 2026-era mistake.
- Avoid hardcoded clock times for daylight behavior. Use `sun` triggers and `sun.sun below_horizon` / elevation conditions. A 6 PM lights-on rule is wrong three seasons a year. Fixed times are fine for intentional schedules (wake-up, backup window) but those values should be `input_datetime` helpers when the household may want to tune them.
- Throttle notification-heavy triggers. A `numeric_state below: 30` with no `for:` fires on every 1 percent drop. Add `for:`, a cooldown (`single` plus delay), or an `alert` helper with acknowledgment for critical repeats (water leak, smoke).

## 5. Scenes vs scripts vs automations vs blueprints

- Scene: a saved snapshot of states applied all at once. No order, no delays, no logic. Use for looks (evening relax, movie dim) and for snapshot-restore: `scene.create` with `snapshot_entities` before a temporary override, `scene.turn_on` to restore after. A flat list of set-state actions with no timing is a scene, not a script.
- Script: an ordered recipe with delays, waits, branches, and loops. No triggers; something must call it (button, voice, automation). Use for reusable sequences (notify-all-phones, arrival sequence) and to keep automation action blocks short. Scripts accept fields for parameterization.
- Automation: the only one that watches the world and fires by itself. It decides when. Its action should ideally call a scene (steady-state look) and/or a script (sequenced part).
- Blueprint: a factory that stamps out many independent automations from one template. Use the moment you copy-paste the same logic for a second room or device. Each stamped automation has its own trace and toggle. Fix-once-propagate-everywhere is the win; break-once-propagate-everywhere is the risk, so test a blueprint change on one instance first. Point tunables at helpers (input_number timeout) rather than baking literals into inputs so the household can tune from a dashboard.

Current repo gap: 0 scripts, 0 scenes (except dynamic `scene.create`), 0 blueprints, 0 helpers. Repeated entity lists are copy-pasted across 5 plus automations. Next refactor should introduce at least one notify script, one bedroom-lights script or scene pair, and helpers for thresholds and schedules (section 8).

## 6. Notifications, presence, and use-case catalog

Proven use-case families, with status in this repo:

- Lighting by sun and presence: sunset on, sunrise off, dim late-night, off when away. Covered (bedroom evening presence, stairs sunset to sunrise, living room sign, away enforcement). Missing: illuminance (lux) gating, mmWave hold behavior, adaptive color temperature through the day.
- Away and return: snapshot on leave, enforce off while empty, restore on return, re-apply night lighting on dark return. Covered by `away_lights_off_restore`. Missing: vacation mode helper, guest override, per-person last-leaves logic polish (section 8, finding A2).
- Bedtime and wake: charging-triggered lights-out, low-battery reminder before bed, gentle morning ramp. Covered. Missing: alarm integration (next-alarm sensor), weekend vs weekday schedule, wake-up light fade (`light brightness over time` ramp instead of instant on).
- Appliance and hobby (3D printer): started/finished/error/stalled/idle nudges with progress milestones and camera snapshot. Covered extensively (7 automations). Missing: filament-runout pause hook, enclosure temperature guard, power-loss detection, print-rate-derivative stall signal instead of fixed 15 minutes.
- System health: Zigbee bridge and device offline watchdog, permit-join auto-close, HA update available, startup notify, weekly backup with verification. Covered. Missing: MQTT broker watchdog, Postgres recorder health, disk and backup-age critical alert with repeat-until-acknowledged, integration repair-issue surfacing.
- Battery fleet: low-battery scan with 1 hour debounce. Covered via template scan. Consider migrating to `battery.became_low` purpose-specific trigger or the community low-battery blueprint when available for the installed domains.
- Climate and energy: open-window-while-HVAC guard, eco setback when empty, humidity and mold-risk nudge, energy price shifting. Not covered. The living room AC entity exists (`climate.living_room_ac_living_room_ac_thermostat`) but no automation drives it.
- Safety: water leak with repeating critical alert, smoke/CO escalation, door-left-open with person-home gating, freezer-door ajar. Not covered.
- Network and infra tie-in: restart-safe presence lighting, Cloudflare Tunnel or backup-failure paging, UPS on-battery shutdown staging. Partially covered (startup notify, backup verify). Missing: dead-mans-switch for Flux or k3s (heartbeat sensor plus missed-check-in alert).

When adding a use case, check the community blueprint forum first for motion lighting, low-battery, HVAC window guard, wake-up fade, and critical repeating notifications. Importing a vetted blueprint beats hand-rolling fiddly timer-reset and override logic.

## 7. Debugging and day-two operations

- Traces first: Settings, Automations, open automation, Traces. Shows trigger values, per-condition pass/fail, per-action outcomes. No trace means the trigger never fired, look upstream, not at actions.
- Run actions manually from the three-dot menu to test the action block in isolation (skips triggers and conditions). Test a single condition from its own three-dot menu. Live condition badges (since 2026.6) show pass/fail in real time.
- Developer Tools States shows real entity states (check `unknown`/`unavailable` before blaming logic). Developer Tools Template renders Jinja against live state. Developer Tools Events reveals exact event names and payloads for event triggers.
- Logs: Settings, System, Logs, filtered by automation or entity name. Template errors now appear in traces.
- Recorder growth: this repo uses external Postgres (`recorder.db_url` via `!secret`), which avoids SQLite bloat. Keep `purge_keep_days` and `commit_interval` intentional, exclude noisy domains fromRecorder when adding cameras or power sensors, and note that 30-day trace retention costs database space.
- Backup before risky work: full backup before upgrades, blueprint conversions, and entity renames. A single-object rollback beats a full restore, which discards every unrelated change since the archive and restarts HA. Never delete a backup or start a Core/OS upgrade without explicit user confirmation.

## 8. Reviewing the current setup

Use `just ha-inventory`, `just ha-diff`, the owning automation files and traces
for fresh evidence. A saved inventory or prior passing resource/test count does
not establish current coverage. `just ha-capture` records a snapshot; it does
not adopt UI edits or advance the verified baseline. Use Workflow B in the
[configuration runbook](home-assistant-configuration.md#workflow-b-ui-experiment-adoption-reconcile-ui-changes)
to adopt intended live changes.

The September audit's fixed-issue list, counts and proposed automations are
historical; consult Git history for that investigation. Current review should
check trigger availability, restart behavior, overlapping runs, unavailable
states and actual entity ownership rather than treating old findings as an
active backlog.

## 9. Repo conventions (must follow)

- One file per automation under `home-assistant/automations/`, filename equals automation `id` (for example, `stairs_light_sunset_to_sunrise.yaml` holds `id: stairs_light_sunset_to_sunrise`). Required keys: `id`, `alias`, `description`, `triggers`, `actions`. Always set `mode` explicitly even when `single` is intended, so the choice is visible.
- Human-readable `alias` values prefixed by area or domain (`Bedroom Lights: ...`, `A1: ...`, `Zigbee: ...`, `System: ...`, `Away: ...`). Descriptions state intent and any accepted restart gap.
- No plaintext secrets. `!secret` references only. `just ha-validate` secret-scans before persistence; keep LLATs in `HASS_TOKEN`, `HASS_TOKEN_FILE`, or `.agent-state/home-assistant/token`, never in Git.
- Workflow: Workflow A for new asks (inspect with `just ha-diff`, edit Git source, `just ha-validate`, `just ha-plan --select <kind>/<key>`, commit and deploy, then `just ha-verify`). Workflow B for UI experiments (diff, `just ha-adopt --select`, validate, commit). Revert only on explicit user request (`just ha-revert --select`). Pause UI edits on target resources during apply; the tool enforces read-before-write and lease locking.
- Validation gate before handoff: `just ha-validate` and `just check-changed` at minimum. `nix develop ./flake -c bash scripts/checks/home-assistant.sh` is the canonical offline check per `AGENT_MAP.md`.
- Never hand-edit `.storage/`, never generate YAML snippets for manual pasting when the config API path exists, never tell the user to edit `configuration.yaml` for a UI-configurable integration.

## 10. Checklist for new automations

1. Purpose-specific trigger or condition available? Area/floor/label target instead of entity list?
2. Native condition instead of template? Helper instead of template sensor?
3. Mode set deliberately (`single`/`restart`/`queued`/`parallel` plus `max` where needed)?
4. `entity_id` (or `device_ieee` for ZHA), no `device_id`? `color_temp_kelvin`, not `color_temp`?
5. `for:` debounce where flicker or restart storms are possible? `unknown`/`unavailable` guards on both `from_state` and `to_state` unless the automation intentionally watches unavailability?
6. Long `delay`/`wait` audited for restart amnesia with a fallback or documented gap? `timeout` plus `continue_on_timeout` on every long wait? `continue_on_error: true` on enrichment steps only?
7. Trigger IDs each consumed exactly once? Critical legs unable to be dropped by `single` mode?
8. Repeated action block extracted to a script, steady-state look extracted to a scene, tunables promoted to helpers?
9. `id`, `alias`, `description`, `mode` present; filename matches `id`?
10. `just ha-validate` clean, secret scan clean, `just ha-plan --select` inspected, baseline advanced via deploy or authorized apply plus `just ha-verify`?
