#!/usr/bin/env python3
"""Automated, idempotent project migration orchestrator.

Handles full migration lifecycle for development repositories:
1. Preflight validation & non-destructive backups (tarball + git bundle)
2. Forgejo repository creation (private, uninitialized) & clone user collaborator setup
3. Git ref transfer & verification (branches + tags, tip hash check)
4. Woodpecker activation & enrollment (with Zot pull credential)
5. Manifest enrollment update
6. GitHub push mirror setup & verification (sync on commit, tip hash match)
7. Local git remote reconfiguration (origin -> Forgejo, github -> GitHub mirror)
8. CI execution verification
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("migrate_project")

BATCH_MAPPING: dict[str, tuple[str, ...]] = {
    "1": ("resume-agent", "cctop"),
    "clean-python": ("resume-agent", "cctop"),
    "2": (
        "musicthing",
        "predmarkbot",
        "roku-bulb-local",
        "bayesian-reaction-optimization-engine",
    ),
    "remaining-python": (
        "musicthing",
        "predmarkbot",
        "roku-bulb-local",
        "bayesian-reaction-optimization-engine",
    ),
    "3": ("apptlyAI-web", "spotify-clone-temp-name"),
    "web": ("apptlyAI-web", "spotify-clone-temp-name"),
    "4": ("NaviSync", "neuralnet-in-c", "md-engine"),
    "compiled": ("NaviSync", "neuralnet-in-c", "md-engine"),
    "5": ("apptlyAI",),
    "complex": ("apptlyAI",),
}

ZOT_REGISTRY_HOST = "registry.rupan.dev"
ZOT_NODE_USER = "node"
ZOT_NODE_PASS = "WbgdChvpHKJR32USsh0etJpqI4EmC3ou"
CLONE_USER = "homelab-ci-source"
FORGEJO_SSH_BASE = "ssh://forgejo@git.internal:2222"
FORGEJO_HTTP_BASE = "http://git.internal:3000"
WOODPECKER_HTTP_BASE = "http://ci.internal:8000"


@dataclass
class ProjectEntry:
    name: str
    github_repo: str
    forgejo_repo: str
    visibility: str
    state: str
    default_branch: str
    local_path: Path | None
    ci_kind: str
    ci_tier: str
    woodpecker_id: int | None
    raw_data: dict[str, Any]


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Projects manifest not found: {manifest_path}")
    data = json.loads(manifest_path.read_text())
    if not isinstance(data, dict) or "projects" not in data:
        raise ValueError("Invalid manifest structure")
    return data


def save_manifest(manifest_path: Path, data: dict[str, Any]) -> None:
    temp_path = manifest_path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(data, indent=2) + "\n")
    temp_path.replace(manifest_path)


def find_project(manifest_data: dict[str, Any], identifier: str) -> ProjectEntry:
    ident_lower = identifier.lower().strip()
    for item in manifest_data.get("projects", []):
        name = item.get("name", "")
        f_repo = item.get("forgejo_repo", "")
        g_repo = item.get("github_repo", "")
        if ident_lower in (
            name.lower(),
            f_repo.lower(),
            g_repo.lower(),
        ) or ident_lower == name.lower().replace("-", "_"):
            local_path = (
                Path(item["local_path"]).expanduser().resolve()
                if item.get("local_path")
                else None
            )
            ci = item.get("ci", {})
            return ProjectEntry(
                name=name,
                github_repo=g_repo or f_repo,
                forgejo_repo=f_repo,
                visibility=item.get("visibility", "private"),
                state=item.get("state", "migrate"),
                default_branch=item.get("default_branch", "main"),
                local_path=local_path,
                ci_kind=ci.get("kind", "none"),
                ci_tier=ci.get("tier", "sandbox"),
                woodpecker_id=item.get("woodpecker_repository_id"),
                raw_data=item,
            )
    raise KeyError(f"Project '{identifier}' not found in manifest")


def get_forgejo_admin_token() -> str:
    """Generate a high-privilege access token for user rupan on nas-01."""
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        "nas-01",
        (
            "sudo -u forgejo /nix/store/wwwljiy2dq8byzfgif9rc9nlp68i18kb-forgejo-lts-15.0.7/bin/forgejo "
            "admin user generate-access-token --config /persist/forgejo/custom/conf/app.ini "
            f"--work-path /persist/forgejo --username rupan --token-name migrate-run-{int(time.time())} --scopes all --raw"
        ),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    token = res.stdout.strip()
    if not token or len(token) < 20:
        raise RuntimeError(f"Failed to generate Forgejo admin token: {res.stderr}")
    return token


def get_woodpecker_jwt_token() -> str:
    """Read user hash for rupan on homelab-04 and construct signed JWT."""
    py_code = """
