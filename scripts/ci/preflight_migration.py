#!/usr/bin/env python3
"""Non-mutating preflight migration auditor.

Validates git repositories against Forgejo, GitHub, and local checkouts
before any migration steps are performed. Does not modify any state.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def load_tea_token() -> tuple[str, str]:
    """Read Forgejo base URL and token from ~/.config/tea/config.yml if available."""
    config_path = Path.home() / ".config" / "tea" / "config.yml"
    base_url = "http://git.internal:3000"
    token = ""
    if config_path.exists():
        for line in config_path.read_text().splitlines():
            if line.strip().startswith("url:"):
                base_url = line.split(":", 1)[1].strip()
            elif line.strip().startswith("token:"):
                token = line.split(":", 1)[1].strip()
    return base_url, token


def check_forgejo(repo_full_name: str, base_url: str, token: str) -> dict[str, Any]:
    url = f"{base_url}/api/v1/repos/{repo_full_name}"
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"token {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
            return {
                "exists": True,
                "private": data.get("private", True),
                "default_branch": data.get("default_branch", "main"),
                "html_url": data.get("html_url", ""),
                "empty": data.get("empty", False),
            }
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"exists": False}
        return {"exists": False, "error": f"HTTP {e.code}"}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"exists": False, "error": str(e)}


def check_github(repo_full_name: str) -> dict[str, Any]:
    try:
        out = subprocess.check_output(
            ["gh", "api", f"repos/{repo_full_name}"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        data = json.loads(out)
        return {
            "exists": True,
            "private": data.get("private", False),
            "default_branch": data.get("default_branch", "main"),
            "html_url": data.get("html_url", ""),
            "archived": data.get("archived", False),
        }
    except subprocess.CalledProcessError:
        return {"exists": False}
    except (OSError, ValueError) as e:
        return {"exists": False, "error": str(e)}


def check_local(local_path: str | None) -> dict[str, Any]:
    if not local_path or not os.path.exists(local_path):
        return {"exists": False}

    git_dir = os.path.join(local_path, ".git")
    if not os.path.exists(git_dir):
        return {"exists": False, "is_git": False}

    res: dict[str, Any] = {"exists": True, "is_git": True}

    # Branch
    try:
        branch = subprocess.check_output(
            ["git", "-C", local_path, "branch", "--show-current"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        res["branch"] = branch
    except subprocess.SubprocessError:
        res["branch"] = "unknown"

    # Status & untracked
    try:
        status_lines = (
            subprocess.check_output(
                ["git", "-C", local_path, "status", "--porcelain"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            .strip()
            .splitlines()
        )
        dirty = [item for item in status_lines if not item.startswith("??")]
        untracked = [item for item in status_lines if item.startswith("??")]
        res["clean"] = len(status_lines) == 0
        res["dirty_count"] = len(dirty)
        res["untracked_count"] = len(untracked)
        res["dirty_samples"] = [item.strip() for item in status_lines[:5]]
    except subprocess.SubprocessError:
        res["clean"] = False
        res["dirty_count"] = -1
        res["untracked_count"] = -1
        res["dirty_samples"] = []

    # Remotes
    try:
        remotes_out = subprocess.check_output(
            ["git", "-C", local_path, "remote", "-v"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        remotes = {}
        for line in remotes_out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and "(fetch)" in line:
                remotes[parts[0]] = parts[1]
        res["remotes"] = remotes
    except subprocess.SubprocessError:
        res["remotes"] = {}

    # Ahead / Behind origin
    try:
        rev_out = subprocess.check_output(
            [
                "git",
                "-C",
                local_path,
                "rev-list",
                "--left-right",
                "--count",
                "origin/HEAD...HEAD",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        behind, ahead = map(int, rev_out.split())
        res["ahead"] = ahead
        res["behind"] = behind
    except subprocess.SubprocessError:
        # Try with branch name
        try:
            b = res.get("branch", "main")
            rev_out = subprocess.check_output(
                [
                    "git",
                    "-C",
                    local_path,
                    "rev-list",
                    "--left-right",
                    "--count",
                    f"origin/{b}...HEAD",
                ],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            behind, ahead = map(int, rev_out.split())
            res["ahead"] = ahead
            res["behind"] = behind
        except subprocess.SubprocessError:
            res["ahead"] = None
            res["behind"] = None

    # LFS check
    attr_path = os.path.join(local_path, ".gitattributes")
    res["has_lfs"] = False
    if os.path.exists(attr_path):
        try:
            with open(attr_path, errors="ignore") as f:
                res["has_lfs"] = "filter=lfs" in f.read()
        except OSError:
            res["has_lfs"] = False

    return res


def run_preflight_for_repo(
    project: dict[str, Any], base_url: str, token: str
) -> dict[str, Any]:
    name = project["name"]
    gh_repo = project.get("github_repo")
    fj_repo = project.get("forgejo_repo", f"JEFF7712/{name}")
    local_path = project.get("local_path")

    gh_info = check_github(gh_repo) if gh_repo else {"exists": False}
    fj_info = check_forgejo(fj_repo, base_url, token)
    loc_info = check_local(local_path)

    warnings = []
    blockers = []

    # Visibility check
    expected_vis = project.get("visibility")
    if gh_info.get("exists"):
        gh_vis = "private" if gh_info.get("private") else "public"
        if gh_vis != expected_vis:
            warnings.append(
                f"GitHub visibility ({gh_vis}) differs from manifest ({expected_vis})"
            )

    if fj_info.get("exists"):
        fj_vis = "private" if fj_info.get("private") else "public"
        if fj_vis != expected_vis:
            warnings.append(
                f"Forgejo visibility ({fj_vis}) differs from manifest ({expected_vis})"
            )

    # Local working tree checks
    if loc_info.get("exists"):
        if not loc_info.get("clean"):
            dirty_msg = f"Local checkout has {loc_info.get('dirty_count')} modified and {loc_info.get('untracked_count')} untracked files"
            warnings.append(dirty_msg)
        if loc_info.get("ahead", 0):
            warnings.append(f"Local branch is ahead by {loc_info['ahead']} commit(s)")
        if loc_info.get("behind", 0):
            blockers.append(
                f"Local branch is behind origin by {loc_info['behind']} commit(s) (needs pull/rebase)"
            )
        if loc_info.get("has_lfs"):
            warnings.append(
                "Repository uses Git LFS (requires LFS object transfer verification)"
            )

    # Migration state check
    state = project.get("state")
    if state == "migrate" and fj_info.get("exists") and not fj_info.get("empty"):
        warnings.append("Forgejo destination already exists and is non-empty")

    return {
        "name": name,
        "state": state,
        "github": gh_info,
        "forgejo": fj_info,
        "local": loc_info,
        "warnings": warnings,
        "blockers": blockers,
        "ready": len(blockers) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Preflight check for repo migration")
    parser.add_argument("--repo", help="Specific repository name to check")
    parser.add_argument(
        "--state", help="Filter by state (e.g. migrate, already-primary)"
    )
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    manifest_path = (
        Path(__file__).resolve().parent.parent.parent
        / "config"
        / "ci"
        / "projects.json"
    )
    if not manifest_path.exists():
        print(f"Error: Manifest not found at {manifest_path}", file=sys.stderr)
        sys.exit(1)

    data = json.loads(manifest_path.read_text())
    projects = data.get("projects", [])

    base_url, token = load_tea_token()

    if args.repo:
        projects = [p for p in projects if p["name"] == args.repo]
        if not projects:
            print(
                f"Error: Repository '{args.repo}' not found in manifest",
                file=sys.stderr,
            )
            sys.exit(1)
    elif args.state:
        projects = [p for p in projects if p.get("state") == args.state]

    results = []
    for p in projects:
        res = run_preflight_for_repo(p, base_url, token)
        results.append(res)

    if args.json:
        print(json.dumps(results, indent=2))
        return

    print("=" * 80)
    print(
        f"{'REPOSITORY':<25} {'STATE':<16} {'LOCAL':<8} {'FORGEJO':<8} {'GITHUB':<8} {'STATUS'}"
    )
    print("=" * 80)

    for r in results:
        loc = (
            "clean"
            if r["local"].get("clean")
            else ("dirty" if r["local"].get("exists") else "n/a")
        )
        fj = "yes" if r["forgejo"].get("exists") else "no"
        gh = "yes" if r["github"].get("exists") else "no"
        status = "READY" if r["ready"] else "BLOCKED"
        if r["warnings"] and r["ready"]:
            status = f"WARN ({len(r['warnings'])})"
        print(f"{r['name']:<25} {r['state']:<16} {loc:<8} {fj:<8} {gh:<8} {status}")
        for b in r["blockers"]:
            print(f"   [BLOCKER] {b}")
        for w in r["warnings"]:
            print(f"   [WARN] {w}")

    print("=" * 80)


if __name__ == "__main__":
    main()
