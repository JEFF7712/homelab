#!/usr/bin/env python3
import datetime
import json
import os
import secrets
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
REVIEWER_CRONJOB = os.getenv("RENOVATE_REVIEWER_CRONJOB", "renovate-major-reviewer")
MANUAL_JOB_TTL_SECONDS = int(os.getenv("RENOVATE_MANUAL_JOB_TTL_SECONDS", "3600"))
ACTION_TOKEN = os.getenv("RENOVATE_DASHBOARD_ACTION_TOKEN") or secrets.token_urlsafe(32)

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

def request_text(method, url, headers=None, body=None, timeout=25, context=None):
  data = json.dumps(body).encode("utf-8") if body is not None else None
  req_headers = {"Accept": "*/*", **(headers or {})}
  if data is not None:
    req_headers.setdefault("Content-Type", "application/json")
  req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
  try:
    with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
      return resp.read().decode("utf-8", errors="replace")
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

def gitlab(method, path, body=None):
  headers = {
    "PRIVATE-TOKEN": GITLAB_TOKEN,
    "Accept": "application/json",
    "User-Agent": "homelab-renovate-dashboard",
  }
  return request(method, f"{GITLAB_ENDPOINT}{path}", headers, body)

def kube_headers():
  with open(SA_TOKEN_PATH, encoding="utf-8") as token_file:
    return {"Authorization": f"Bearer {token_file.read().strip()}"}

def kube_context():
  return ssl.create_default_context(cafile=SA_CA_PATH)

def kube_configmap(name):
  path = f"/api/v1/namespaces/{NAMESPACE}/configmaps/{name}"
  return request("GET", f"{KUBE_API}{path}", kube_headers(), context=kube_context())

def kube(path):
  return request("GET", f"{KUBE_API}{path}", kube_headers(), context=kube_context())

def kube_post(path, body):
  return request("POST", f"{KUBE_API}{path}", kube_headers(), body, context=kube_context())

def kube_text(path):
  return request_text("GET", f"{KUBE_API}{path}", kube_headers(), context=kube_context())

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

def gitlab_allowlist():
  return {
    (namespace.lower(), project.lower())
    for namespace, project in load_repo_file("/config/renovate-agent-gitlab-infra-repositories.txt")
  }

def renovate_pr(pr):
  branch = pr.get("head", {}).get("ref", "") or pr.get("source_branch", "")
  title = (pr.get("title") or "").lower()
  user = pr.get("user", {}).get("login", "").lower()
  return branch.startswith("renovate/") or user.startswith("renovate") or "renovate" in title

def parse_time(value):
  if not value:
    return None
  return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))

def iso_time(value):
  if not value:
    return ""
  return value.isoformat(timespec="seconds")

def day_key(value):
  return value.astimezone(ZoneInfo("America/New_York")).date().isoformat()

def human_duration(seconds):
  seconds = max(0, int(seconds))
  if seconds < 60:
    return f"{seconds}s"
  minutes, seconds = divmod(seconds, 60)
  if minutes < 60:
    return f"{minutes}m {seconds}s"
  hours, minutes = divmod(minutes, 60)
  return f"{hours}h {minutes}m"

def next_cron_time(schedule):
  parts = schedule.split()
  if len(parts) != 5:
    return ""
  minute, hour, day, month, weekday = parts
  if hour != "*" or day != "*" or month != "*" or weekday != "*" or not minute.isdigit():
    return ""
  minute_value = int(minute)
  if minute_value < 0 or minute_value > 59:
    return ""
  now = datetime.datetime.now(ZoneInfo("America/New_York"))
  next_run = now.replace(minute=minute_value, second=0, microsecond=0)
  if next_run <= now:
    next_run += datetime.timedelta(hours=1)
  return next_run.isoformat(timespec="seconds")

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

def latest_gitlab_state(state_data, namespace, project, number, sha=None):
  prefix = f"gitlab-{namespace}-{project}-{number}-"
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

def gitlab_checks(project_id, iid):
  pipelines = gitlab("GET", f"/projects/{project_id}/merge_requests/{iid}/pipelines?per_page=10")
  if not pipelines:
    return {"state": "unknown", "failures": [], "pending": [], "successes": []}
  status = pipelines[0].get("status")
  if status == "success":
    state = "success"
  elif status in ("pending", "running", "created", "waiting_for_resource", "preparing"):
    state = "pending"
  elif status in ("failed", "canceled", "skipped", "manual"):
    state = "failure"
  else:
    state = "unknown"
  return {
    "state": state,
    "failures": [pipelines[0].get("web_url", "pipeline")] if state == "failure" else [],
    "pending": [pipelines[0].get("web_url", "pipeline")] if state == "pending" else [],
    "successes": [pipelines[0].get("web_url", "pipeline")] if state == "success" else [],
  }

