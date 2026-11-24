#!/bin/sh
set -eu

namespace="automation"
state_cm="cluster-sentinel-state"
topic="$(cat /config/ntfy-topic)"
ntfy_url="http://ntfy-ntfy.observability.svc.cluster.local/${topic}"
now="$(date +%s)"
cert_warn_seconds=$((21 * 24 * 60 * 60))

kubectl -n "${namespace}" get configmap "${state_cm}" >/dev/null 2>&1 || \
  kubectl -n "${namespace}" create configmap "${state_cm}" --from-literal=initialized=true >/dev/null

state_get() {
  kubectl -n "${namespace}" get configmap "${state_cm}" -o json | jq -r --arg key "$1" '.data[$key] // ""'
}

state_set() {
  kubectl -n "${namespace}" patch configmap "${state_cm}" --type merge \
    -p "$(jq -n --arg key "$1" --arg value "$2" '{data: {($key): $value}}')" >/dev/null
}

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

# Each failure line starts with the failing object's identity, ended by ": "
# or " (". The identities are stored per check so a second object failing
# while the check is already bad still alerts.
check_change() {
  key="$1"
  status="$2"
  fail_title="$3"
  ok_title="$4"
  message="$5"
  previous="$(state_get "${key}")"
  items=""
  if [ "${status}" = "bad" ]; then
    items="$(printf '%s\n' "${message}" | sed -E 's/( \(|: ).*//' | sed '/^$/d' | sort -u)"
    new_items="$(printf '%s\n' "${items}" | KNOWN="$(state_get "${key}-items")" awk '
      BEGIN { n = split(ENVIRON["KNOWN"], known, "\n"); for (i = 1; i <= n; i++) seen[known[i]] = 1 }
      $0 != "" && !($0 in seen)
    ')"
    if [ -n "${new_items}" ]; then
      notify "${fail_title}" "high" "${message}"
    fi
  elif [ "${previous}" = "bad" ]; then
    notify "${ok_title}" "default" "${message}"
  fi
  state_set "${key}-items" "${items}"
  state_set "${key}" "${status}"
}

