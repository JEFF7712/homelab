# predmarkbot deployment

Vendored from `~/predmarkbot` via `git subtree` and built/deployed via the
homelab GitLab CI + ArgoCD GitOps loop.

## Update the deployed bot

When you ship a new version of predmarkbot:

```bash
# 1. Develop + commit in the dev repo as usual
cd ~/predmarkbot
git commit -am "..."

# 2. Sync the vendored source in the homelab repo
cd ~/homelab
git subtree pull --prefix=infrastructure/predmarkbot/src \
  ~/predmarkbot main --squash

# 3. Push — CI builds a new image, Renovate PR-bumps the deployment tag
git push
```

## Flip from shadow → demo trading

Edit `infrastructure/predmarkbot/configmap.yaml`, change `mode: shadow` to
`mode: demo`. Commit, push. ArgoCD will sync the ConfigMap; Stakater
Reloader will restart the pod.

## Flip from demo → prod (real money)

Edit `infrastructure/predmarkbot/configmap.yaml`:
- `mode: prod`
- `prod_confirmed: true`
- `kalshi.api_base_url: https://api.elections.kalshi.com/trade-api/v2`
- `kalshi.ws_base_url: wss://api.elections.kalshi.com/trade-api/ws/v2`
- `kalshi.key_id_env: KALSHI_PROD_KEY_ID`
- `kalshi.private_key_path: /var/run/secrets/kalshi/kalshi-prod-private-key.pem`

The bot will refuse to start without `prod_confirmed: true` (config-level safety gate).

## Recover from a kill-switch trip

If the kill switch fires, the pod writes `/var/lib/predmarkbot/state.db.killed`
and refuses to restart. Resolve manually:

```bash
# Inspect the cause first
kubectl -n predmarkbot logs deployment/predmarkbot --previous

# Then delete the sentinel
kubectl -n predmarkbot exec deployment/predmarkbot -- \
  rm /var/lib/predmarkbot/state.db.killed

# Restart
kubectl -n predmarkbot rollout restart deployment/predmarkbot
```
