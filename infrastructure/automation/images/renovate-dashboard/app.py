#!/usr/bin/env python3
import datetime
import html
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

NAMESPACE = os.getenv("POD_NAMESPACE", "automation")
STATE_CM = os.getenv("RENOVATE_AGENT_STATE_CONFIGMAP", "renovate-agent-state")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN", "")
GITLAB_ENDPOINT = os.getenv("GITLAB_ENDPOINT", "https://gitlab.com/api/v4")
SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
SA_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
KUBE_HOST = os.getenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
KUBE_PORT = os.getenv("KUBERNETES_SERVICE_PORT", "443")
KUBE_API = f"https://{KUBE_HOST}:{KUBE_PORT}"
CACHE_SECONDS = int(os.getenv("RENOVATE_DASHBOARD_CACHE_SECONDS", "90"))

CACHE = {"expires": 0, "data": None}

def request(method, url, headers=None, body=None, timeout=25, context=None):
  data = json.dumps(body).encode("utf-8") if body is not None else None
  req_headers = {"Accept": "application/json", **(headers or {})}
  if data is not None:
    req_headers.setdefault("Content-Type", "application/json")
  req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
  with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
    raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}

def github(method, path):
  headers = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "homelab-renovate-dashboard",
  }
  return request(method, f"https://api.github.com{path}", headers)

def gitlab(method, path):
  headers = {
    "PRIVATE-TOKEN": GITLAB_TOKEN,
    "Accept": "application/json",
    "User-Agent": "homelab-renovate-dashboard",
  }
  return request(method, f"{GITLAB_ENDPOINT}{path}", headers)

def kube_headers():
  with open(SA_TOKEN_PATH, encoding="utf-8") as token_file:
    return {"Authorization": f"Bearer {token_file.read().strip()}"}

def kube_context():
  return ssl.create_default_context(cafile=SA_CA_PATH)

def kube_configmap(name):
  path = f"/api/v1/namespaces/{NAMESPACE}/configmaps/{name}"
  return request("GET", f"{KUBE_API}{path}", kube_headers(), context=kube_context())

def load_repo_file(path):
  repos = []
  if not os.path.exists(path):
    return repos
  with open(path, encoding="utf-8") as repo_file:
    for line in repo_file:
      line = line.strip()
      if not line or line.startswith("#"):
        continue
      left, right = line.split("|", 1)
      repos.append((left.strip(), right.strip()))
  return repos

def renovate_pr(pr):
  branch = pr.get("head", {}).get("ref", "") or pr.get("source_branch", "")
  title = (pr.get("title") or "").lower()
  user = pr.get("user", {}).get("login", "").lower()
  return branch.startswith("renovate/") or user.startswith("renovate") or "renovate" in title

def latest_state(state_data, owner, repo, number, sha=None):
  prefix = f"{owner}-{repo}-{number}-"
  candidates = []
  for key, raw in state_data.items():
    if not key.startswith(prefix):
      continue
    try:
      item = json.loads(raw)
    except Exception:
      continue
    if sha and item.get("sha") == sha:
      return item
    candidates.append(item)
  if not candidates:
    return {}
  return max(candidates, key=lambda item: item.get("processed_at", 0))

def checks_for(owner, repo, sha):
  failures = []
  pending = []
  successes = []
  try:
    statuses = github("GET", f"/repos/{owner}/{repo}/commits/{sha}/status").get("statuses", [])
    for status in statuses:
      name = status.get("context", "status")
      if status.get("state") == "success":
        successes.append(name)
      elif status.get("state") in ("pending", "expected"):
        pending.append(name)
      else:
        failures.append(f"{name}: {status.get('state')}")
  except Exception:
    pass
  try:
    runs = github("GET", f"/repos/{owner}/{repo}/commits/{sha}/check-runs?per_page=100").get("check_runs", [])
    for run in runs:
      name = run.get("name", "check")
      if run.get("status") != "completed":
        pending.append(name)
      elif run.get("conclusion") in ("success", "neutral", "skipped"):
        successes.append(name)
      else:
        failures.append(f"{name}: {run.get('conclusion')}")
  except Exception:
    pass
  if failures:
    state = "failure"
  elif pending:
    state = "pending"
  elif successes:
    state = "success"
  else:
    state = "unknown"
  return {"state": state, "failures": failures[:5], "pending": pending[:5], "successes": successes[:5]}

