# OpenClaw Deployment

Secure deployment of OpenClaw AI agent on Kubernetes.

## Components

- **manager-deployment.yaml** - Main OpenClaw gateway with security hardening
- **worker-deployment.yaml** - Worker nodes for sandboxed tasks
- **openclaw.json** - Configuration with audit logging and tool restrictions
- **pvc.yaml** - Persistent storage for workspace and memory
- **network-policy.yaml** - Cilium network policies (commented out, see SECURITY.md)
- **nginx.conf** - Reverse proxy configuration

## Security Features

This deployment implements multiple layers of security:

1. **Container Security**
   - Read-only root filesystem
   - Non-root user (UID 1000)
   - No privilege escalation
   - All capabilities dropped
   - Resource limits enforced

2. **Secret Management**
   - File-based secrets (not env vars)
   - Pulled from GitLab CI/CD via External Secrets Operator
   - Minimal permissions (mode 0400)

3. **Audit Logging**
   - All tool calls logged to `/home/node/.openclaw/audit.log`
   - Security alerts logged to `/home/node/.openclaw/security-audit.log`
   - JSON format with timestamps

4. **Tool Restrictions**
   - Dangerous commands blocked
   - Execution timeouts
   - HTTP request limits

5. **Monitoring**
   - Health probes (liveness, readiness)
   - Resource monitoring
   - Security scanning script

See [SECURITY.md](./SECURITY.md) for complete security documentation.

## Deployment

Managed via ArgoCD:
```bash
kubectl apply -f ../../apps/openclaw-hive.yaml
```

ArgoCD will sync from this directory automatically.

## Monitoring

### View Logs
```bash
# Application logs
kubectl logs -n openclaw -l app=openclaw-manager -f

# Audit log
kubectl exec -n openclaw deployment/openclaw-manager -- tail -f /home/node/.openclaw/audit.log

# Security alerts
kubectl exec -n openclaw deployment/openclaw-manager -- tail -f /home/node/.openclaw/security-audit.log
```

### Run Security Scan
```bash
./monitor-security.sh
```

Set `ALERT_WEBHOOK` environment variable to send alerts to a webhook:
```bash
ALERT_WEBHOOK=https://your-webhook.com/alerts ./monitor-security.sh
```

### Resource Usage
```bash
kubectl top pod -n openclaw
```

## Configuration

### Adding Secrets

1. Add secret to GitLab CI/CD variables (Settings → CI/CD → Variables)
2. Update `../../secrets/openclaw.yaml` to reference the variable
3. ArgoCD will sync the External Secret and restart pods

### Updating Configuration

Edit `openclaw.json` and commit. ArgoCD will apply changes.

### Tool Restrictions

Edit `openclaw.json` under `tools.exec.blocklist` or `tools.exec.requireApproval` to add patterns.

## Troubleshooting

### Pod Won't Start

Check init container logs:
```bash
kubectl logs -n openclaw deployment/openclaw-manager -c init-config
```

### Permission Errors

Verify PVC ownership:
```bash
kubectl exec -n openclaw deployment/openclaw-manager -- ls -la /home/node/.openclaw
```

### Network Issues

Check Cilium connectivity:
```bash
kubectl exec -n openclaw deployment/openclaw-manager -- curl -v https://api.anthropic.com
```

If network policy is enabled, verify FQDN rules in `network-policy.yaml`.

## Development

### Local Testing

Build image:
```bash
docker build -t jeff7712/openclaw:test .
docker push jeff7712/openclaw:test
```

Update deployment to use `:test` tag, then sync.

### Accessing Shell

```bash
kubectl exec -it -n openclaw deployment/openclaw-manager -- /bin/sh
```

Note: Shell access is limited due to read-only root filesystem.

## References

- [OpenClaw Documentation](https://docs.openclaw.ai)
- [Security Configuration](./SECURITY.md)
- [Monitoring Script](./monitor-security.sh)
