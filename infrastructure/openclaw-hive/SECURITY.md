# OpenClaw Security Configuration

This document outlines the security hardening applied to the OpenClaw deployment.

## Security Layers

### 1. Container Security

**Read-Only Root Filesystem:**
- Root filesystem is read-only to prevent unauthorized file modifications
- Writable volumes explicitly mounted for:
  - `/tmp` - Temporary files
  - `/home/node/.openclaw` - Persistent data (PVC)
  - `/home/node/.npm` - NPM cache

**Security Context:**
- `runAsNonRoot: true` - Container runs as non-root user (UID 1000)
- `allowPrivilegeEscalation: false` - Prevents privilege escalation
- `readOnlyRootFilesystem: true` - Enforces read-only root
- `capabilities: drop: ALL` - Drops all Linux capabilities
- `seccompProfile: RuntimeDefault` - Applies default seccomp profile

**Resource Limits:**
```yaml
requests:
  cpu: 500m
  memory: 1Gi
limits:
  cpu: 2
  memory: 4Gi
```

Prevents resource exhaustion attacks and fork bombs.

### 2. Secret Management

**File-Based Secrets:**
- Secrets mounted as files at `/secrets/` instead of environment variables
- Default mode: `0400` (read-only for owner)
- Secrets exported to environment at startup for compatibility

**Available Secrets:**
- `anthropic-key` - Anthropic API key
- `gateway-token` - OpenClaw gateway authentication
- `telegram-token` - Telegram bot token
- `gitlab-token` - GitLab API access for automation

**External Secrets Operator:**
- Secrets pulled from GitLab CI/CD variables
- Automatic rotation support via operator refresh
- Secrets never committed to git

### 3. Audit Logging

**Application Logs:**
- Path: `/home/node/.openclaw/audit.log`
- Format: JSON with timestamps
- Includes: All tool calls (exec, http, read, write)
- Redaction: Disabled for audit trail (secrets not logged by application)

**Security Audit Log:**
- Path: `/home/node/.openclaw/security-audit.log`
- Monitors for suspicious patterns:
  - Secret access attempts (`cat /secrets`, `echo $.*_KEY`)
  - Data exfiltration (`curl.*$(cat`, `wget.*http`)
  - Network tools (`nc`, `netcat`)
  - Obfuscation (`base64 -d | sh`)

**Monitoring Commands:**
```bash
# Watch audit log in real-time
kubectl logs -n openclaw -l app=openclaw-manager -f | grep -E "exec|http"

# Check security alerts
kubectl exec -n openclaw deployment/openclaw-manager -- tail -f /home/node/.openclaw/security-audit.log

# Export logs for analysis
kubectl cp openclaw/openclaw-manager:/home/node/.openclaw/audit.log ./audit-$(date +%Y%m%d).log
```

### 4. Tool Restrictions

**Exec Tool:**
- Timeout: 300 seconds (prevents hanging processes)
- Blocklist: Dangerous commands blocked at application level
  - `rm -rf /` - Recursive delete
  - Fork bombs
  - `/dev/zero` writes
- Require Approval: Commands that need confirmation
  - `rm -rf` operations
  - Force git pushes
  - Command injection patterns

**HTTP Tool:**
- Timeout: 30 seconds
- Max redirects: 5
- No explicit domain allowlist (relies on network policy when enabled)

### 5. Network Security

**Current State:**
Network policy allows all egress due to issues with FQDN-based filtering.

**Recommended Cilium Network Policy** (for future implementation):
```yaml
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: openclaw-egress-strict
  namespace: openclaw
spec:
  endpointSelector:
    matchLabels:
      app: openclaw-manager
  egress:
  - toEndpoints:
    - matchLabels:
        io.kubernetes.pod.namespace: kube-system
        k8s-app: kube-dns
    toPorts:
    - ports:
      - port: "53"
        protocol: ANY
  - toFQDNs:
    - matchName: "api.anthropic.com"
    - matchName: "api.telegram.org"
    - matchName: "moltbook.com"
    - matchName: "www.moltbook.com"
    - matchName: "gitlab.com"
    toPorts:
    - ports:
      - port: "443"
        protocol: TCP
```

**Hubble Monitoring** (if available):
```bash
# Watch network traffic
hubble observe --namespace openclaw --follow

# Alert on unexpected domains
hubble observe --namespace openclaw | grep -vE "anthropic|telegram|moltbook|gitlab"
```

### 6. RBAC

**Current State:**
- Uses default ServiceAccount with no RBAC permissions
- Cannot list, create, or modify Kubernetes resources
- Cannot access resources in other namespaces

**Verification:**
```bash
kubectl auth can-i list pods --as=system:serviceaccount:openclaw:default -n openclaw
# Output: no
```

## Threat Model

### Mitigated Threats

✅ **Container Escape:** Read-only filesystem, no capabilities, non-root user
✅ **Resource Exhaustion:** CPU/memory limits prevent DoS
✅ **Unauthorized K8s Access:** No RBAC permissions
✅ **File System Tampering:** Read-only root filesystem
✅ **Malicious Code Execution:** Audit logging tracks all exec calls

### Remaining Risks

⚠️ **Data Exfiltration via Allowed Services:**
- Agent can send data to Anthropic API, Telegram, or Moltbook
- Mitigation: Audit logging + behavioral monitoring
- Requires trust in agent's instructions/prompting

⚠️ **Prompt Injection:**
- User messages could attempt to override security rules
- Mitigation: Security rules in SOUL.md/AGENTS.md, audit logging
- Requires careful prompt engineering

⚠️ **Secrets in Memory:**
- Secrets available as environment variables during runtime
- Mitigation: No RBAC to other pods, audit logging for access attempts
- Consider: Runtime secret protection tools (e.g., Sealed Secrets rotation)

## Security Checklist

**Daily:**
- [ ] Review audit logs for suspicious patterns
- [ ] Check security-audit.log for alerts

**Weekly:**
- [ ] Review tool usage patterns for anomalies
- [ ] Check resource usage for unexpected spikes
- [ ] Review Hubble logs for unexpected domains (if enabled)

**Monthly:**
- [ ] Rotate all secrets via GitLab CI/CD variables
- [ ] Review and update blocklist patterns
- [ ] Audit agent workspace for suspicious files

**Quarterly:**
- [ ] Update container images to latest
- [ ] Review and update network policy (if enabled)
- [ ] Penetration test agent behavior with adversarial prompts

## Incident Response

**If suspicious activity detected:**

1. **Immediate:**
   ```bash
   # Scale down deployment
   kubectl scale deployment/openclaw-manager -n openclaw --replicas=0
   
   # Extract logs
   kubectl logs -n openclaw deployment/openclaw-manager --all-containers > incident-logs.txt
   ```

2. **Investigation:**
   - Review audit logs for attack timeline
   - Check Hubble logs for data exfiltration attempts
   - Review workspace files for malicious content
   - Check if secrets were accessed

3. **Remediation:**
   - Rotate all secrets immediately via GitLab
   - Review and update security policies
   - Update agent instructions/prompts
   - Scale deployment back up with enhanced monitoring

4. **Post-Incident:**
   - Document findings
   - Update blocklist/alerting rules
   - Consider additional security layers
   - Review prompt engineering for vulnerabilities

## References

- [OpenClaw Documentation](https://docs.openclaw.ai)
- [Kubernetes Pod Security Standards](https://kubernetes.io/docs/concepts/security/pod-security-standards/)
- [Cilium Network Policy](https://docs.cilium.io/en/stable/security/policy/)
- [OWASP LLM Top 10](https://owasp.org/www-project-top-10-for-large-language-model-applications/)
