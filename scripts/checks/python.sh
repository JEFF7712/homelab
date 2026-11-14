#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
for tool in ruff pyright python; do command -v "$tool" >/dev/null || { echo "missing required tool: $tool; run nix develop ./flake" >&2; exit 127; }; done
# Keep equal to python_paths in the justfile (tests/test_python_gate_paths.py).
ruff_paths=(
  scripts opnsense_reconciler tests flake/modules services/bus-display
  services/led-matrix gitops/observability/network-monitor gitops/media/spotify-sync
)
# Scripts that ConfigMap generators deploy from gitops/, linted but not
# formatted: they were extracted byte for byte from inline ConfigMaps, and
# reformatting them would only churn running workloads.
ruff_lint_only_paths=(
  gitops/automation
)
if [[ "${CI_LINT_EXTERNAL:-0}" != "1" ]]; then
  ruff format --check "${ruff_paths[@]}"
fi
ruff check --select E,F,I,UP --ignore E501 "${ruff_paths[@]}" "${ruff_lint_only_paths[@]}"
# Left out of pyright because their imports exist only on the target host or
# in a separate environment, not in this dev shell: the MQTT/BLE/NVML/numpy
# host bridges in flake/modules (ledfx-roku-bridge, roku-bridge,
# roku-cloud-bridge, apollo-ble-bridge, controller-pairer, bedroom-audio-fft,
# nvidia_gpu_exporter), scripts/jarvis_wake_eval.py, scripts/voice_id, and
# the services/* subprojects, which type-check in their own uv environments.
pyright_paths=(
  scripts/agent scripts/ci scripts/registry scripts/cloudflare scripts/websites
  scripts/verifier scripts/home_assistant scripts/projectctl scripts/orchestrator
  scripts/agent_workspaces scripts/checks scripts/omarchy_vm scripts/switches scripts/voice
  scripts/deploy_fleet.py scripts/opnsense_config.py scripts/tofu_config.py
  scripts/fleet_reboot.py scripts/jarvis_acoustic_eval.py scripts/jarvis_eval.py
  scripts/jev_decision_gate.py scripts/k3s_backup_source.py scripts/nemotron_bench.py
  scripts/netbird_agent_workspaces.py scripts/prometheus_storage_migrate.py
  scripts/record_jev_decisions.py scripts/recovery_guest.py scripts/recovery_host.py
  scripts/recovery_pack.py scripts/score_l1_acceptance.py
  scripts/navidrome_lookup.py
  scripts/voice_stt_switch.py scripts/voice_topology.py
  flake/modules/moonlight-autostart.py flake/modules/repo_monitor.py flake/modules/kiosk-rotate.py
  flake/modules/kiosk-ha-token-seed.py flake/modules/jarvis-spectrum.py
  flake/modules/bedroom-bluetooth.py flake/modules/omarchy_vm
  gitops/observability/network-monitor/monitor.py gitops/media/spotify-sync/sync.py
  gitops/automation/forgejo-dashboard/dashboard.py
  gitops/automation/renovate-approval-webhook/renovate-approval-webhook.py
  gitops/automation/renovate-reviewer/renovate-agent.py
  opnsense_reconciler
)
pyright "${pyright_paths[@]}" &
pyright_pid=$!

if [[ -n "${CI_SCOPED_TEST_MODULES:-}" ]]; then
  read -ra scoped_modules <<< "$CI_SCOPED_TEST_MODULES"
  python -m unittest "${scoped_modules[@]}" -v &
elif [[ "${CI_UNIT_TESTS_EXTERNAL:-0}" == "1" ]]; then
  (
    python -m scripts.ci.tests --start tests --pattern test_zot_registry_module.py --output "${CI_TEST_REPORT:-artifacts/ci/nix-integration.xml}"
    python -m unittest tests.test_agent_workspace_sources -v
  ) &
elif [[ -n "${CI_TEST_REPORT:-}" ]]; then
  python -m scripts.ci.tests --output "$CI_TEST_REPORT" &
else
  python -m unittest discover -s tests -v &
fi
unittest_pid=$!

pyright_rc=0
unittest_rc=0
wait "$pyright_pid" || pyright_rc=$?
wait "$unittest_pid" || unittest_rc=$?

if [[ $pyright_rc -ne 0 ]]; then
  exit "$pyright_rc"
fi
if [[ $unittest_rc -ne 0 ]]; then
  exit "$unittest_rc"
fi
