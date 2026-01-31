#!/bin/bash
# Security monitoring script for OpenClaw
# Run this periodically (e.g., via cron) or on-demand to check for suspicious activity

set -euo pipefail

NAMESPACE="openclaw"
DEPLOYMENT="openclaw-manager"
ALERT_WEBHOOK="${ALERT_WEBHOOK:-}"  # Optional: webhook URL for alerts

echo "=== OpenClaw Security Monitor ==="
echo "Timestamp: $(date -Iseconds)"
echo ""

# Function to send alert
alert() {
    local severity="$1"
    local message="$2"
    echo "[$severity] $message"
    
    if [ -n "$ALERT_WEBHOOK" ]; then
        curl -X POST "$ALERT_WEBHOOK" \
            -H "Content-Type: application/json" \
            -d "{\"severity\": \"$severity\", \"message\": \"$message\", \"timestamp\": \"$(date -Iseconds)\"}" \
            2>/dev/null || true
    fi
}

# Check if deployment is running
echo "1. Checking deployment status..."
if ! kubectl get deployment -n "$NAMESPACE" "$DEPLOYMENT" &>/dev/null; then
    alert "CRITICAL" "OpenClaw deployment not found!"
    exit 1
fi

REPLICAS=$(kubectl get deployment -n "$NAMESPACE" "$DEPLOYMENT" -o jsonpath='{.status.availableReplicas}')
if [ "${REPLICAS:-0}" -eq 0 ]; then
    alert "CRITICAL" "OpenClaw has no available replicas!"
    exit 1
fi
echo "✓ Deployment healthy ($REPLICAS replicas)"

# Check for suspicious exec patterns in logs (last 1 hour)
echo ""
echo "2. Scanning for suspicious exec patterns..."
SUSPICIOUS_PATTERNS=(
    "cat /secrets"
    "echo \$.*KEY"
    "echo \$.*TOKEN"
    "curl.*http://.*\$"
    "wget.*http"
    "nc -l"
    "netcat"
    "base64 -d"
    "/bin/sh -c.*curl"
    "eval.*\$"
)

for pattern in "${SUSPICIOUS_PATTERNS[@]}"; do
    if kubectl logs -n "$NAMESPACE" deployment/"$DEPLOYMENT" --since=1h 2>/dev/null | grep -iE "$pattern" > /dev/null; then
        alert "WARNING" "Suspicious pattern detected: $pattern"
    fi
done
echo "✓ Pattern scan complete"

# Check resource usage
echo ""
echo "3. Checking resource usage..."
POD=$(kubectl get pod -n "$NAMESPACE" -l app=openclaw-manager -o jsonpath='{.items[0].metadata.name}')

if [ -n "$POD" ]; then
    # Get resource metrics (requires metrics-server)
    if kubectl top pod -n "$NAMESPACE" "$POD" &>/dev/null; then
        CPU=$(kubectl top pod -n "$NAMESPACE" "$POD" --no-headers | awk '{print $2}')
        MEM=$(kubectl top pod -n "$NAMESPACE" "$POD" --no-headers | awk '{print $3}')
        echo "Current usage: CPU=$CPU, Memory=$MEM"
        
        # Alert if CPU > 1.5 cores or Memory > 3Gi (close to limits)
        CPU_NUM=$(echo "$CPU" | sed 's/m//')
        if [ "$CPU_NUM" -gt 1500 ]; then
            alert "WARNING" "High CPU usage: $CPU (limit: 2 cores)"
        fi
    else
        echo "⚠ metrics-server not available, skipping resource check"
    fi
fi

# Check for new network connections (requires Hubble)
echo ""
echo "4. Checking network activity..."
if command -v hubble &>/dev/null; then
    echo "Unique domains accessed in last hour:"
    hubble observe --namespace "$NAMESPACE" --since 1h 2>/dev/null | \
        grep -oE 'to-fqdn: [^ ]+' | \
        awk '{print $2}' | \
        sort -u | \
        while read -r domain; do
            case "$domain" in
                api.anthropic.com|api.telegram.org|moltbook.com|www.moltbook.com|gitlab.com)
                    echo "  ✓ $domain (expected)"
                    ;;
                *)
                    alert "INFO" "Unexpected domain accessed: $domain"
                    echo "  ⚠ $domain (unexpected)"
                    ;;
            esac
        done
else
    echo "⚠ Hubble CLI not available, skipping network check"
fi

# Check audit log for rate anomalies
echo ""
echo "5. Checking audit log for anomalies..."
if kubectl exec -n "$NAMESPACE" deployment/"$DEPLOYMENT" -- test -f /home/node/.openclaw/audit.log 2>/dev/null; then
    EXEC_COUNT=$(kubectl exec -n "$NAMESPACE" deployment/"$DEPLOYMENT" -- \
        sh -c 'tail -n 1000 /home/node/.openclaw/audit.log | grep -c "\"tool\":\"exec\"" || true' 2>/dev/null)
    HTTP_COUNT=$(kubectl exec -n "$NAMESPACE" deployment/"$DEPLOYMENT" -- \
        sh -c 'tail -n 1000 /home/node/.openclaw/audit.log | grep -c "\"tool\":\"http\"" || true' 2>/dev/null)
    
    echo "Recent activity (last 1000 log entries):"
    echo "  Exec calls: $EXEC_COUNT"
    echo "  HTTP calls: $HTTP_COUNT"
    
    # Alert if unusually high (>100 execs or >200 HTTP in recent logs)
    if [ "${EXEC_COUNT:-0}" -gt 100 ]; then
        alert "WARNING" "High exec activity: $EXEC_COUNT calls in recent logs"
    fi
    if [ "${HTTP_COUNT:-0}" -gt 200 ]; then
        alert "WARNING" "High HTTP activity: $HTTP_COUNT calls in recent logs"
    fi
else
    echo "⚠ Audit log not found"
fi

# Check for security audit alerts
echo ""
echo "6. Checking security audit log..."
if kubectl exec -n "$NAMESPACE" deployment/"$DEPLOYMENT" -- test -f /home/node/.openclaw/security-audit.log 2>/dev/null; then
    ALERT_COUNT=$(kubectl exec -n "$NAMESPACE" deployment/"$DEPLOYMENT" -- \
        sh -c 'wc -l < /home/node/.openclaw/security-audit.log' 2>/dev/null || echo "0")
    
    if [ "${ALERT_COUNT:-0}" -gt 0 ]; then
        alert "CRITICAL" "Security audit log has $ALERT_COUNT entries!"
        echo "Recent alerts:"
        kubectl exec -n "$NAMESPACE" deployment/"$DEPLOYMENT" -- \
            tail -n 10 /home/node/.openclaw/security-audit.log 2>/dev/null || true
    else
        echo "✓ No security alerts"
    fi
else
    echo "⚠ Security audit log not found"
fi

echo ""
echo "=== Security scan complete ==="