def merge_status(owner, repo, number):
  pr = github("GET", f"/repos/{owner}/{repo}/pulls/{number}")
  mergeable = pr.get("mergeable")
  mergeable_state = pr.get("mergeable_state") or "unknown"
  if mergeable is None and mergeable_state == "unknown":
    time.sleep(1)
    pr = github("GET", f"/repos/{owner}/{repo}/pulls/{number}")
    mergeable = pr.get("mergeable")
    mergeable_state = pr.get("mergeable_state") or "unknown"
  return {
    "mergeable": mergeable,
    "mergeable_state": mergeable_state,
    "has_conflicts": mergeable is False and mergeable_state == "dirty",
  }

def bucket_for(pr, state_item, checks, merge):
  action = state_item.get("action", "")
  decision = state_item.get("decision", {}) or {}
  approval = state_item.get("approval", {}) or {}
  if merge.get("has_conflicts"):
    return "conflict"
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
  merged_recent = []
  now = datetime.datetime.now(ZoneInfo("America/New_York"))
  midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
  since = now - datetime.timedelta(days=7)
  for owner, repo in load_repo_file("/config/renovate-agent-repositories.txt"):
    pulls = github("GET", f"/repos/{owner}/{repo}/pulls?state=open&per_page=100")
    for pr in pulls:
      if not renovate_pr(pr):
        continue
      sha = pr.get("head", {}).get("sha", "")
      state_item = latest_state(state_data, owner, repo, pr["number"], sha)
      checks = checks_for(owner, repo, sha)
      merge = merge_status(owner, repo, pr["number"])
      decision = state_item.get("decision", {}) or {}
      approval = state_item.get("approval", {}) or {}
      reason = decision.get("reason", "")
      display_decision = decision.get("decision", "unreviewed")
      if merge["has_conflicts"]:
        display_decision = "merge_conflict"
        reason = "GitHub reports this PR has merge conflicts with the base branch."
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
        "bucket": bucket_for(pr, state_item, checks, merge),
        "checks": checks["state"],
        "mergeable": merge["mergeable"],
        "mergeable_state": merge["mergeable_state"],
        "decision": display_decision,
        "approval": approval.get("method") or "",
        "action": state_item.get("action", ""),
        "reason": reason,
        "processed_at": state_item.get("processed_at", 0),
        "can_approve": checks["state"] == "success" and not approval.get("approved") and not merge["has_conflicts"],
      })
    closed = github("GET", f"/repos/{owner}/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=100")
    for pr in closed:
      merged_at = pr.get("merged_at")
      if not merged_at:
        continue
      merged_dt = datetime.datetime.fromisoformat(merged_at.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
      if merged_dt < since:
        continue
      if not renovate_pr(pr):
        continue
      merged_recent.append({
        "platform": "github",
        "repo": repo,
        "number": pr["number"],
        "title": pr.get("title"),
        "url": pr.get("html_url"),
        "merged_at": merged_dt.isoformat(timespec="minutes"),
        "is_today": merged_dt >= midnight,
      })
  return rows, merged_recent

def gitlab_status(state_data):
  rows = []
  merged_recent = []
  now = datetime.datetime.now(ZoneInfo("America/New_York"))
  midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
  since = now - datetime.timedelta(days=7)
  if not GITLAB_TOKEN:
    return rows, merged_recent
  for namespace, project in load_repo_file("/config/renovate-agent-gitlab-infra-repositories.txt"):
    project_id = urllib.parse.quote(f"{namespace}/{project}", safe="")
    try:
      opened = gitlab("GET", f"/projects/{project_id}/merge_requests?state=opened&per_page=100")
      for mr in opened:
        if not renovate_pr(mr):
          continue
        sha = mr.get("sha") or ""
        state_item = latest_gitlab_state(state_data, namespace, project, mr.get("iid"), sha)
        decision = state_item.get("decision", {}) or {}
        approval = state_item.get("approval", {}) or {}
        checks = gitlab_checks(project_id, mr.get("iid"))
        labels = {label.lower() for label in mr.get("labels", [])}
        approved = approval.get("approved") or APPROVAL_LABEL.lower() in labels
        merge_state = mr.get("detailed_merge_status") or mr.get("merge_status") or "unknown"
        has_conflicts = merge_state in ("conflict", "cannot_be_merged")
        bucket = "approved" if approved else "approval"
        if has_conflicts:
          bucket = "conflict"
        elif checks["state"] in ("pending", "unknown"):
          bucket = "waiting"
        elif checks["state"] == "failure":
          bucket = "blocked"
        rows.append({
          "platform": "gitlab",
          "owner": namespace,
          "repo": project,
          "number": mr.get("iid"),
          "title": mr.get("title"),
          "url": mr.get("web_url"),
          "sha": sha,
          "short_sha": sha[:12],
          "branch": mr.get("source_branch"),
          "bucket": bucket,
          "checks": checks["state"],
          "mergeable": None,
          "mergeable_state": merge_state,
          "decision": decision.get("decision", "infra-review"),
          "approval": approval.get("method") or (f"label:{APPROVAL_LABEL}" if approved else ""),
          "action": state_item.get("action", ""),
          "reason": decision.get("reason", "Homelab infra Renovate MRs are approval-gated."),
          "processed_at": state_item.get("processed_at", 0),
          "can_approve": checks["state"] == "success" and not approved and not has_conflicts,
        })
      closed = gitlab("GET", f"/projects/{project_id}/merge_requests?state=merged&order_by=updated_at&sort=desc&per_page=100")
      for mr in closed:
        merged_at = mr.get("merged_at")
        if not merged_at:
          continue
        merged_dt = datetime.datetime.fromisoformat(merged_at.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
        if merged_dt < since:
          continue
        if renovate_pr(mr):
          merged_recent.append({
            "platform": "gitlab",
            "repo": project,
            "number": mr.get("iid"),
            "title": mr.get("title"),
            "url": mr.get("web_url"),
            "merged_at": merged_dt.isoformat(timespec="minutes"),
            "is_today": merged_dt >= midnight,
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
        "mergeable": None,
        "mergeable_state": "unknown",
        "decision": "scan_failed",
        "approval": "",
        "action": "",
        "reason": str(exc),
        "processed_at": 0,
        "can_approve": False,
      })
  return rows, merged_recent

def reviewer_run_status():
  try:
    cron = kube(f"/apis/batch/v1/namespaces/{NAMESPACE}/cronjobs/{REVIEWER_CRONJOB}")
    jobs = kube(f"/apis/batch/v1/namespaces/{NAMESPACE}/jobs").get("items", [])
    jobs = [job for job in jobs if job.get("metadata", {}).get("name", "").startswith(f"{REVIEWER_CRONJOB}-")]
    jobs.sort(key=lambda job: job.get("status", {}).get("startTime") or job.get("metadata", {}).get("creationTimestamp") or "", reverse=True)
    latest = jobs[0] if jobs else {}
    status = latest.get("status", {}) if latest else {}
    start = parse_time(status.get("startTime") or latest.get("metadata", {}).get("creationTimestamp"))
    completed = parse_time(status.get("completionTime"))
    if status.get("active", 0):
      state = "running"
    elif status.get("succeeded", 0):
      state = "succeeded"
    elif status.get("failed", 0):
      state = "failed"
    else:
      state = "unknown"
    end = completed or datetime.datetime.now(ZoneInfo("America/New_York"))
    duration = human_duration((end - start).total_seconds()) if start else ""
    log_tail = []
    job_name = latest.get("metadata", {}).get("name", "")
    if job_name:
      selector = urllib.parse.quote(f"job-name={job_name}", safe="")
      pods = kube(f"/api/v1/namespaces/{NAMESPACE}/pods?labelSelector={selector}").get("items", [])
      if pods:
        pod_name = pods[0].get("metadata", {}).get("name", "")
        log_path = f"/api/v1/namespaces/{NAMESPACE}/pods/{pod_name}/log?tailLines=12"
        log_tail = [line for line in kube_text(log_path).splitlines() if line.strip()][-8:]
    schedule = cron.get("spec", {}).get("schedule", "")
    last_success = parse_time(cron.get("status", {}).get("lastSuccessfulTime"))
    stale = True
    if last_success:
      stale = (datetime.datetime.now(ZoneInfo("America/New_York")) - last_success) > datetime.timedelta(hours=3)
    return {
      "name": REVIEWER_CRONJOB,
      "schedule": schedule,
      "suspended": cron.get("spec", {}).get("suspend", False),
      "last_schedule_time": iso_time(parse_time(cron.get("status", {}).get("lastScheduleTime"))),
      "last_successful_time": iso_time(last_success),
      "next_schedule_time": next_cron_time(schedule),
      "stale": stale,
      "latest_job": job_name,
      "state": state,
      "started_at": iso_time(start),
      "completed_at": iso_time(completed),
      "duration": duration,
      "log_tail": log_tail,
    }
  except Exception as exc:
    return {
      "name": REVIEWER_CRONJOB,
      "state": "unavailable",
      "error": str(exc),
      "log_tail": [],
    }

def trigger_reviewer():
  cron = kube(f"/apis/batch/v1/namespaces/{NAMESPACE}/cronjobs/{REVIEWER_CRONJOB}")
  job_template = cron.get("spec", {}).get("jobTemplate", {})
  job_spec = job_template.get("spec")
  if not job_spec:
    raise ValueError(f"CronJob {REVIEWER_CRONJOB} does not have a job template")
  timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M%S")
  body = {
    "apiVersion": "batch/v1",
    "kind": "Job",
    "metadata": {
      "name": f"{REVIEWER_CRONJOB}-manual-{timestamp}",
      "namespace": NAMESPACE,
      "labels": {
        "app.kubernetes.io/name": REVIEWER_CRONJOB,
        "renovate-dashboard.rupan.dev/manual": "true",
      },
      "annotations": {
        "cronjob.kubernetes.io/instantiate": "manual",
      },
    },
    "spec": {**job_spec, "ttlSecondsAfterFinished": MANUAL_JOB_TTL_SECONDS},
  }
  job = kube_post(f"/apis/batch/v1/namespaces/{NAMESPACE}/jobs", body)
  CACHE["expires"] = 0
  return {
    "ok": True,
    "job": job.get("metadata", {}).get("name", body["metadata"]["name"]),
    "message": f"Started reviewer job {job.get('metadata', {}).get('name', body['metadata']['name'])}.",
  }

def activity_history(state_data, merged_recent):
  now = datetime.datetime.now(ZoneInfo("America/New_York"))
  since_ts = int((now - datetime.timedelta(days=7)).timestamp())
  daily = {}
  recent_actions = []
  for item in merged_recent:
    merged = parse_time(item.get("merged_at"))
    if not merged:
      continue
    key = day_key(merged)
    daily.setdefault(key, {"date": key, "merged": 0, "repaired": 0, "blocked": 0, "approval": 0})
    daily[key]["merged"] += 1
  for raw in state_data.values():
    try:
      item = json.loads(raw)
    except Exception:
      continue
    if not isinstance(item, dict):
      continue
    processed_at = int(item.get("processed_at") or 0)
    if processed_at < since_ts:
      continue
    processed_dt = datetime.datetime.fromtimestamp(processed_at, ZoneInfo("America/New_York"))
    action = item.get("action") or "notified"
    decision = (item.get("decision") or {}).get("decision", "")
    key = day_key(processed_dt)
    daily.setdefault(key, {"date": key, "merged": 0, "repaired": 0, "blocked": 0, "approval": 0})
    if action == "repaired":
      daily[key]["repaired"] += 1
    if action in ("repair_failed", "repair_skipped", "merge_failed") or decision == "blocked":
      daily[key]["blocked"] += 1
    if decision == "needs_approval":
      daily[key]["approval"] += 1
    recent_actions.append({
      "repo": item.get("repo", ""),
      "number": item.get("number", ""),
      "action": action,
      "decision": decision,
      "reason": (item.get("decision") or {}).get("reason", ""),
      "processed_at": processed_dt.isoformat(timespec="minutes"),
    })
  days = []
  for offset in range(6, -1, -1):
    key = day_key(now - datetime.timedelta(days=offset))
    days.append(daily.get(key, {"date": key, "merged": 0, "repaired": 0, "blocked": 0, "approval": 0}))
  recent_actions.sort(key=lambda item: item["processed_at"], reverse=True)
  return {
    "days": days,
    "recent_actions": recent_actions[:12],
    "totals": {
      "merged": sum(day["merged"] for day in days),
      "repaired": sum(day["repaired"] for day in days),
      "blocked": sum(day["blocked"] for day in days),
      "approval": sum(day["approval"] for day in days),
    },
  }

def status_payload():
  now = time.time()
  if CACHE["data"] and CACHE["expires"] > now:
    return CACHE["data"]
  state_cm = kube_configmap(STATE_CM)
  state_data = state_cm.get("data", {})
  github_rows, github_merged = github_status(state_data)
  gitlab_rows, gitlab_merged = gitlab_status(state_data)
  rows = sorted(github_rows + gitlab_rows, key=lambda item: (item["bucket"], item["repo"], item["number"]))
  merged_recent = sorted(github_merged + gitlab_merged, key=lambda item: item["merged_at"], reverse=True)
  merged_today = [item for item in merged_recent if item.get("is_today")]
  counts = {}
  for row in rows:
    counts[row["bucket"]] = counts.get(row["bucket"], 0) + 1
  payload = {
    "generated_at": datetime.datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="seconds"),
    "counts": counts,
    "reviewer": reviewer_run_status(),
    "activity": activity_history(state_data, merged_recent),
    "open": rows,
    "merged_today": merged_today,
    "merged_recent": merged_recent,
  }
  CACHE["data"] = payload
  CACHE["expires"] = now + CACHE_SECONDS
  return payload

def approve_pr(body):
  platform = str(body.get("platform", "github")).strip().lower()
  owner = str(body.get("owner", "")).strip()
  repo = str(body.get("repo", "")).strip()
  number = int(body.get("number", 0))
  expected_sha = str(body.get("sha", "")).strip()
  if not owner or not repo or number <= 0 or not expected_sha:
    raise ValueError("owner, repo, number, and sha are required")
  if platform == "gitlab":
    return approve_gitlab_mr(owner, repo, number, expected_sha)
  if platform != "github":
    raise ValueError("unsupported approval platform")
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
  mergeable = pr.get("mergeable")
  mergeable_state = pr.get("mergeable_state") or "unknown"
  if mergeable is False and mergeable_state == "dirty":
    raise ValueError("pull request has merge conflicts")

  github("POST", f"/repos/{owner}/{repo}/issues/{number}/labels", {"labels": [APPROVAL_LABEL]})
  github("POST", f"/repos/{owner}/{repo}/issues/{number}/comments", {"body": APPROVAL_COMMAND})
  CACHE["expires"] = 0
  return {
    "ok": True,
    "message": f"Approved {owner}/{repo}#{number}. The reviewer will merge it on the next run once checks are green.",
  }

def approve_gitlab_mr(namespace, project, number, expected_sha):
  if not GITLAB_TOKEN:
    raise PermissionError("GitLab token is not configured")
  if (namespace.lower(), project.lower()) not in gitlab_allowlist():
    raise PermissionError("GitLab repository is not in the Renovate agent allowlist")
  project_id = urllib.parse.quote(f"{namespace}/{project}", safe="")
  mr = gitlab("GET", f"/projects/{project_id}/merge_requests/{number}")
  if mr.get("state") != "opened":
    raise ValueError("merge request is not open")
  current_sha = mr.get("sha") or ""
  if current_sha != expected_sha:
    raise ValueError("merge request head changed; refresh before approving")
  if not renovate_pr(mr):
    raise ValueError("merge request does not look like a Renovate MR")
  merge_state = mr.get("detailed_merge_status") or mr.get("merge_status") or "unknown"
  if merge_state in ("conflict", "cannot_be_merged"):
    raise ValueError("merge request has merge conflicts")
  gitlab("PUT", f"/projects/{project_id}/merge_requests/{number}", {"add_labels": APPROVAL_LABEL})
  gitlab("POST", f"/projects/{project_id}/merge_requests/{number}/notes", {"body": APPROVAL_COMMAND})
  CACHE["expires"] = 0
  return {
    "ok": True,
    "message": f"Approved {namespace}/{project}!{number}. The reviewer will merge it on the next run once checks are green.",
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
    .panel-grid { display: grid; grid-template-columns: 1fr 1.4fr; gap: 14px; margin-bottom: 20px; }
    .panel { border: 1px solid #27272a; background: #18181b; border-radius: 8px; padding: 14px; }
    .panel h2 { margin-bottom: 10px; }
    .panel-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 10px; }
    .panel-head h2 { margin-bottom: 0; }
    .kv { display: grid; grid-template-columns: 128px 1fr; gap: 7px 12px; font-size: 13px; }
    .log { margin: 12px 0 0; padding: 10px; border-radius: 6px; background: #101114; color: #d4d4d8; font-size: 12px; overflow: auto; white-space: pre-wrap; }
    .history { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 8px; }
    .day { border: 1px solid #27272a; border-radius: 6px; padding: 8px; min-height: 76px; background: #101114; }
    .day strong { display: block; font-size: 12px; margin-bottom: 7px; }
    .mini { display: flex; justify-content: space-between; gap: 6px; color: #d4d4d8; font-size: 12px; }
    .audit { display: grid; gap: 8px; }
    .audit-item { border: 1px solid #27272a; background: #18181b; border-radius: 8px; padding: 10px 12px; font-size: 13px; }
    .audit-top { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 4px; }
    section { margin-top: 26px; }
    .panel-grid section { margin-top: 0; }
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
    .blocked, .conflict { border-color: #ef4444; color: #f87171; }
    .stale { border-color: #f59e0b; color: #fbbf24; }
    .waiting { border-color: #38bdf8; color: #67e8f9; }
    .repaired { border-color: #22c55e; color: #86efac; }
    .approved, .green { border-color: #84cc16; color: #bef264; }
    .reason { color: #a1a1aa; max-width: 440px; }
    .empty { color: #a1a1aa; background: #18181b; border: 1px solid #27272a; border-radius: 8px; padding: 16px; }
    .error { color: #fecaca; background: #3f1d22; border: 1px solid #7f1d1d; border-radius: 8px; padding: 16px; margin-bottom: 18px; }
    .actions { white-space: nowrap; }
    @media (max-width: 900px) { .panel-grid { grid-template-columns: 1fr; } .history { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
    @media (max-width: 760px) {
      header { display: block; }
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      input { min-width: 100%; }
      table, thead, tbody, tr, th, td { display: block; }
      thead { display: none; }
      table { background: transparent; border: 0; }
      tr { border: 1px solid #27272a; background: #18181b; border-radius: 8px; margin-bottom: 10px; overflow: hidden; }
      td { display: grid; grid-template-columns: 92px 1fr; gap: 10px; border-bottom: 1px solid #27272a; }
      td::before { content: attr(data-label); color: #a1a1aa; font-size: 11px; text-transform: uppercase; }
      .actions { white-space: normal; }
    }
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
    <div class="panel-grid">
      <section class="panel">
        <div class="panel-head">
          <h2>Reviewer Run</h2>
          <button id="runReviewer" type="button">Run now</button>
        </div>
        <div id="reviewer"></div>
      </section>
      <section class="panel">
        <h2>7-Day Activity</h2>
        <div id="activity"></div>
      </section>
    </div>
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
      <h2>Pending Approved</h2>
      <div id="pending"></div>
    </section>
    <section>
      <h2>Audit Trail</h2>
      <div id="audit"></div>
    </section>
    <section>
      <h2>Merged Today</h2>
      <div id="merged"></div>
    </section>
  </main>
  <script>
    const ACTION_TOKEN = __ACTION_TOKEN__;
    const labels = ["approval","conflict","blocked","waiting","repaired","approved","green","unreviewed"];
    const state = { open: [], merged_today: [], counts: {}, generated_at: "", reviewer: {}, activity: {} };
    const locallyApproved = new Set();
    function esc(value) { return String(value ?? "").replace(/[&<>"']/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c])); }
    function formatTime(value) {
      if (!value) return "";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return value;
      return new Intl.DateTimeFormat(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      }).format(date);
    }
    function formatDate(value) {
      if (!value) return "";
      const date = new Date(`${value}T12:00:00`);
      if (Number.isNaN(date.getTime())) return value;
      return new Intl.DateTimeFormat(undefined, {
        month: "short",
        day: "numeric",
      }).format(date);
    }
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
    function approvalKey(row) {
      return `${row.owner}/${row.repo}#${row.number}@${row.sha}`;
    }
    function applyLocalApprovals(rows) {
      return rows.map(row => {
        if (!locallyApproved.has(approvalKey(row))) return row;
        return { ...row, can_approve: false, approval: row.approval || 'approved from dashboard' };
      });
    }
    function filteredRows() {
      const bucket = document.getElementById('stateFilter').value;
      const repo = document.getElementById('repoFilter').value;
      const platform = document.getElementById('platformFilter').value;
      const query = document.getElementById('searchFilter').value.trim().toLowerCase();
      return state.open.filter(row => {
        const haystack = `${row.repo} ${row.title} ${row.branch} ${row.reason} ${row.mergeable_state}`.toLowerCase();
        return (!bucket || row.bucket === bucket)
          && (!repo || row.repo === repo)
          && (!platform || row.platform === platform)
          && (!query || haystack.includes(query));
      });
    }
    function render() {
      document.getElementById('generated').textContent = state.generated_at ? `Generated ${formatTime(state.generated_at)}` : 'Loading...';
      document.getElementById('stats').innerHTML = labels.map(label => `<div class="stat"><strong>${state.counts[label] || 0}</strong><span>${label}</span></div>`).join("");
      document.getElementById('reviewer').innerHTML = renderReviewer(state.reviewer || {});
      document.getElementById('activity').innerHTML = renderActivity(state.activity || {});
      document.getElementById('open').innerHTML = renderRows(filteredRows());
      document.getElementById('pending').innerHTML = renderRows(pendingApproved());
      document.getElementById('audit').innerHTML = renderAudit(state.activity.recent_actions || []);
      document.getElementById('merged').innerHTML = renderMerged(state.merged_today || []);
    }
    function pendingApproved() {
      return state.open.filter(row => row.approval && !['merged', 'repaired'].includes(row.action || ''));
    }
    function renderReviewer(row) {
      if (row.error) return `<div class="error">Reviewer status failed: ${esc(row.error)}</div>`;
      const log = (row.log_tail || []).join("\\n");
      return `<div class="kv">
        <div class="muted">State</div><div><span class="pill ${row.stale ? 'stale' : row.state === 'succeeded' ? 'green' : row.state === 'failed' ? 'blocked' : 'waiting'}">${esc(row.stale ? 'stale' : row.state || 'unknown')}</span></div>
        <div class="muted">Schedule</div><div>${esc(row.schedule || '')}</div>
        <div class="muted">Next run</div><div>${esc(formatTime(row.next_schedule_time))}</div>
        <div class="muted">Last schedule</div><div>${esc(formatTime(row.last_schedule_time))}</div>
        <div class="muted">Last success</div><div>${esc(formatTime(row.last_successful_time))}</div>
        <div class="muted">Latest job</div><div>${esc(row.latest_job || '')}</div>
        <div class="muted">Duration</div><div>${esc(row.duration || '')}</div>
      </div>${log ? `<pre class="log">${esc(log)}</pre>` : ''}`;
    }
    function renderActivity(activity) {
      const days = activity.days || [];
      if (!days.length) return '<div class="empty">No activity history yet.</div>';
      const totals = activity.totals || {};
      return `<div class="muted">Merged ${totals.merged || 0}, repaired ${totals.repaired || 0}, blocked ${totals.blocked || 0}, approval ${totals.approval || 0}</div>
        <div class="history">${days.map(day => `<div class="day">
          <strong>${esc(formatDate(day.date))}</strong>
          <div class="mini"><span>merged</span><span>${day.merged || 0}</span></div>
          <div class="mini"><span>repaired</span><span>${day.repaired || 0}</span></div>
          <div class="mini"><span>blocked</span><span>${day.blocked || 0}</span></div>
          <div class="mini"><span>approval</span><span>${day.approval || 0}</span></div>
        </div>`).join("")}</div>`;
    }
    function renderRows(rows) {
      if (!rows.length) return '<div class="empty">No open Renovate PRs.</div>';
      return `<table><thead><tr><th>State</th><th>Repo</th><th>PR</th><th>Checks</th><th>Merge</th><th>Decision</th><th>Reason</th><th>Actions</th></tr></thead><tbody>${rows.map(row => `
        <tr>
          <td data-label="State"><span class="pill ${esc(row.bucket)}">${esc(row.bucket)}</span></td>
          <td data-label="Repo">${esc(row.repo)}<div class="muted">${esc(row.platform)} ${esc(row.short_sha || row.sha)}</div></td>
          <td data-label="PR"><a href="${esc(row.url)}">${esc(row.title)}</a><div class="muted">#${esc(row.number)} ${esc(row.branch)}</div></td>
          <td data-label="Checks">${esc(row.checks)}</td>
          <td data-label="Merge">${esc(row.mergeable_state || "unknown")}</td>
          <td data-label="Decision">${esc(row.decision)}<div class="muted">${esc(row.approval || row.action || "")}</div></td>
          <td data-label="Reason" class="reason">${esc(row.reason)}</td>
          <td data-label="Actions" class="actions">${row.can_approve ? `<button class="primary" type="button" data-platform="${esc(row.platform)}" data-owner="${esc(row.owner)}" data-repo="${esc(row.repo)}" data-number="${esc(row.number)}" data-sha="${esc(row.sha)}">Approve</button>` : row.approval ? '<button type="button" disabled>Approved</button>' : ''}</td>
        </tr>`).join("")}</tbody></table>`;
    }
    function renderAudit(rows) {
      if (!rows.length) return '<div class="empty">No recent reviewer actions.</div>';
      return `<div class="audit">${rows.map(row => `<div class="audit-item">
        <div class="audit-top"><strong>${esc(row.repo)}#${esc(row.number)}</strong><span class="muted">${esc(formatTime(row.processed_at))}</span></div>
        <div>${esc(row.action || 'unknown')} / ${esc(row.decision || 'unknown')}</div>
        <div class="muted">${esc(row.reason || '')}</div>
      </div>`).join("")}</div>`;
    }
    function renderMerged(rows) {
      if (!rows.length) return '<div class="empty">No Renovate PRs merged today.</div>';
      return `<table><thead><tr><th>Repo</th><th>PR</th><th>Merged</th></tr></thead><tbody>${rows.map(row => `
        <tr>
          <td data-label="Repo">${esc(row.repo)}<div class="muted">${esc(row.platform)}</div></td>
          <td data-label="PR"><a href="${esc(row.url)}">${esc(row.title)}</a><div class="muted">#${esc(row.number)}</div></td>
          <td data-label="Merged">${esc(formatTime(row.merged_at))}</td>
        </tr>`).join("")}</tbody></table>`;
    }
    async function approve(row, button) {
      button.disabled = true;
      button.textContent = 'Approving...';
      showNotice("");
      try {
        const res = await fetch('/api/approve', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Renovate-Dashboard-Action': ACTION_TOKEN },
          body: JSON.stringify(row),
        });
        const data = await res.json();
        if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
        locallyApproved.add(approvalKey(row));
        button.textContent = 'Approved';
        button.disabled = true;
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
        state.reviewer = data.reviewer || {};
        state.activity = data.activity || {};
        state.open = applyLocalApprovals(data.open || []);
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
    async function runReviewer(button) {
      if (!window.confirm('Run the Renovate reviewer now? This will create a one-off Kubernetes Job.')) return;
      button.disabled = true;
      button.textContent = 'Starting...';
      showNotice("");
      try {
        const res = await fetch('/api/run-reviewer', {
          method: 'POST',
          headers: { 'X-Renovate-Dashboard-Action': ACTION_TOKEN },
        });
        const data = await res.json();
        if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
        showNotice(data.message || 'Reviewer started.');
        await load();
      } catch (err) {
        showNotice(`Reviewer run failed: ${err.message}`, true);
      } finally {
        button.disabled = false;
        button.textContent = 'Run now';
      }
    }
    document.getElementById('open').addEventListener('click', event => {
      if (event.target.tagName !== 'BUTTON') return;
      const button = event.target;
      approve({
        platform: button.dataset.platform || 'github',
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
    document.getElementById('runReviewer').addEventListener('click', event => runReviewer(event.target));
    load();
    setInterval(load, 90000);
  </script>
</body>
</html>""".replace("__ACTION_TOKEN__", json.dumps(ACTION_TOKEN))

class Handler(BaseHTTPRequestHandler):
  def send_json(self, status, payload):
    body = json.dumps(payload).encode("utf-8")
    self.send_response(status)
    self.send_header("Content-Type", "application/json")
    self.send_header("Cache-Control", "no-store")
    self.end_headers()
    self.wfile.write(body)

  def action_allowed(self):
    token = self.headers.get("X-Renovate-Dashboard-Action", "")
    if not secrets.compare_digest(token, ACTION_TOKEN):
      self.send_json(403, {"error": "invalid dashboard action token"})
      return False
    host = self.headers.get("Host", "")
    for header in ("Origin", "Referer"):
      value = self.headers.get(header)
      if not value:
        continue
      parsed = urllib.parse.urlparse(value)
      if parsed.netloc and parsed.netloc != host:
        self.send_json(403, {"error": f"invalid {header.lower()} header"})
        return False
    return True

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
    if path == "/api/run-reviewer":
      if not self.action_allowed():
        return
      try:
        self.send_json(200, trigger_reviewer())
      except HttpError as exc:
        self.send_json(400, {"error": str(exc)})
      except Exception as exc:
        self.send_json(500, {"error": str(exc)})
      return
    if path != "/api/approve":
      self.send_json(404, {"error": "not found"})
      return
    if not self.action_allowed():
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
