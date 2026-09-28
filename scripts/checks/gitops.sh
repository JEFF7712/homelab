#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
for tool in yamllint kubectl kubeconform; do command -v "$tool" >/dev/null || { echo "missing required tool: $tool; run nix develop ./flake" >&2; exit 127; }; done
if [[ "${CI_LINT_EXTERNAL:-0}" != "1" ]]; then yamllint gitops; fi
schema_root="$PWD/schemas/kubernetes"
if [[ ! -d "$schema_root" ]]; then
  echo 'missing pinned repository CRD schemas under schemas/kubernetes' >&2
  exit 69
fi
check_kustomization() {
  local file="$1"
  case "$file" in
    gitops/secrets/*) return 0 ;;
  esac
  local output status=0
  output=$({ kubectl kustomize "$(dirname "$file")" | kubeconform -strict -summary -ignore-missing-schemas -skip CustomResourceDefinition -schema-location default -schema-location "$schema_root/{{.ResourceKind}}{{.KindSuffix}}.json"; } 2>&1) || status=$?
  printf '%s\n' "$output"
  if [[ $status -ne 0 ]]; then
    echo "Kubernetes validation failed in $file" >&2
    return "$status"
  fi
  local allow_skipped=false
  case "$file" in
    gitops/clusters/homelab-01/kustomization.yaml | \
      gitops/clusters/homelab-01/flux-system/kustomization.yaml | \
      gitops/ingress/crds/kustomization.yaml) allow_skipped=true ;;
  esac
  if grep -Eq 'Skipped: [1-9]' <<<"$output" && [[ $allow_skipped == false ]]; then
    echo "missing Kubernetes schemas in $file; refresh and commit the required CRD schemas" >&2
    return 69
  fi
}
export -f check_kustomization
export schema_root

# shellcheck disable=SC2016
find gitops -name kustomization.yaml -print0 | sort -z | xargs -0 -n 1 -P "${CHECK_JOBS:-8}" bash -euo pipefail -c 'check_kustomization "$1"' _
