import hashlib
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from scripts.ci.operation import current_source

FORGEJO_API = "http://git.internal:3000/api/v1/repos/JEFF7712/homelab"
REQUIRED_CONTEXT = "ci/woodpecker/validation-v2"


def _change_key(path: str, data: bytes) -> bytes:
    if path == "registry/images.lock.json":
        try:
            payload = json.loads(data)
        except ValueError:
            return data
        if isinstance(payload, dict):
            payload.pop("generated_at", None)
            return json.dumps(payload, sort_keys=True).encode()
    return data


def _api(token: str, path: str, *, data: dict | None = None) -> dict:
    payload = None if data is None else json.dumps(data).encode()
    request = urllib.request.Request(
        FORGEJO_API + path,
        data=payload,
        headers={
            "Authorization": f"token {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        return json.loads(body) if body else {}


def _assert_nix_agent_only(changed: list[str]) -> None:
    allowed = {
        "registry/images.lock.json",
        "gitops/websites/nixagent/deployment.yaml",
    }
    if set(changed) != allowed:
        raise ValueError("Automatic promotion may change only the nix-agent digest")
    previous = json.loads(
        subprocess.check_output(
            ["git", "show", "HEAD:registry/images.lock.json"], text=True
        )
    )
    current = json.loads(Path("registry/images.lock.json").read_text())
    previous.pop("generated_at", None)
    current.pop("generated_at", None)
    previous_images = previous.get("images", [])
    current_images = current.get("images", [])
    old_target = [
        item
        for item in previous_images
        if item.get("destination_repository") == "apps/nix-agent-site"
    ]
    new_target = [
        item
        for item in current_images
        if item.get("destination_repository") == "apps/nix-agent-site"
    ]
    if len(old_target) != 1 or len(new_target) != 1:
        raise ValueError("Automatic promotion requires one locked nix-agent image")
    previous["images"] = sorted(
        (
            item
            for item in previous_images
            if item.get("destination_repository") != "apps/nix-agent-site"
        ),
        key=lambda item: json.dumps(item, sort_keys=True),
    )
    current["images"] = sorted(
        (
            item
            for item in current_images
            if item.get("destination_repository") != "apps/nix-agent-site"
        ),
        key=lambda item: json.dumps(item, sort_keys=True),
    )
    if previous != current or old_target == new_target:
        raise ValueError("Automatic promotion changed unrelated lock policy")


def _auto_merge_pull(token: str, pull: dict) -> None:
    number = int(pull["number"])
    _api(token, f"/pulls/{number}/update?style=rebase", data={})
    current = _api(token, f"/pulls/{number}")
    expected_sha = current["head"]["sha"]
    expected_base_sha = current["base"]["sha"]
    for _ in range(120):
        current = _api(token, f"/pulls/{number}")
        if current.get("head", {}).get("sha") != expected_sha:
            raise ValueError("Promotion PR head changed while awaiting validation")
        if current.get("base", {}).get("sha") != expected_base_sha:
            _api(token, f"/pulls/{number}/update?style=rebase", data={})
            updated = _api(token, f"/pulls/{number}")
            expected_sha = updated["head"]["sha"]
            expected_base_sha = updated["base"]["sha"]
            time.sleep(15)
            continue
        status = _api(token, f"/commits/{expected_sha}/status")
        matches = [
            item
            for item in status.get("statuses", [])
            if item.get("context") == REQUIRED_CONTEXT
        ]
        latest = max(matches, key=lambda item: item.get("updated_at", ""), default=None)
        if latest and latest.get("status") == "success":
            try:
                _api(token, f"/pulls/{number}/merge", data={"Do": "merge"})
            except urllib.error.HTTPError as error:
                if (
                    error.code != 405
                    or b"head branch is behind the base branch" not in error.read()
                ):
                    raise
                updated = _api(token, f"/pulls/{number}/update?style=rebase", data={})
                updated = _api(token, f"/pulls/{number}")
                expected_sha = updated["head"]["sha"]
                expected_base_sha = updated["base"]["sha"]
                time.sleep(15)
                continue
            print(
                f"Required validation passed; merged nix-agent promotion PR #{number}"
            )
            return
        if latest and latest.get("status") in {"failure", "error"}:
            raise ValueError("Required homelab validation failed for promotion PR")
        time.sleep(15)
    raise TimeoutError("Timed out waiting for homelab validation on promotion PR")


def main() -> None:
    current_source(dict(os.environ))
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", "--", "registry/images.lock.json", "gitops"],
        text=True,
    ).splitlines()
    if not changed:
        return
    auto_merge = os.environ.get("CI_AUTO_MERGE_PROMOTION") == "nix-agent-site"
    if auto_merge:
        _assert_nix_agent_only(changed)
    if any(not Path(path).is_file() for path in changed):
        raise ValueError("Promotion cannot delete files")
    content = b"".join(
        path.encode() + b"\0" + _change_key(path, Path(path).read_bytes())
        for path in sorted(changed)
    )
    branch = f"registry-promotion/{os.environ['CI_COMMIT_SHA'][:12]}-{hashlib.sha256(content).hexdigest()[:12]}"
    recovery = os.environ.get("CI_OPERATION_AUTHORITY") == "gitlab"
    token_name = "GITLAB_PUBLISH_TOKEN" if recovery else "FORGEJO_PUBLISH_TOKEN"
    token = os.environ[token_name]
    base = (
        "https://gitlab.com/api/v4/projects/85910419/merge_requests"
        if recovery
        else "https://git.rupan.dev/api/v1/repos/JEFF7712/homelab/pulls"
    )
    headers = {"Content-Type": "application/json"}
    headers.update(
        {"PRIVATE-TOKEN": token} if recovery else {"Authorization": f"token {token}"}
    )
    request = urllib.request.Request(
        base + ("?state=opened&per_page=100" if recovery else "?state=open&limit=100"),
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        pulls = json.load(response)
    existing = next(
        (
            pull
            for pull in pulls
            if (pull["source_branch"] if recovery else pull["head"]["ref"]) == branch
        ),
        None,
    )
    if existing:
        print(
            f"Promotion is already awaiting review: {existing['web_url' if recovery else 'html_url']}"
        )
        if auto_merge:
            _auto_merge_pull(token, existing)
        return
    subprocess.run(["git", "add", "--", *changed], check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Homelab CI",
            "-c",
            "user.email=ci@rupan.dev",
            "commit",
            "-m",
            "registry: promote first-party image digests",
        ],
        check=True,
    )
    with tempfile.TemporaryDirectory(prefix="forgejo-publish-") as temporary:
        remote = (
            "https://gitlab.com/JEFF7712/homelab.git"
            if recovery
            else "https://git.rupan.dev/JEFF7712/homelab.git"
        )
        credential_host = "https://gitlab.com" if recovery else "https://git.rupan.dev"
        helper = Path(temporary) / "credential"
        helper.write_text(
            '#!/usr/bin/env bash\nif [[ $1 == get ]]; then\n  printf "%s\\n" "username=$CI_PUBLISH_USERNAME" "password=$CI_PUBLISH_TOKEN"\nfi\n'
        )
        helper.chmod(0o700)
        subprocess.run(
            [
                "git",
                "-c",
                "credential.helper=",
                "-c",
                f"credential.{credential_host}.helper={helper}",
                "push",
                remote,
                f"HEAD:refs/heads/{branch}",
            ],
            check=True,
            env=dict(
                os.environ,
                CI_PUBLISH_TOKEN=token,
                CI_PUBLISH_USERNAME="oauth2" if recovery else "homelab-publisher",
            ),
        )
    request = urllib.request.Request(
        base,
        data=json.dumps(
            {
                **(
                    {"target_branch": "main", "source_branch": branch}
                    if recovery
                    else {"base": "main", "head": branch}
                ),
                "title": "registry: promote first-party image digests",
            }
        ).encode(),
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        pull = json.load(response)
    print(
        f"Promotion is ready for review: {pull['web_url' if recovery else 'html_url']}"
    )
    if auto_merge:
        _auto_merge_pull(token, pull)


if __name__ == "__main__":
    main()