import sqlite3, hmac, hashlib, base64, json
con = sqlite3.connect('/persist/woodpecker/server-data/woodpecker.sqlite')
cur = con.cursor()
cur.execute("SELECT id, login, hash FROM users WHERE login='rupan'")
uid, login, secret = cur.fetchone()
def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()
header = b64url(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode())
payload = b64url(json.dumps({'type': 'user', 'user-id': str(uid)}).encode())
sig = b64url(hmac.new(secret.encode(), f'{header}.{payload}'.encode(), hashlib.sha256).digest())
print(f'{header}.{payload}.{sig}')
"""
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        "homelab-04",
        f"sudo /nix/store/7vlwcwfrd7ywsrr7y6rif7jq0biziyhq-python3-3.13.15-env/bin/python -c {json.dumps(py_code)}",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    jwt_token = res.stdout.strip()
    if not jwt_token or len(jwt_token) < 30:
        raise RuntimeError("Failed to generate Woodpecker JWT token")
    return jwt_token


def get_github_auth_token() -> str:
    res = subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True, check=True
    )
    token = res.stdout.strip()
    if not token:
        raise RuntimeError("GitHub CLI auth token is empty")
    return token


def forgejo_request(
    endpoint: str, token: str, method: str = "GET", data: dict[str, Any] | None = None
) -> Any:
    url = f"{FORGEJO_HTTP_BASE}/api/v1/{endpoint.lstrip('/')}"
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"token {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read()
            if not content:
                return {}
            return json.loads(content)
    except urllib.error.HTTPError as err:
        error_msg = err.read().decode("utf-8", errors="replace")
        if err.code == 404:
            return None
        raise RuntimeError(
            f"Forgejo API {method} {url} returned HTTP {err.code}: {error_msg}"
        ) from err


def woodpecker_request(
    endpoint: str, token: str, method: str = "GET", data: dict[str, Any] | None = None
) -> Any:
    url = f"{WOODPECKER_HTTP_BASE}/api/{endpoint.lstrip('/')}"
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read()
            if not content:
                return {}
            return json.loads(content)
    except urllib.error.HTTPError as err:
        error_msg = err.read().decode("utf-8", errors="replace")
        if err.code == 404:
            return None
        raise RuntimeError(
            f"Woodpecker API {method} {url} returned HTTP {err.code}: {error_msg}"
        ) from err


# ==============================================================================
# Migration Stages
# ==============================================================================


def stage_preflight_and_backup(
    project: ProjectEntry, dry_run: bool, skip_backup: bool
) -> dict[str, Any]:
    logger.info(f"[{project.name}] Stage 1: Preflight & non-destructive backup")
    if not project.local_path or not project.local_path.is_dir():
        raise FileNotFoundError(f"Local checkout does not exist: {project.local_path}")
    if not (project.local_path / ".git").is_dir():
        raise ValueError(f"Not a git repository: {project.local_path}")

    # Check git status
    status_out = subprocess.check_output(
        ["git", "-C", str(project.local_path), "status", "--porcelain"],
        text=True,
    )
    is_dirty = bool(status_out.strip())
    head_tip = subprocess.check_output(
        ["git", "-C", str(project.local_path), "rev-parse", project.default_branch],
        text=True,
    ).strip()

    backup_dir = Path("/home/rupan/projects")
    tar_path = backup_dir / f".backup-{project.name}-pre-migration.tar.gz"
    bundle_path = backup_dir / f".backup-{project.name}-refs.bundle"

    if dry_run:
        logger.info(
            f"[{project.name}] [DRY RUN] Would verify tree (dirty={is_dirty}) and create backups at {tar_path} & {bundle_path}"
        )
        return {"head_tip": head_tip, "dirty": is_dirty}

    if not skip_backup:
        if not tar_path.exists():
            logger.info(f"[{project.name}] Creating tarball backup: {tar_path}")
            subprocess.run(
                [
                    "tar",
                    "-czf",
                    str(tar_path),
                    "-C",
                    str(project.local_path.parent),
                    project.local_path.name,
                ],
                check=True,
            )
        if not bundle_path.exists():
            logger.info(f"[{project.name}] Creating git bundle backup: {bundle_path}")
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(project.local_path),
                    "bundle",
                    "create",
                    str(bundle_path),
                    "--all",
                ],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(project.local_path),
                    "bundle",
                    "verify",
                    str(bundle_path),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )

    return {"head_tip": head_tip, "dirty": is_dirty}


def stage_forgejo_repo(project: ProjectEntry, forgejo_token: str, dry_run: bool) -> int:
    logger.info(f"[{project.name}] Stage 2: Forgejo repository setup")
    org, repo_name = project.forgejo_repo.split("/", 1)

    repo_info = forgejo_request(f"repos/{project.forgejo_repo}", forgejo_token)
    if dry_run:
        if repo_info is None:
            logger.info(
                f"[{project.name}] [DRY RUN] Would create Forgejo repo {project.forgejo_repo} (private=true)"
            )
        else:
            logger.info(
                f"[{project.name}] [DRY RUN] Forgejo repo already exists (id={repo_info.get('id')})"
            )
        logger.info(
            f"[{project.name}] [DRY RUN] Would ensure collaborator {CLONE_USER} with read permission"
        )
        return repo_info.get("id", 9999) if repo_info else 9999

    if repo_info is None:
        logger.info(
            f"[{project.name}] Creating Forgejo repository: {project.forgejo_repo}"
        )
        created = forgejo_request(
            f"orgs/{org}/repos",
            forgejo_token,
            method="POST",
            data={"name": repo_name, "private": True, "auto_init": False},
        )
        forgejo_id = created["id"]
    else:
        forgejo_id = repo_info["id"]
        # Ensure private
        if not repo_info.get("private", False):
            logger.info(
                f"[{project.name}] Setting Forgejo repository privacy to private"
            )
            forgejo_request(
                f"repos/{project.forgejo_repo}",
                forgejo_token,
                method="PATCH",
                data={"private": True},
            )

    # Ensure clone user collaborator permission
    logger.info(f"[{project.name}] Granting read access to {CLONE_USER}")
    forgejo_request(
        f"repos/{project.forgejo_repo}/collaborators/{CLONE_USER}",
        forgejo_token,
        method="PUT",
        data={"permission": "read"},
    )

    return forgejo_id


def stage_transfer_refs(project: ProjectEntry, head_tip: str, dry_run: bool) -> None:
    logger.info(f"[{project.name}] Stage 3: Git ref transfer & verification")
    target_url = f"{FORGEJO_SSH_BASE}/{project.forgejo_repo}.git"

    if dry_run:
        logger.info(
            f"[{project.name}] [DRY RUN] Would push all branches and tags to {target_url}"
        )
        return

    logger.info(f"[{project.name}] Pushing refs to {target_url}")
    subprocess.run(
        [
            "git",
            "-C",
            str(project.local_path),
            "push",
            target_url,
            "refs/heads/*:refs/heads/*",
            "refs/tags/*:refs/tags/*",
        ],
        check=True,
    )

    # Verify remote tip matches local head tip
    ls_out = subprocess.check_output(
        ["git", "ls-remote", target_url, f"refs/heads/{project.default_branch}"],
        text=True,
    ).strip()
    remote_tip = ls_out.split()[0] if ls_out else ""
    if remote_tip != head_tip:
        raise RuntimeError(
            f"Tip divergence for {project.name}! Local {head_tip} != Remote {remote_tip}"
        )
    logger.info(f"[{project.name}] Remote tip verified at {remote_tip}")


def stage_woodpecker_enrollment(
    project: ProjectEntry,
    forgejo_repo_id: int,
    woodpecker_token: str,
    dry_run: bool,
) -> int:
    logger.info(f"[{project.name}] Stage 4: Woodpecker activation & enrollment")
    if dry_run:
        logger.info(
            f"[{project.name}] [DRY RUN] Would activate repo with forge remote ID {forgejo_repo_id}"
        )
        logger.info(
            f"[{project.name}] [DRY RUN] Would configure Zot registry pull credential for {ZOT_REGISTRY_HOST}"
        )
        return project.woodpecker_id or 9999

    repo_info = woodpecker_request(
        f"repos/lookup/{project.forgejo_repo}", woodpecker_token
    )
    if repo_info is None or not repo_info.get("active", False):
        logger.info(f"[{project.name}] Activating repository in Woodpecker")
        repo_info = woodpecker_request(
            f"repos?forge_remote_id={forgejo_repo_id}",
            woodpecker_token,
            method="POST",
        )

    wp_id = repo_info["id"]
    logger.info(f"[{project.name}] Woodpecker repository ID: {wp_id}")

    # Set private in Woodpecker sqlite database to ensure clone netrc is supplied
    py_code = f"""
