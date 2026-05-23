#!/usr/bin/env python3
import datetime
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
APPROVAL_LABEL = os.getenv("RENOVATE_AGENT_APPROVAL_LABEL", "renovate-agent-approved")
APPROVAL_COMMAND = os.getenv("RENOVATE_AGENT_APPROVAL_COMMAND", "/renovate-agent approve")
SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
SA_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
KUBE_HOST = os.getenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
KUBE_PORT = os.getenv("KUBERNETES_SERVICE_PORT", "443")
KUBE_API = f"https://{KUBE_HOST}:{KUBE_PORT}"
CACHE_SECONDS = int(os.getenv("RENOVATE_DASHBOARD_CACHE_SECONDS", "90"))

CACHE = {"expires": 0, "data": None}

class HttpError(RuntimeError):
  def __init__(self, status, body):
    super().__init__(f"HTTP {status}: {body}")
    self.status = status
    self.body = body

def request(method, url, headers=None, body=None, timeout=25, context=None):
  data = json.dumps(body).encode("utf-8") if body is not None else None
  req_headers = {"Accept": "application/json", **(headers or {})}
  if data is not None:
    req_headers.setdefault("Content-Type", "application/json")
  req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
  try:
    with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
      raw = resp.read().decode("utf-8")
      return json.loads(raw) if raw else {}
  except urllib.error.HTTPError as exc:
    raw = exc.read().decode("utf-8", errors="replace")
    raise HttpError(exc.code, raw) from exc

def github(method, path, body=None):
  headers = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "homelab-renovate-dashboard",
  }
  return request(method, f"https://api.github.com{path}", headers, body)

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

def github_allowlist():
  return {
    (owner.lower(), repo.lower())
    for owner, repo in load_repo_file("/config/renovate-agent-repositories.txt")
  }

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
        "owner": owner,
        "repo": repo,
        "number": pr["number"],
        "title": pr.get("title"),
        "url": pr.get("html_url"),
        "sha": sha,
        "short_sha": sha[:12],
        "branch": pr.get("head", {}).get("ref"),
        "bucket": bucket_for(pr, state_item, checks),
        "checks": checks["state"],
        "decision": decision.get("decision", "unreviewed"),
        "approval": approval.get("method") or "",
        "action": state_item.get("action", ""),
        "reason": decision.get("reason", ""),
        "processed_at": state_item.get("processed_at", 0),
        "can_approve": checks["state"] == "success" and not approval.get("approved"),
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
          "owner": namespace,
          "repo": project,
          "number": mr.get("iid"),
          "title": mr.get("title"),
          "url": mr.get("web_url"),
          "sha": (mr.get("sha") or "")[:12],
          "short_sha": (mr.get("sha") or "")[:12],
          "branch": mr.get("source_branch"),
          "bucket": "approval",
          "checks": "unknown",
          "decision": "infra-review",
          "approval": "",
          "action": "",
          "reason": "Homelab infra Renovate MRs are approval-gated.",
          "processed_at": 0,
          "can_approve": False,
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
        "owner": namespace,
        "repo": project,
        "number": "",
        "title": "GitLab scan failed",
        "url": "",
        "sha": "",
        "short_sha": "",
        "branch": "",
        "bucket": "blocked",
        "checks": "failure",
        "decision": "scan_failed",
        "approval": "",
        "action": "",
        "reason": str(exc),
        "processed_at": 0,
        "can_approve": False,
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

