#!/usr/bin/env python3
import hashlib
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

NAMESPACE = os.getenv("POD_NAMESPACE", "automation")
STATE_CM = os.getenv("RENOVATE_AGENT_STATE_CONFIGMAP", "renovate-agent-state")
STATE_RETENTION_DAYS = int(os.getenv("RENOVATE_AGENT_STATE_RETENTION_DAYS", "30"))
APPROVAL_COMMAND = os.getenv("RENOVATE_AGENT_APPROVAL_COMMAND", "/renovate-agent approve")
APPROVERS = {
  item.strip().lower()
  for item in os.getenv("RENOVATE_AGENT_APPROVERS", "rupan,jeff7712").split(",")
  if item.strip()
}
FORGEJO_TOKEN = os.getenv("FORGEJO_TOKEN", "")
FORGEJO_ENDPOINT = os.getenv("FORGEJO_ENDPOINT", "https://git.rupan.dev/api/v1")
MAX_NTFY_MESSAGE_LENGTH = 3500
TOPIC = open("/config/ntfy-topic", encoding="utf-8").read().strip()
NTFY_URL = f"http://ntfy-ntfy.observability.svc.cluster.local/{TOPIC}"
SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
SA_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
KUBE_HOST = os.getenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
KUBE_PORT = os.getenv("KUBERNETES_SERVICE_PORT", "443")
KUBE_API = f"https://{KUBE_HOST}:{KUBE_PORT}"

class HttpError(RuntimeError):
  def __init__(self, status, body):
    super().__init__(f"HTTP {status}: {body}")
    self.status = status
    self.body = body

def request(method, url, headers=None, body=None, timeout=30, context=None):
  data = None
  if body is not None:
    data = json.dumps(body).encode("utf-8")
  req_headers = {"Accept": "application/json", **(headers or {})}
  if data is not None and "Content-Type" not in req_headers:
    req_headers["Content-Type"] = "application/json"
  req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
  try:
    with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
      raw = resp.read().decode("utf-8")
      return json.loads(raw) if raw else {}
  except urllib.error.HTTPError as exc:
    raw = exc.read().decode("utf-8", errors="replace")
    raise HttpError(exc.code, raw) from exc

def forgejo(method, path, body=None):
  if not FORGEJO_TOKEN:
    raise RuntimeError("FORGEJO_TOKEN is required")
  return request(method, f"{FORGEJO_ENDPOINT}{path}", {"Authorization": f"token {FORGEJO_TOKEN}"}, body)

def kube_context():
  return ssl.create_default_context(cafile=SA_CA_PATH)

def kube_headers():
  with open(SA_TOKEN_PATH, encoding="utf-8") as token_file:
    token = token_file.read().strip()
  return {"Authorization": f"Bearer {token}"}

def ensure_state():
  path = f"/api/v1/namespaces/{NAMESPACE}/configmaps/{STATE_CM}"
  try:
    return request("GET", f"{KUBE_API}{path}", kube_headers(), context=kube_context())
  except HttpError as exc:
    if exc.status != 404:
      raise
  body = {
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": {"name": STATE_CM, "namespace": NAMESPACE},
    "data": {"initialized": "true"},
  }
  return request("POST", f"{KUBE_API}/api/v1/namespaces/{NAMESPACE}/configmaps", kube_headers(), body, context=kube_context())

def patch_state(key, value):
  safe_key = re.sub(r"[^A-Za-z0-9._-]", "-", key)
  path = f"/api/v1/namespaces/{NAMESPACE}/configmaps/{STATE_CM}"
  headers = {**kube_headers(), "Content-Type": "application/merge-patch+json"}
  body = {"data": {safe_key: value}}
  request("PATCH", f"{KUBE_API}{path}", headers, body, context=kube_context())

def cleanup_state(state):
  if STATE_RETENTION_DAYS <= 0:
    return
  cutoff = int(time.time()) - (STATE_RETENTION_DAYS * 86400)
  removals = {}
  for key, raw in (state.get("data") or {}).items():
    if key == "initialized":
      continue
    try:
      item = json.loads(raw)
    except Exception:
      continue
    processed_at = int(item.get("processed_at") or 0)
    if processed_at and processed_at < cutoff:
      removals[key] = None
  if not removals:
    return
  path = f"/api/v1/namespaces/{NAMESPACE}/configmaps/{STATE_CM}"
  headers = {**kube_headers(), "Content-Type": "application/merge-patch+json"}
  request("PATCH", f"{KUBE_API}{path}", headers, {"data": removals}, context=kube_context())
  for key in removals:
    state.setdefault("data", {}).pop(key, None)
  print(f"cleaned {len(removals)} renovate state entries older than {STATE_RETENTION_DAYS} days")

def notify(title, priority, message, actions=None, click=None):
  if len(message) > MAX_NTFY_MESSAGE_LENGTH:
    message = message[:MAX_NTFY_MESSAGE_LENGTH - 32].rstrip() + "\n[message truncated]"
  headers = {
    "Authorization": f"Bearer {os.environ['NTFY_TOKEN']}",
    "X-Title": title,
    "X-Priority": priority,
  }
  if click:
    headers["Click"] = click
  if actions:
    headers["Actions"] = actions
  data = message.encode("utf-8")
  url = NTFY_URL
  req = urllib.request.Request(url, data=data, headers=headers, method="POST")
  try:
    with urllib.request.urlopen(req, timeout=20) as resp:
      resp.read()
  except Exception as exc:
    print(f"warning: ntfy notification failed: {exc}", file=sys.stderr)

