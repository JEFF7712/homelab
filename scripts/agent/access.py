"""Live access checks for every credential path agents use to ship changes.

`just doctor --live` runs these so a missing token or SSH identity shows up
before a task, not as a 401 halfway through it. Each check is read-only.
Secrets are read only to authenticate and are never printed.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

Runner = Callable[[Sequence[str], float], tuple[int, str]]
Fetch = Callable[[str, dict[str, str]], int]

WOODPECKER = "http://ci.internal:8000"
OBSERVED_STALE_SECONDS = 7 * 24 * 3600


def run(command: Sequence[str], timeout: float) -> tuple[int, str]:
    try:
        result = subprocess.run(
            list(command), capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return 124, str(error)
    return result.returncode, (result.stdout + result.stderr).strip()


def fetch(url: str, headers: dict[str, str]) -> int:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (urllib.error.URLError, OSError):
        return 0


def check(name: str, ok: bool, detail: str, remedy: str) -> dict[str, str]:
    return {
        "name": f"access:{name}",
        "status": "pass" if ok else "fail",
        "detail": detail,
        "remedy": "" if ok else remedy,
    }


def tea_login(home: Path) -> dict[str, Any] | None:
    try:
        config = yaml.safe_load((home / ".config/tea/config.yml").read_text())
    except (OSError, yaml.YAMLError):
        return None
    logins = (config or {}).get("logins") or []
    return next((item for item in logins if item.get("default")), None) or (
        logins[0] if logins else None
    )


def age_recipients(root: Path) -> set[str]:
    rules = yaml.safe_load((root / ".sops.yaml").read_text())["creation_rules"]
    return {
        recipient.strip()
        for rule in rules
        for recipient in str(rule.get("age", "")).split(",")
        if recipient.strip()
    }


def access_checks(
    root: Path,
    home: Path,
    hosts: dict[str, str],
    runner: Runner = run,
    fetcher: Fetch = fetch,
    now: float | None = None,
) -> list[dict[str, str]]:
    checks = []

    login = tea_login(home)
    if login is None:
        checks.append(
            check(
                "forgejo-api",
                False,
                "no tea login",
                "run tea login add --url http://git.internal:3000 as homelab-agent "
                "(docs/runbooks/ci-pipelines.md)",
            )
        )
    else:
        status = fetcher(
            login["url"].rstrip("/") + "/api/v1/repos/JEFF7712/homelab",
            {"Authorization": "token " + login["token"]},
        )
        checks.append(
            check(
                "forgejo-api",
                status == 200,
                f"{login.get('user', '?')} at {login['url']}: HTTP {status}",
                "rotate the homelab-agent token (docs/runbooks/ci-pipelines.md)",
            )
        )

    code, output = runner(
        ["git", "ls-remote", "git-agent:JEFF7712/homelab.git", "HEAD"], 15
    )
    checks.append(
        check(
            "forgejo-ssh",
            code == 0,
            "git-agent push identity reachable" if code == 0 else output[-200:],
            "configure the git-agent SSH alias and register its key on homelab-agent",
        )
    )

    token_file = home / ".config/woodpecker/token"
    token = os.environ.get("WOODPECKER_TOKEN") or (
        token_file.read_text().strip() if token_file.is_file() else ""
    )
    status = (
        fetcher(f"{WOODPECKER}/api/user", {"Authorization": "Bearer " + token})
        if token
        else 0
    )
    checks.append(
        check(
            "woodpecker-api",
            status == 200,
            f"HTTP {status}" if token else "no token",
            "save a Woodpecker personal token to ~/.config/woodpecker/token",
        )
    )

    code, output = runner(["kubectl", "get", "--raw", "/readyz"], 15)
    checks.append(
        check(
            "kubernetes",
            code == 0,
            output[-200:] or "ready",
            "fix the kube context for the homelab-01 API server",
        )
    )

    key_file = Path(
        os.environ.get("SOPS_AGE_KEY_FILE", home / ".config/sops/age/keys.txt")
    )
    code, public = (
        runner(["age-keygen", "-y", str(key_file)], 10)
        if key_file.is_file()
        else (1, "")
    )
    recipients = age_recipients(root)
    matched = code == 0 and bool(set(public.split()) & recipients)
    checks.append(
        check(
            "sops-age",
            matched,
            "key decrypts repository secrets"
            if matched
            else f"{key_file} missing or not a .sops.yaml recipient",
            "install the homelab age key at ~/.config/sops/age/keys.txt",
        )
    )

    for name, address in sorted(hosts.items()):
        code, output = runner(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=5",
                f"rupan@{address}",
                "true",
            ],
            10,
        )
        checks.append(
            check(
                f"ssh:{name}",
                code == 0,
                address if code == 0 else output[-200:],
                f"check reachability and authorized keys for {name}",
            )
        )

    observed = root / "registry/observed-images.json"
    try:
        captured = datetime.fromisoformat(
            json.loads(observed.read_text())["observed_at"]
        ).timestamp()
        age = (now if now is not None else time.time()) - captured
    except (OSError, KeyError, ValueError):
        age = float("inf")
    checks.append(
        check(
            "registry-observed-snapshot",
            age <= OBSERVED_STALE_SECONDS,
            f"{age / 86400:.1f} days old" if age != float("inf") else "unreadable",
            "run just registry-snapshot-live and ship the refreshed snapshot",
        )
    )
    return checks