def approve_pr(body):
  owner = str(body.get("owner", "")).strip()
  repo = str(body.get("repo", "")).strip()
  number = int(body.get("number", 0))
  expected_sha = str(body.get("sha", "")).strip()
  if not owner or not repo or number <= 0 or not expected_sha:
    raise ValueError("owner, repo, number, and sha are required")
  if (owner.lower(), repo.lower()) not in github_allowlist():
    raise PermissionError("repository is not in the Renovate agent allowlist")

  pr = github("GET", f"/repos/{owner}/{repo}/pulls/{number}")
  if pr.get("state") != "open":
    raise ValueError("pull request is not open")
  current_sha = pr.get("head", {}).get("sha", "")
  if current_sha != expected_sha:
    raise ValueError("pull request head changed; refresh before approving")
  if not renovate_pr(pr):
    raise ValueError("pull request does not look like a Renovate PR")

  github("POST", f"/repos/{owner}/{repo}/issues/{number}/labels", {"labels": [APPROVAL_LABEL]})
  github("POST", f"/repos/{owner}/{repo}/issues/{number}/comments", {"body": APPROVAL_COMMAND})
  CACHE["expires"] = 0
  return {
    "ok": True,
    "message": f"Approved {owner}/{repo}#{number}. The reviewer will merge it on the next run once checks are green.",
  }

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
    .section-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
    h2 { font-size: 16px; margin: 0; }
    .controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
    select, input, button { border: 1px solid #3f3f46; background: #18181b; color: #f4f4f5; border-radius: 6px; font: inherit; font-size: 13px; min-height: 34px; }
    select, input { padding: 0 10px; }
    input { min-width: 220px; }
    button { padding: 0 10px; cursor: pointer; }
    button:hover:not(:disabled) { background: #27272a; }
    button:disabled { opacity: 0.55; cursor: default; }
    .primary { border-color: #2563eb; background: #1d4ed8; color: white; }
    .primary:hover:not(:disabled) { background: #2563eb; }
    .notice { color: #bbf7d0; background: #123022; border: 1px solid #166534; border-radius: 8px; padding: 10px 12px; margin-bottom: 12px; }
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
    .error { color: #fecaca; background: #3f1d22; border: 1px solid #7f1d1d; border-radius: 8px; padding: 16px; margin-bottom: 18px; }
    .actions { white-space: nowrap; }
    @media (max-width: 760px) { header { display: block; } .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } table { display: block; overflow-x: auto; } input { min-width: 100%; } }
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
      <div class="section-head">
        <h2>Open PRs</h2>
        <button id="refresh" type="button">Refresh</button>
      </div>
      <div class="controls">
        <select id="stateFilter"><option value="">All states</option></select>
        <select id="repoFilter"><option value="">All repos</option></select>
        <select id="platformFilter"><option value="">All platforms</option></select>
        <input id="searchFilter" type="search" placeholder="Search PRs">
      </div>
      <div id="notice"></div>
      <div id="open"></div>
    </section>
    <section>
      <h2>Merged Today</h2>
      <div id="merged"></div>
    </section>
  </main>
  <script>
    const labels = ["approval","blocked","waiting","repaired","approved","green","unreviewed"];
    const state = { open: [], merged_today: [], counts: {}, generated_at: "" };
    function esc(value) { return String(value ?? "").replace(/[&<>"']/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c])); }
    function unique(values) { return [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b)); }
    function fillSelect(id, values, label) {
      const select = document.getElementById(id);
      const current = select.value;
      select.innerHTML = `<option value="">${label}</option>` + values.map(value => `<option value="${esc(value)}">${esc(value)}</option>`).join("");
      if (values.includes(current)) select.value = current;
    }
    function showNotice(message, error = false) {
      const el = document.getElementById('notice');
      el.innerHTML = message ? `<div class="${error ? 'error' : 'notice'}">${esc(message)}</div>` : "";
    }
    function filteredRows() {
      const bucket = document.getElementById('stateFilter').value;
      const repo = document.getElementById('repoFilter').value;
      const platform = document.getElementById('platformFilter').value;
      const query = document.getElementById('searchFilter').value.trim().toLowerCase();
      return state.open.filter(row => {
        const haystack = `${row.repo} ${row.title} ${row.branch} ${row.reason}`.toLowerCase();
        return (!bucket || row.bucket === bucket)
          && (!repo || row.repo === repo)
          && (!platform || row.platform === platform)
          && (!query || haystack.includes(query));
      });
    }
    function render() {
      document.getElementById('generated').textContent = state.generated_at ? `Generated ${state.generated_at}` : 'Loading...';
      document.getElementById('stats').innerHTML = labels.map(label => `<div class="stat"><strong>${state.counts[label] || 0}</strong><span>${label}</span></div>`).join("");
      document.getElementById('open').innerHTML = renderRows(filteredRows());
      document.getElementById('merged').innerHTML = renderMerged(state.merged_today || []);
    }
    function renderRows(rows) {
      if (!rows.length) return '<div class="empty">No open Renovate PRs.</div>';
      return `<table><thead><tr><th>State</th><th>Repo</th><th>PR</th><th>Checks</th><th>Decision</th><th>Reason</th><th>Actions</th></tr></thead><tbody>${rows.map(row => `
        <tr>
          <td><span class="pill ${esc(row.bucket)}">${esc(row.bucket)}</span></td>
          <td>${esc(row.repo)}<div class="muted">${esc(row.platform)} ${esc(row.short_sha || row.sha)}</div></td>
          <td><a href="${esc(row.url)}">${esc(row.title)}</a><div class="muted">#${esc(row.number)} ${esc(row.branch)}</div></td>
          <td>${esc(row.checks)}</td>
          <td>${esc(row.decision)}<div class="muted">${esc(row.approval || row.action || "")}</div></td>
          <td class="reason">${esc(row.reason)}</td>
          <td class="actions">${row.can_approve ? `<button class="primary" type="button" data-owner="${esc(row.owner)}" data-repo="${esc(row.repo)}" data-number="${esc(row.number)}" data-sha="${esc(row.sha)}">Approve</button>` : ''}</td>
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
    async function approve(row, button) {
      button.disabled = true;
      button.textContent = 'Approving...';
      showNotice("");
      try {
        const res = await fetch('/api/approve', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(row),
        });
        const data = await res.json();
        if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
        showNotice(data.message || 'Approved.');
        await load();
      } catch (err) {
        showNotice(`Approval failed: ${err.message}`, true);
        button.disabled = false;
        button.textContent = 'Approve';
      }
    }
    async function load() {
      try {
        const res = await fetch('/api/status', { cache: 'no-store' });
        const data = await res.json();
        if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
        state.generated_at = data.generated_at;
        state.counts = data.counts || {};
        state.open = data.open || [];
        state.merged_today = data.merged_today || [];
        fillSelect('stateFilter', unique(state.open.map(row => row.bucket)), 'All states');
        fillSelect('repoFilter', unique(state.open.map(row => row.repo)), 'All repos');
        fillSelect('platformFilter', unique(state.open.map(row => row.platform)), 'All platforms');
        render();
      } catch (err) {
        document.getElementById('generated').textContent = 'Status unavailable';
        document.getElementById('stats').innerHTML = '';
        document.getElementById('open').innerHTML = `<div class="error">Dashboard API failed: ${esc(err.message)}</div>`;
        document.getElementById('merged').innerHTML = '';
      }
    }
    document.getElementById('open').addEventListener('click', event => {
      if (event.target.tagName !== 'BUTTON') return;
      const button = event.target;
      approve({
        owner: button.dataset.owner,
        repo: button.dataset.repo,
        number: button.dataset.number,
        sha: button.dataset.sha,
      }, button);
    });
    for (const id of ['stateFilter', 'repoFilter', 'platformFilter', 'searchFilter']) {
      document.getElementById(id).addEventListener('input', render);
    }
    document.getElementById('refresh').addEventListener('click', load);
    load();
    setInterval(load, 90000);
  </script>
</body>
</html>"""

class Handler(BaseHTTPRequestHandler):
  def send_json(self, status, payload):
    body = json.dumps(payload).encode("utf-8")
    self.send_response(status)
    self.send_header("Content-Type", "application/json")
    self.send_header("Cache-Control", "no-store")
    self.end_headers()
    self.wfile.write(body)

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

  def do_POST(self):
    path = urllib.parse.urlparse(self.path).path
    if path != "/api/approve":
      self.send_json(404, {"error": "not found"})
      return
    try:
      length = int(self.headers.get("Content-Length", "0"))
      raw = self.rfile.read(length).decode("utf-8") if length else "{}"
      result = approve_pr(json.loads(raw))
      self.send_json(200, result)
    except PermissionError as exc:
      self.send_json(403, {"error": str(exc)})
    except (ValueError, json.JSONDecodeError, HttpError) as exc:
      self.send_json(400, {"error": str(exc)})
    except Exception as exc:
      self.send_json(500, {"error": str(exc)})

  def log_message(self, fmt, *args):
    print(fmt % args, flush=True)

if __name__ == "__main__":
  port = int(os.getenv("PORT", "8080"))
  ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