# Ready=Unknown is a reconcile in progress, not a failure; counting it sent
# a notice whenever a deploy touched a new Kustomization.
flux_bad="$(
  kubectl get kustomizations.kustomize.toolkit.fluxcd.io -A -o json | jq -r '
    .items[]
    | select(.status.conditions // [] | any(.type == "Ready" and .status == "False"))
    | "\(.metadata.namespace)/\(.metadata.name): \(.status.conditions[-1].reason // "NotReady") \(.status.conditions[-1].message // "")"
  '
  kubectl get helmreleases.helm.toolkit.fluxcd.io -A -o json | jq -r '
    .items[]
    | select(.status.conditions // [] | any(.type == "Ready" and .status == "False"))
    | "\(.metadata.namespace)/\(.metadata.name): \(.status.conditions[-1].reason // "NotReady") \(.status.conditions[-1].message // "")"
  '
)"
flux_bad="$(printf '%s\n' "${flux_bad}" | sed '/^$/d' | sort -u)"
if [ -n "${flux_bad}" ]; then
  check_change "flux-resources" "bad" "Flux health issue" "Flux recovered" "${flux_bad}"
else
  check_change "flux-resources" "ok" "Flux health issue" "Flux recovered" "All Flux Kustomizations and HelmReleases are ready."
fi

external_secret_bad="$(kubectl get externalsecrets.external-secrets.io -A -o json | jq -r '
  .items[]
  | select((.status.conditions // [] | map(select(.type == "Ready" and .status == "True")) | length) == 0)
  | "\(.metadata.namespace)/\(.metadata.name): \(.status.conditions[-1].reason // "NotReady") \(.status.conditions[-1].message // "")"
')"
if [ -n "${external_secret_bad}" ]; then
  check_change "external-secrets" "bad" "ExternalSecret sync issue" "ExternalSecrets recovered" "${external_secret_bad}"
else
  check_change "external-secrets" "ok" "ExternalSecret sync issue" "ExternalSecrets recovered" "All ExternalSecrets are ready."
fi

node_bad="$(kubectl get nodes -o json | jq -r '
  .items[]
  | select((.status.conditions // [] | map(select(.type == "Ready" and .status == "True")) | length) == 0)
  | "\(.metadata.name): NotReady"
')"
if [ -n "${node_bad}" ]; then
  check_change "nodes" "bad" "Kubernetes node issue" "Kubernetes nodes recovered" "${node_bad}"
else
  check_change "nodes" "ok" "Kubernetes node issue" "Kubernetes nodes recovered" "All Kubernetes nodes are Ready."
fi

pod_bad="$(kubectl get pods -A -o json | jq -r '
  .items[]
  | select(.status.phase != "Succeeded")
  | . as $pod
  | [
      (.status.containerStatuses // [])[]
      | select(
          (.state.waiting.reason // "") as $reason
          | ["CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError", "RunContainerError"] | index($reason)
        )
      | "\($pod.metadata.namespace)/\($pod.metadata.name): \(.state.waiting.reason)"
    ][]
' | sort -u)"
if [ -n "${pod_bad}" ]; then
  check_change "pods" "bad" "Pod runtime issue" "Pod runtime issues recovered" "${pod_bad}"
else
  check_change "pods" "ok" "Pod runtime issue" "Pod runtime issues recovered" "No pods are in critical waiting states."
fi

# Failed CronJob runs alert through Alertmanager (CronJobLastRunFailed).

cloudflare_bad="$(kubectl -n cloudflare get deployment cloudflared -o json | jq -r '
  (.spec.replicas // 0) as $desired
  | (.status.availableReplicas // 0) as $available
  | select($available < $desired)
  | "cloudflare/cloudflared: available=\($available) desired=\($desired)"
')"
cloudflare_restarts="$(kubectl -n cloudflare get pods -l app=cloudflared -o json | jq -r '[.items[].status.containerStatuses[]?.restartCount // 0] | add // 0')"
previous_restarts="$(state_get "cloudflare-restarts")"
if [ -n "${previous_restarts}" ] && [ "${cloudflare_restarts}" -gt "${previous_restarts}" ]; then
  cloudflare_bad="$(printf '%s\ncloudflare/cloudflared: restart count increased from %s to %s\n' "${cloudflare_bad}" "${previous_restarts}" "${cloudflare_restarts}")"
fi
state_set "cloudflare-restarts" "${cloudflare_restarts}"
cloudflare_bad="$(printf '%s\n' "${cloudflare_bad}" | sed '/^$/d' | sort -u)"
if [ -n "${cloudflare_bad}" ]; then
  check_change "cloudflare-tunnel" "bad" "Cloudflare tunnel issue" "Cloudflare tunnel recovered" "${cloudflare_bad}"
else
  check_change "cloudflare-tunnel" "ok" "Cloudflare tunnel issue" "Cloudflare tunnel recovered" "Cloudflared has all desired replicas available and no restarted pods."
fi

# Restore drills (gitops/backups/restore-drill.yaml): every dataset needs a
# passing drill within eight days and a latest result that passed.
drill_bad=""
for item in backups/immich-db backups/home-assistant-db backups/umami-db immich/immich-library analytics/umami-wal; do
  drill_ns="${item%%/*}"
  dataset="${item#*/}"
  evidence="$(kubectl -n "${drill_ns}" get configmap restore-drill-evidence -o json 2>/dev/null || echo '{}')"
  passed="$(printf '%s' "${evidence}" | jq -r --arg k "${dataset}.passed" '.data[$k] // "0"')"
  result="$(printf '%s' "${evidence}" | jq -r --arg k "${dataset}.result" '.data[$k] // "never run"')"
  if [ "${result#pass}" = "${result}" ] || [ $((now - passed)) -gt $((8 * 24 * 60 * 60)) ]; then
    drill_bad="$(printf '%s\n%s: %s (last pass %s)' "${drill_bad}" "${item}" "${result}" \
      "$([ "${passed}" -gt 0 ] && date -u -d "@${passed}" +%Y-%m-%d || echo never)")"
  fi
done
drill_bad="$(printf '%s\n' "${drill_bad}" | sed '/^$/d')"
if [ -n "${drill_bad}" ]; then
  check_change "restore-drills" "bad" "Restore drill issue" "Restore drills recovered" "${drill_bad}"
else
  check_change "restore-drills" "ok" "Restore drill issue" "Restore drills recovered" "Every backed-up dataset restored within eight days."
fi

# Renovate exits 0 even when it aborts the repository (44.256.1 on a lscr.io
# 429, 2026-11-20), so a succeeded Job proves nothing: read the result each of
# its last two finished runs logged. One bad run is a transient rate limit;
# two in a row means Renovate has stopped updating anything.
renovate_verdict() {
  # stdin: "<job> <result>" per finished run, oldest first.
  tail -n 2 | awk '
    NF == 2 { runs++; if ($2 != "done") { bad++; job = $1; result = $2 } }
    END {
      if (runs == 2 && bad == 2)
        printf "automation/renovate-forgejo: last two runs ended %s (%s)\n", result, job
    }
  '
}

renovate_runs="$(
  kubectl -n "${namespace}" get jobs -o json | jq -r '
    .items[]
    | select((.metadata.ownerReferences // [])[0].name == "renovate-forgejo")
    | select((.status.succeeded // 0) > 0 or (.status.failed // 0) > 0)
    | "\(.metadata.creationTimestamp) \(.metadata.name)"
  ' | sort | tail -n 2 | while read -r _created job; do
    result="$(kubectl -n "${namespace}" logs "job/${job}" --tail=2000 2>/dev/null \
      | sed -n 's/.*"result": "\([a-z-]*\)".*/\1/p' | tail -n 1)"
    printf '%s %s\n' "${job}" "${result:-unknown}"
  done
)"
renovate_bad="$(printf '%s\n' "${renovate_runs}" | sed '/^$/d' | renovate_verdict)"
if [ -n "${renovate_bad}" ]; then
  check_change "renovate-runs" "bad" "Renovate is not updating" "Renovate recovered" "${renovate_bad}"
else
  check_change "renovate-runs" "ok" "Renovate is not updating" "Renovate recovered" "Renovate's last runs finished."
fi

cert_bad="$(
  kubectl get certificates.cert-manager.io -A -o json | jq -r '
    .items[]
    | . as $cert
    | ((.status.conditions // [] | map(select(.type == "Ready" and .status == "True")) | length) == 0) as $not_ready
    | (.status.notAfter // "") as $not_after
    | select($not_ready or $not_after == "")
    | "\($cert.metadata.namespace)/\($cert.metadata.name): ready=\(($not_ready | not) | tostring) expires=\($not_after)"
  '
  kubectl get certificates.cert-manager.io -A -o json | jq -r '.items[] | select(.status.notAfter != null) | "\(.metadata.namespace)/\(.metadata.name)|\(.status.notAfter)"' | while IFS='|' read -r cert_name not_after; do
    [ -z "${cert_name}" ] && continue
    expiry="$(date -d "${not_after}" +%s 2>/dev/null || echo 0)"
    if [ "${expiry}" -gt 0 ] && [ $((expiry - now)) -le "${cert_warn_seconds}" ]; then
      days_left=$(((expiry - now) / 86400))
      printf '%s: expires in %sd (%s)\n' "${cert_name}" "${days_left}" "${not_after}"
    fi
  done
)"
cert_bad="$(printf '%s\n' "${cert_bad}" | sed '/^$/d' | sort -u)"
if [ -n "${cert_bad}" ]; then
  check_change "certificates" "bad" "Certificate issue" "Certificates recovered" "${cert_bad}"
else
  check_change "certificates" "ok" "Certificate issue" "Certificates recovered" "All certificates are ready and at least 21 days from expiry."
fi
