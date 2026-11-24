#!/bin/sh
set -eu

namespace="automation"
state_cm="website-health-state"
config_dir="${CONFIG_DIR:-/config}"
topic="$(cat "${config_dir}/ntfy-topic")"
ntfy_url="http://ntfy-ntfy.observability.svc.cluster.local/${topic}"

kubectl -n "${namespace}" get configmap "${state_cm}" >/dev/null 2>&1 || \
  kubectl -n "${namespace}" create configmap "${state_cm}" --from-literal=initialized=true >/dev/null

notify() {
  title="$1"
  priority="$2"
  message="$3"
  curl -fsS \
    -H "Authorization: Bearer ${NTFY_TOKEN}" \
    -H "X-Title: ${title}" \
    -H "X-Priority: ${priority}" \
    -d "${message}" \
    "${ntfy_url}" >/dev/null || true
}

while IFS='|' read -r name url; do
  [ -z "${name}" ] && continue
  code="$(curl -fsS -k -L --connect-timeout 10 --max-time 25 -o /dev/null -w "%{http_code}" "${url}" || true)"
  status="down"
  if [ "${code}" -ge 200 ] 2>/dev/null && [ "${code}" -lt 400 ] 2>/dev/null; then
    status="up"
  fi

  previous="$(kubectl -n "${namespace}" get configmap "${state_cm}" -o json | jq -r --arg key "${name}" '.data[$key] // ""')"
  previous_status="$(printf '%s' "${previous}" | cut -d: -f1)"

  # One failed probe is recorded as "failing"; only a second consecutive
  # failure pages, so a single timeout never produces a down/recovered pair.
  if [ "${status}" = "down" ]; then
    if [ "${previous_status}" = "failing" ]; then
      notify "Website down: ${name}" "urgent" "${url} returned HTTP ${code:-000} twice in a row"
    elif [ "${previous_status}" != "down" ]; then
      status="failing"
    fi
  elif [ "${previous_status}" = "down" ]; then
    notify "Website recovered: ${name}" "default" "${url} is healthy again with HTTP ${code}"
  fi

  kubectl -n "${namespace}" patch configmap "${state_cm}" --type merge \
    -p "$(jq -n --arg key "${name}" --arg value "${status}:${code}" '{data: {($key): $value}}')" >/dev/null
done < "${config_dir}/sites.txt"