import sqlite3
con = sqlite3.connect('/persist/woodpecker/server-data/woodpecker.sqlite')
cur = con.cursor()
cur.execute("UPDATE repos SET private=1, visibility='private' WHERE id={wp_id}")
con.commit()
"""
    subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "homelab-04",
            f"sudo /nix/store/7vlwcwfrd7ywsrr7y6rif7jq0biziyhq-python3-3.13.15-env/bin/python -c {json.dumps(py_code)}",
        ],
        check=True,
    )

    # Ensure registry pull credential exists
    registries = woodpecker_request(f"repos/{wp_id}/registries", woodpecker_token)
    has_registry = any(
        isinstance(r, dict) and r.get("address") == ZOT_REGISTRY_HOST
        for r in (registries or [])
    )
    if not has_registry:
        logger.info(
            f"[{project.name}] Adding registry credential for {ZOT_REGISTRY_HOST}"
        )
        woodpecker_request(
            f"repos/{wp_id}/registries",
            woodpecker_token,
            method="POST",
            data={
                "address": ZOT_REGISTRY_HOST,
                "username": ZOT_NODE_USER,
                "password": ZOT_NODE_PASS,
            },
        )

    return wp_id


def stage_github_mirror(
    project: ProjectEntry,
    forgejo_token: str,
    github_token: str,
    head_tip: str,
    dry_run: bool,
) -> None:
    logger.info(f"[{project.name}] Stage 5: GitHub push mirror setup & sync")
    remote_address = f"https://github.com/{project.github_repo}.git"

    if dry_run:
        logger.info(
            f"[{project.name}] [DRY RUN] Would configure push mirror to {remote_address}"
        )
        return

    mirrors = (
        forgejo_request(f"repos/{project.forgejo_repo}/push_mirrors", forgejo_token)
        or []
    )
    matching = next(
        (m for m in mirrors if m.get("remote_address") == remote_address),
        None,
    )
    if matching is None:
        logger.info(
            f"[{project.name}] Creating push mirror on Forgejo to {remote_address}"
        )
        forgejo_request(
            f"repos/{project.forgejo_repo}/push_mirrors",
            forgejo_token,
            method="POST",
            data={
                "remote_address": remote_address,
                "remote_username": "x-access-token",
                "remote_password": github_token,
                "interval": "1h",
                "sync_on_commit": True,
            },
        )

    logger.info(f"[{project.name}] Triggering push mirror sync")
    forgejo_request(
        f"repos/{project.forgejo_repo}/push_mirrors-sync",
        forgejo_token,
        method="POST",
    )

    # Wait for sync and verify tip on GitHub
    logger.info(f"[{project.name}] Verifying sync on GitHub...")
    for _ in range(12):
        time.sleep(2)
        gh_res = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{project.github_repo}/commits/{project.default_branch}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if gh_res.returncode == 0:
            commit_data = json.loads(gh_res.stdout)
            gh_sha = commit_data.get("sha", "")
            if gh_sha == head_tip:
                logger.info(
                    f"[{project.name}] GitHub mirror verified at commit {gh_sha}"
                )
                return

    logger.warning(
        f"[{project.name}] GitHub mirror sync pending or tip not yet visible"
    )


def stage_reconfigure_remotes(project: ProjectEntry, dry_run: bool) -> None:
    logger.info(f"[{project.name}] Stage 6: Reconfiguring local git remotes")
    if not project.local_path:
        return

    forgejo_remote_url = f"{FORGEJO_SSH_BASE}/{project.forgejo_repo}.git"

    remotes_out = subprocess.check_output(
        ["git", "-C", str(project.local_path), "remote", "-v"],
        text=True,
    )

    if dry_run:
        logger.info(
            f"[{project.name}] [DRY RUN] Would update remotes: origin -> {forgejo_remote_url}, github -> GitHub"
        )
        return

    # Check origin
    if "origin" in remotes_out:
        origin_url = subprocess.check_output(
            ["git", "-C", str(project.local_path), "remote", "get-url", "origin"],
            text=True,
        ).strip()
        if "github.com" in origin_url:
            if "github" not in remotes_out:
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(project.local_path),
                        "remote",
                        "rename",
                        "origin",
                        "github",
                    ],
                    check=True,
                )
            else:
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(project.local_path),
                        "remote",
                        "remove",
                        "origin",
                    ],
                    check=True,
                )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(project.local_path),
                    "remote",
                    "add",
                    "origin",
                    forgejo_remote_url,
                ],
                check=True,
            )
        elif origin_url != forgejo_remote_url:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(project.local_path),
                    "remote",
                    "set-url",
                    "origin",
                    forgejo_remote_url,
                ],
                check=True,
            )
    else:
        subprocess.run(
            [
                "git",
                "-C",
                str(project.local_path),
                "remote",
                "add",
                "origin",
                forgejo_remote_url,
            ],
            check=True,
        )

    # Ensure github remote exists
    remotes_after = subprocess.check_output(
        ["git", "-C", str(project.local_path), "remote"],
        text=True,
    ).split()
    if "github" not in remotes_after:
        subprocess.run(
            [
                "git",
                "-C",
                str(project.local_path),
                "remote",
                "add",
                "github",
                f"git@github.com:{project.github_repo}.git",
            ],
            check=True,
        )

    # Set upstream to origin/default_branch
    subprocess.run(
        [
            "git",
            "-C",
            str(project.local_path),
            "fetch",
            "origin",
            project.default_branch,
        ],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(project.local_path),
            "branch",
            f"--set-upstream-to=origin/{project.default_branch}",
            project.default_branch,
        ],
        check=True,
    )
    logger.info(
        f"[{project.name}] Local remotes reconfigured; tracking origin/{project.default_branch}"
    )


def stage_verify_pipeline(
    project: ProjectEntry,
    wp_id: int,
    woodpecker_token: str,
    dry_run: bool,
    wait: bool,
) -> bool:
    logger.info(f"[{project.name}] Stage 7: CI pipeline verification")
    if dry_run or not wait:
        return True

    logger.info(f"[{project.name}] Checking Woodpecker pipelines for repo {wp_id}...")
    for _ in range(30):
        pipelines = (
            woodpecker_request(f"repos/{wp_id}/pipelines", woodpecker_token) or []
        )
        if pipelines:
            latest = pipelines[0]
            status = latest.get("status")
            p_num = latest.get("number")
            logger.info(f"[{project.name}] Pipeline #{p_num} status: {status}")
            if status == "success":
                return True
            if status in {"failure", "error", "killed"}:
                logger.error(f"[{project.name}] Pipeline #{p_num} ended in {status}")
                return False
        time.sleep(3)

    logger.warning(f"[{project.name}] Pipeline wait timed out")
    return False


# ==============================================================================
# Orchestrator
# ==============================================================================


def migrate_project(
    project: ProjectEntry,
    manifest_data: dict[str, Any],
    manifest_path: Path,
    forgejo_token: str,
    woodpecker_token: str,
    github_token: str,
    dry_run: bool,
    skip_backup: bool,
    wait_ci: bool,
) -> bool:
    logger.info("============================================================")
    logger.info(f"Starting migration for {project.name} ({project.forgejo_repo})")
    logger.info("============================================================")

    # 1. Preflight & Backup
    preflight = stage_preflight_and_backup(project, dry_run, skip_backup)
    head_tip = preflight["head_tip"]

    # 2. Forgejo Repo & Collaborator
    forgejo_id = stage_forgejo_repo(project, forgejo_token, dry_run)

    # 3. Ref transfer
    stage_transfer_refs(project, head_tip, dry_run)

    # 4. Woodpecker Enrollment
    wp_id = stage_woodpecker_enrollment(project, forgejo_id, woodpecker_token, dry_run)

    # Update manifest in-memory
    for p in manifest_data["projects"]:
        if p["name"] == project.name:
            p["woodpecker_repository_id"] = wp_id
            p["visibility"] = "private"
            p["state"] = "already-primary"
            p["project_type"] = "development"
            p["migration_status"] = "complete"
            p["git_primary"] = "forgejo"
            p["github_mirror"] = True
            break

    if not dry_run:
        save_manifest(manifest_path, manifest_data)
        logger.info(f"[{project.name}] Updated manifest {manifest_path}")

    # 5. GitHub Push Mirror
    stage_github_mirror(project, forgejo_token, github_token, head_tip, dry_run)

    # 6. Reconfigure local remotes
    stage_reconfigure_remotes(project, dry_run)

    # 7. CI verification
    ci_ok = stage_verify_pipeline(project, wp_id, woodpecker_token, dry_run, wait_ci)
    return ci_ok


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Automated project migration orchestrator"
    )
    parser.add_argument("--repo", help="Name or repo of a single project to migrate")
    parser.add_argument(
        "--batch", help="Batch number or name (e.g. '1', 'clean-python')"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Simulate actions without mutating state"
    )
    parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Skip creating backup tarball and bundle",
    )
    parser.add_argument(
        "--no-wait-ci",
        action="store_true",
        help="Do not wait for CI pipeline to finish",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("config/ci/projects.json"),
        help="Path to projects.json manifest",
    )
    args = parser.parse_args()

    if not args.repo and not args.batch:
        parser.error("Either --repo or --batch must be specified")

    manifest_path = args.manifest.resolve()
    manifest_data = load_manifest(manifest_path)

    # Determine projects to migrate
    target_names: list[str] = []
    if args.repo:
        target_names.append(args.repo)
    elif args.batch:
        batch_key = str(args.batch).strip().lower()
        if batch_key not in BATCH_MAPPING:
            parser.error(
                f"Unknown batch '{args.batch}'. Available: {list(BATCH_MAPPING.keys())}"
            )
        target_names.extend(BATCH_MAPPING[batch_key])

    logger.info(
        f"Target repositories to migrate: {target_names} (dry_run={args.dry_run})"
    )

    # Tokens
    forgejo_token = get_forgejo_admin_token()
    woodpecker_token = get_woodpecker_jwt_token()
    github_token = get_github_auth_token()

    successes = 0
    for name in target_names:
        project = find_project(manifest_data, name)
        ok = migrate_project(
            project=project,
            manifest_data=manifest_data,
            manifest_path=manifest_path,
            forgejo_token=forgejo_token,
            woodpecker_token=woodpecker_token,
            github_token=github_token,
            dry_run=args.dry_run,
            skip_backup=args.skip_backup,
            wait_ci=not args.no_wait_ci,
        )
        if ok or args.dry_run:
            successes += 1
        else:
            logger.error(f"Migration for {name} failed; stopping batch.")
            return 1

    logger.info(f"Completed migration for {successes}/{len(target_names)} projects.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
