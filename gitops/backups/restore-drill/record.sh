#!/bin/sh
# Write each result to restore-drill-evidence; fail when any dataset failed
# or produced no result, so the failure is visible beyond the evidence.
# Usage: record.sh <results-dir> <dataset>...
set -eu
results="$1"
shift
now="$(date +%s)"
kubectl get configmap restore-drill-evidence >/dev/null 2>&1 \
  || kubectl create configmap restore-drill-evidence >/dev/null
status=0
for dataset in "$@"; do
  result="$(cat "${results}/${dataset}" 2>/dev/null || echo "fail: no result")"
  if [ "${result#pass}" != "${result}" ]; then
    patch="$(jq -cn --arg d "${dataset}" --arg r "${result}" --arg t "${now}" \
      '{data: {($d + ".result"): $r, ($d + ".checked"): $t, ($d + ".passed"): $t}}')"
  else
    status=1
    patch="$(jq -cn --arg d "${dataset}" --arg r "${result}" --arg t "${now}" \
      '{data: {($d + ".result"): $r, ($d + ".checked"): $t}}')"
  fi
  kubectl patch configmap restore-drill-evidence --type merge -p "${patch}" >/dev/null
  echo "${dataset}: ${result}"
done
exit "${status}"