def notification_dedupe_key(*parts):
  raw = "|".join(str(part or "") for part in parts)
  digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
  label = "-".join(str(part or "") for part in parts[:4])
  return f"notification-{label}-{digest}"

def notify_once(state, key, title, priority, message, actions=None, click=None):
  if state.get("data", {}).get(key):
    print(f"notification suppressed for repeated issue: {key}")
    return
  notify(title, priority, message, actions=actions, click=click)
  value = json.dumps({
    "title": title,
    "message": message[:500],
    "processed_at": int(time.time()),
  }, sort_keys=True)
  patch_state(key, value)
  state.setdefault("data", {})[re.sub(r"[^A-Za-z0-9._-]", "-", key)] = value

def load_forgejo_infra_repos():
  path = "/config/renovate-agent-forgejo-infra-repositories.txt"
  repos = []
  if not os.path.exists(path):
    return repos
  with open(path, encoding="utf-8") as repo_file:
    for line in repo_file:
      line = line.strip()
      if not line or line.startswith("#"):
        continue
      namespace, project = line.split("|", 1)
      repos.append((namespace.strip(), project.strip()))
  return repos

def renovate_pr(pr):
  branch = pr.get("head", {}).get("ref", "")
  labels = {label.get("name", "").lower() for label in pr.get("labels", [])}
  user = pr.get("user", {}).get("login", "").lower()
  title = pr.get("title", "").lower()
  return (
    branch.startswith("renovate/")
    or "renovate" in labels
    or user.startswith("renovate")
    or "renovate" in title
  )

def process_forgejo_pr(owner, repo, pr, state):
  number = pr["number"]
  sha = pr.get("head", {}).get("sha", "")
  key = f"forgejo-{owner}-{repo}-{number}-{sha[:12]}"
  if not sha:
    raise RuntimeError("Pull request has no source commit")
  statuses = forgejo("GET", f"/repos/{owner}/{repo}/commits/{sha}/status")
  # Combined status contains the latest result for each context. Missing
  # checks must wait, including the policy check on a newly rebased head.
  required = ("ci/woodpecker/validation-v2", "policy/merge")
  checks = {item.get("context"): item.get("status", "unknown")
    for item in (statuses.get("statuses") or [])}
  required_checks = {name: checks.get(name, "unknown") for name in required}
  checks_state = "success" if all(value == "success" for value in required_checks.values()) else "waiting"
  # This only selects reporting ownership, never grants merge permission.
  # Renovate and branch protection own automatic merges; this reviewer
  # retains exact-head human approval for its own merges.
  native_automerge = (
    pr.get("user", {}).get("login", "").lower() == "homelab-renovate"
    and "**Automerge**: Enabled." in (pr.get("body") or "")
  )
  comments = forgejo("GET", f"/repos/{owner}/{repo}/issues/{number}/comments?limit=100")
  approval = next((comment for comment in reversed(comments)
    if comment.get("user", {}).get("login", "").lower() in APPROVERS
    and comment.get("body", "").strip().lower() == f"{APPROVAL_COMMAND} {sha}".lower()), None)
  existing = json.loads(state.get("data", {}).get(key, "{}"))
  if existing.get("action") == "merged":
    return
  action = "waiting"
  if checks_state == "success" and approval and not native_automerge:
    forgejo("POST", f"/repos/{owner}/{repo}/pulls/{number}/merge", {"Do": "merge", "head_commit_id": sha, "delete_branch_after_merge": True})
    action = "merged"
  if (existing.get("required_checks") == required_checks
      and existing.get("native_automerge") == native_automerge
      and existing.get("action") == action):
    return
  guidance = (
    "Native automerge enabled; Renovate merges after required checks pass."
    if native_automerge else f"Approve this exact commit in the PR: {APPROVAL_COMMAND} {sha}"
  )
  check_summary = ", ".join(f"{name}: {value}" for name, value in required_checks.items())
  notify(f"Renovate Forgejo: {repo}#{number}", "default" if action == "merged" or native_automerge else "high",
    f"{pr.get('title', '')}\nchecks: {check_summary}\naction: {action}\n{guidance}", click=pr.get("html_url"))
  patch_state(key, json.dumps({"platform": "forgejo", "repo": f"{owner}/{repo}", "number": number, "sha": sha, "checks": checks_state, "required_checks": required_checks, "native_automerge": native_automerge, "approval": {"approved": bool(approval), "method": "comment" if approval else ""}, "action": action, "processed_at": int(time.time())}, sort_keys=True))

def main():
  state = ensure_state()
  cleanup_state(state)
  found = 0
  for owner, repo in load_forgejo_infra_repos():
    try:
      for pr in forgejo("GET", f"/repos/{owner}/{repo}/pulls?state=open&limit=100"):
        if not renovate_pr(pr):
          continue
        found += 1
        process_forgejo_pr(owner, repo, pr, state)
    except Exception as exc:
      notify_once(state, notification_dedupe_key("forgejo-repo", owner, repo, exc),
        f"Renovate Forgejo scan failed: {repo}", "high", f"{owner}/{repo}: {exc}")
  print(f"Renovate agent complete: found={found}")

if __name__ == "__main__":
  main()
