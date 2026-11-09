# Orchestrator rehearsal and gate 4

A credential-free run of the persistent-agent controller on homelab-02, used
for the live checks of gate 4 in
[persistent-agent.md](../architecture/persistent-agent.md). Every Secret
value is a random placeholder: the controller starts, serves its APIs and
admits intake signals, but takes no task that needs the forge, ntfy or a
model. Nothing is bound into Flux; the rehearsal is applied and removed by
hand.

Inputs: `config/orchestrator/rehearsal.json` (node, disposable
`agent-sandbox` project, rehearsal ntfy topic) and the digests pinned in
`config/orchestrator/images.json`. The controller's node-local store lives
in `/persist/persistent-agent` on homelab-02, created once with:

```sh
ssh homelab-02 'sudo install -d -o 10001 -g 10001 -m 0700 /persist/persistent-agent'
```

## Apply

```sh
python -m scripts.orchestrator.rehearsal render > /tmp/rehearsal.yaml
kubectl apply -f /tmp/rehearsal.yaml          # namespace first: re-run once if Secrets race it
python -m scripts.orchestrator.rehearsal secrets | kubectl apply -f -
kubectl -n persistent-agent rollout status deploy/orchestrator-controller
```

## Gate 4 checks

1. **Admission.** Each case from `python -m scripts.orchestrator.rehearsal
   admission` is created with
   `kubectl create --dry-run=server --as=system:serviceaccount:persistent-agent:orchestrator-controller -f -`.
   Only `worker job` may be admitted; every other case must be denied.
2. **Credential reach.** `kubectl auth can-i get secrets -n persistent-agent
   --as=system:serviceaccount:persistent-agent:orchestrator-controller` is
   `no`, and so is changing admission policies or RBAC.
3. **Worker network.** `python -m scripts.orchestrator.rehearsal probe |
   kubectl apply -f -`, wait for the pod to finish, and read its log: a JSON
   report with `"passed": true` (DNS allow and refuse, TLS to the providers,
   private and API-server isolation, foreign worker token refused).
4. **Intake ingress.** A pod outside `observability` and `automation` cannot
   reach `orchestrator-controller.persistent-agent:8092`.

Record the results under "Gate evidence" in the architecture doc.

## Remove

```sh
kubectl delete pod -n persistent-agent gate4-network-probe --ignore-not-found
kubectl delete -f /tmp/rehearsal.yaml --ignore-not-found
```

The store directory on homelab-02 is kept; the rehearsal leaves only an
empty database there.
