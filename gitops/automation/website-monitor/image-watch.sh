#!/bin/sh
set -eu

namespace="automation"
state_cm="website-image-state"
topic="$(cat /config/ntfy-topic)"
ntfy_url="http://ntfy-ntfy.observability.svc.cluster.local/${topic}"

kubectl -n "${namespace}" get configmap "${state_cm}" >/dev/null 2>&1 || \
  kubectl -n "${namespace}" create configmap "${state_cm}" --from-literal=initialized=true >/dev/null

notify() {
  title="$1"
  message="$2"
  curl -fsS \
    -H "Authorization: Bearer ${NTFY_TOKEN}" \
    -H "X-Title: ${title}" \
    -H "X-Priority: default" \
    -d "${message}" \
    "${ntfy_url}" >/dev/null || true
}

while IFS='|' read -r deploy_ns deploy_name; do
  [ -z "${deploy_ns}" ] && continue
  key="$(printf '%s-%s' "${deploy_ns}" "${deploy_name}" | tr -c 'A-Za-z0-9._-' '-')"
  image="$(kubectl -n "${deploy_ns}" get deployment "${deploy_name}" -o json | jq -r '.spec.template.spec.containers | map(.name + "=" + .image) | join(", ")')"
  previous="$(kubectl -n "${namespace}" get configmap "${state_cm}" -o json | jq -r --arg key "${key}" '.data[$key] // ""')"

  if [ -n "${previous}" ] && [ "${previous}" != "${image}" ]; then
    notify "Image updated: ${deploy_ns}" "${deploy_ns}/${deploy_name} changed from ${previous} to ${image}"
  fi

  kubectl -n "${namespace}" patch configmap "${state_cm}" --type merge \
    -p "$(jq -n --arg key "${key}" --arg value "${image}" '{data: {($key): $value}}')" >/dev/null
done < /config/deployments.txt