def bucket_for(pr, state_item, checks):
  action = state_item.get("action", "")
  decision = state_item.get("decision", {}) or {}
  approval = state_item.get("approval", {}) or {}
  if action == "repaired":
    return "repaired"
  if action in ("repair_failed", "repair_skipped", "merge_failed") or decision.get("decision") == "blocked":
    return "blocked"
  if decision.get("decision") == "wait" or checks["state"] in ("pending", "unknown"):
    return "waiting"
  if decision.get("requires_permission") and not approval.get("approved"):
    return "approval"
  if approval.get("approved"):
    return "approved"
  if checks["state"] == "success":
    return "green"
  return "unreviewed"

def github_status(state_data):
  rows = []
  merged_today = []
  now = datetime.datetime.now(ZoneInfo("America/New_York"))
  midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
  for owner, repo in load_repo_file("/config/renovate-agent-repositories.txt"):
    pulls = github("GET", f"/repos/{owner}/{repo}/pulls?state=open&per_page=100")
    for pr in pulls:
      if not renovate_pr(pr):
        continue
      sha = pr.get("head", {}).get("sha", "")
      state_item = latest_state(state_data, owner, repo, pr["number"], sha)
      checks = checks_for(owner, repo, sha)
      decision = state_item.get("decision", {}) or {}
      approval = state_item.get("approval", {}) or {}
      rows.append({
        "platform": "github",
        "repo": repo,
        "number": pr["number"],
        "title": pr.get("title"),
        "url": pr.get("html_url"),
        "sha": sha[:12],
        "branch": pr.get("head", {}).get("ref"),
        "bucket": bucket_for(pr, state_item, checks),
        "checks": checks["state"],
        "decision": decision.get("decision", "unreviewed"),
        "approval": approval.get("method") or "",
        "action": state_item.get("action", ""),
        "reason": decision.get("reason", ""),
        "processed_at": state_item.get("processed_at", 0),
      })
    closed = github("GET", f"/repos/{owner}/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=30")
    for pr in closed:
      merged_at = pr.get("merged_at")
      if not merged_at:
        continue
      merged_dt = datetime.datetime.fromisoformat(merged_at.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
      if merged_dt < midnight:
        continue
      if not renovate_pr(pr):
        continue
      merged_today.append({
        "platform": "github",
        "repo": repo,
        "number": pr["number"],
        "title": pr.get("title"),
        "url": pr.get("html_url"),
        "merged_at": merged_dt.isoformat(timespec="minutes"),
      })
  return rows, merged_today

def gitlab_status():
  rows = []
  merged_today = []
  now = datetime.datetime.now(ZoneInfo("America/New_York"))
  midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
  if not GITLAB_TOKEN:
    return rows, merged_today
  for namespace, project in load_repo_file("/config/renovate-agent-gitlab-infra-repositories.txt"):
    project_id = urllib.parse.quote(f"{namespace}/{project}", safe="")
    try:
      opened = gitlab("GET", f"/projects/{project_id}/merge_requests?state=opened&per_page=100")
      for mr in opened:
        if not renovate_pr(mr):
          continue
        rows.append({
          "platform": "gitlab",
          "repo": project,
          "number": mr.get("iid"),
          "title": mr.get("title"),
          "url": mr.get("web_url"),
          "sha": (mr.get("sha") or "")[:12],
          "branch": mr.get("source_branch"),
          "bucket": "approval",
          "checks": "unknown",
          "decision": "infra-review",
          "approval": "",
          "action": "",
          "reason": "Homelab infra Renovate MRs are approval-gated.",
          "processed_at": 0,
        })
      closed = gitlab("GET", f"/projects/{project_id}/merge_requests?state=merged&order_by=updated_at&sort=desc&per_page=30")
      for mr in closed:
        merged_at = mr.get("merged_at")
        if not merged_at:
          continue
        merged_dt = datetime.datetime.fromisoformat(merged_at.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
        if merged_dt < midnight:
          continue
        if renovate_pr(mr):
          merged_today.append({
            "platform": "gitlab",
            "repo": project,
            "number": mr.get("iid"),
            "title": mr.get("title"),
            "url": mr.get("web_url"),
            "merged_at": merged_dt.isoformat(timespec="minutes"),
          })
    except Exception as exc:
      rows.append({
        "platform": "gitlab",
        "repo": project,
        "number": "",
        "title": "GitLab scan failed",
        "url": "",
        "sha": "",
        "branch": "",
        "bucket": "blocked",
        "checks": "failure",
        "decision": "scan_failed",
        "approval": "",
        "action": "",
        "reason": str(exc),
        "processed_at": 0,
      })
  return rows, merged_today

def status_payload():
  now = time.time()
  if CACHE["data"] and CACHE["expires"] > now:
    return CACHE["data"]
  state_cm = kube_configmap(STATE_CM)
  state_data = state_cm.get("data", {})
  github_rows, github_merged = github_status(state_data)
  gitlab_rows, gitlab_merged = gitlab_status()
  rows = sorted(github_rows + gitlab_rows, key=lambda item: (item["bucket"], item["repo"], item["number"]))
  merged_today = sorted(github_merged + gitlab_merged, key=lambda item: item["merged_at"], reverse=True)
  counts = {}
  for row in rows:
    counts[row["bucket"]] = counts.get(row["bucket"], 0) + 1
  payload = {
    "generated_at": datetime.datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="seconds"),
    "counts": counts,
    "open": rows,
    "merged_today": merged_today,
  }
  CACHE["data"] = payload
  CACHE["expires"] = now + CACHE_SECONDS
  return payload

def page():
  return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Renovate Status</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #101114; color: #f4f4f5; }
    body { margin: 0; min-height: 100vh; background: #101114; }
    main { max-width: 1180px; margin: 0 auto; padding: 28px 18px 48px; }
    header { display: flex; justify-content: space-between; gap: 18px; align-items: end; margin-bottom: 24px; }
    h1 { font-size: 28px; line-height: 1.1; margin: 0; letter-spacing: 0; }
    .muted { color: #a1a1aa; font-size: 13px; }
    .grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 10px; margin-bottom: 20px; }
    .stat { border: 1px solid #27272a; background: #18181b; border-radius: 8px; padding: 12px; min-height: 62px; }
    .stat strong { display: block; font-size: 24px; }
    .stat span { color: #a1a1aa; font-size: 12px; text-transform: uppercase; }
    section { margin-top: 26px; }
    h2 { font-size: 16px; margin: 0 0 10px; }
    table { width: 100%; border-collapse: collapse; background: #18181b; border: 1px solid #27272a; border-radius: 8px; overflow: hidden; }
    th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid #27272a; vertical-align: top; font-size: 13px; }
    th { color: #a1a1aa; font-size: 11px; text-transform: uppercase; background: #1f1f23; }
    tr:last-child td { border-bottom: 0; }
    a { color: #93c5fd; text-decoration: none; }
    a:hover { text-decoration: underline; }
    .pill { display: inline-flex; align-items: center; border-radius: 999px; padding: 3px 8px; font-size: 12px; border: 1px solid #3f3f46; color: #e4e4e7; white-space: nowrap; }
    .approval { border-color: #f59e0b; color: #fbbf24; }
    .blocked { border-color: #ef4444; color: #f87171; }
    .waiting { border-color: #38bdf8; color: #67e8f9; }
    .repaired { border-color: #22c55e; color: #86efac; }
    .approved, .green { border-color: #84cc16; color: #bef264; }
    .reason { color: #a1a1aa; max-width: 440px; }
    .empty { color: #a1a1aa; background: #18181b; border: 1px solid #27272a; border-radius: 8px; padding: 16px; }
    @media (max-width: 760px) { header { display: block; } .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } table { display: block; overflow-x: auto; } }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Renovate Status</h1>
        <div class="muted" id="generated">Loading...</div>
      </div>
      <div class="muted">Auto-refreshes every 90s</div>
    </header>
    <div class="grid" id="stats"></div>
    <section>
      <h2>Open PRs</h2>
      <div id="open"></div>
    </section>
    <section>
      <h2>Merged Today</h2>
      <div id="merged"></div>
    </section>
  </main>
  <script>
    const labels = ["approval","blocked","waiting","repaired","approved","green","unreviewed"];
    function esc(value) { return String(value ?? "").replace(/[&<>"']/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c])); }
    function renderRows(rows) {
      if (!rows.length) return '<div class="empty">No open Renovate PRs.</div>';
      return `<table><thead><tr><th>State</th><th>Repo</th><th>PR</th><th>Checks</th><th>Decision</th><th>Reason</th></tr></thead><tbody>${rows.map(row => `
        <tr>
          <td><span class="pill ${esc(row.bucket)}">${esc(row.bucket)}</span></td>
          <td>${esc(row.repo)}<div class="muted">${esc(row.platform)} ${esc(row.sha)}</div></td>
          <td><a href="${esc(row.url)}">${esc(row.title)}</a><div class="muted">#${esc(row.number)} ${esc(row.branch)}</div></td>
          <td>${esc(row.checks)}</td>
          <td>${esc(row.decision)}<div class="muted">${esc(row.approval || row.action || "")}</div></td>
          <td class="reason">${esc(row.reason)}</td>
        </tr>`).join("")}</tbody></table>`;
    }
    function renderMerged(rows) {
      if (!rows.length) return '<div class="empty">No Renovate PRs merged today.</div>';
      return `<table><thead><tr><th>Repo</th><th>PR</th><th>Merged</th></tr></thead><tbody>${rows.map(row => `
        <tr>
          <td>${esc(row.repo)}<div class="muted">${esc(row.platform)}</div></td>
          <td><a href="${esc(row.url)}">${esc(row.title)}</a><div class="muted">#${esc(row.number)}</div></td>
          <td>${esc(row.merged_at)}</td>
        </tr>`).join("")}</tbody></table>`;
    }
    async function load() {
      const res = await fetch('/api/status', { cache: 'no-store' });
      const data = await res.json();
      document.getElementById('generated').textContent = `Generated ${data.generated_at}`;
      document.getElementById('stats').innerHTML = labels.map(label => `<div class="stat"><strong>${data.counts[label] || 0}</strong><span>${label}</span></div>`).join("");
      document.getElementById('open').innerHTML = renderRows(data.open);
      document.getElementById('merged').innerHTML = renderMerged(data.merged_today);
    }
    load();
    setInterval(load, 90000);
  </script>
</body>
</html>"""

class Handler(BaseHTTPRequestHandler):
  def do_GET(self):
    path = urllib.parse.urlparse(self.path).path
    if path == "/healthz":
      self.send_response(200)
      self.end_headers()
      self.wfile.write(b"ok")
      return
    if path == "/api/status":
      try:
        body = json.dumps(status_payload()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
      except Exception as exc:
        self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))
      return
    body = page().encode("utf-8")
    self.send_response(200)
    self.send_header("Content-Type", "text/html; charset=utf-8")
    self.send_header("Cache-Control", "no-store")
    self.end_headers()
    self.wfile.write(body)

  def log_message(self, fmt, *args):
    print(fmt % args, flush=True)

if __name__ == "__main__":
  port = int(os.getenv("PORT", "8080"))
  ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
