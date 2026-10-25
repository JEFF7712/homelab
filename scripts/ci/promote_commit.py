import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from scripts.ci.operation import SupersededSource, current_source

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


def _api(
    token: str, path: str, *, data: dict | None = None, method: str | None = None
) -> dict:
    payload = None if data is None else json.dumps(data).encode()
    request = urllib.request.Request(
        FORGEJO_API + path,
        data=payload,
        method=method,
        headers={
            "Authorization": f"token {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        return json.loads(body) if body else {}


LEGACY_BRANCH = re.compile(r"registry-promotion/[0-9a-f]{12}-[0-9a-f]{12}")


def superseded_pulls(pulls: list[dict], label: str, branch: str) -> list[dict]:
    """Open promotion PRs this run replaces: same scope, other content.

    Legacy branches keyed by main commit predate scopes; whichever scope runs
    first retires them, and the other scope regenerates its own if needed.
    """
    prefix = f"registry-promotion/{label}-"
    return [
        pull
        for pull in pulls
        if pull["head"]["ref"] != branch
        and (
            pull["head"]["ref"].startswith(prefix)
            or LEGACY_BRANCH.fullmatch(pull["head"]["ref"])
        )
    ]


def _assert_nix_agent_only(changed: list[str]) -> None:
    consumers = {
        "gitops/websites/nixagent/site.yaml",
        "gitops/websites/nixagent/deployment.yaml",
    }
    if set(changed) != consumers | {"registry/images.lock.json"}:
        raise ValueError("Automatic promotion may change only the nix-agent digest")
    for path in sorted(consumers):
        _assert_digest_only(path)
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


DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def _assert_digest_only(path: str) -> None:
    previous = subprocess.check_output(["git", "show", f"HEAD:{path}"], text=True)
    if DIGEST.sub("sha256:", previous) != DIGEST.sub("sha256:", Path(path).read_text()):
        raise ValueError(f"Automatic promotion changed more than digests in {path}")


def _assert_first_party_digests_only(changed: list[str]) -> None:
    """Automatic first-party promotion may only move first-party digests.

    GitOps consumers must differ from HEAD only in digests, the lock only in
    records for first-party (apps/) repositories, and the inventory, which
    is derived from both, only in digests and tags. Anything else, such as a
    new repository, a policy field, or another image, needs a human.
    """
    allowed_static = {"registry/images.lock.json", "registry/images.inventory.json"}
    for path in changed:
        if path in allowed_static:
            continue
        if not (path.startswith("gitops/") and path.endswith((".yaml", ".yml"))):
            raise ValueError(f"Automatic promotion may not change {path}")
        _assert_digest_only(path)
    if "registry/images.lock.json" in changed:
        previous = json.loads(
            subprocess.check_output(
                ["git", "show", "HEAD:registry/images.lock.json"], text=True
            )
        )
        current = json.loads(Path("registry/images.lock.json").read_text())

        def others(lock: dict) -> tuple[list[str], dict]:
            lock = dict(lock)
            lock.pop("generated_at", None)
            images = lock.pop("images", [])
            first_party = sorted(
                item["id"]
                for item in images
                if item.get("destination_repository", "").startswith("apps/")
            )
            lock["images"] = sorted(
                (
                    item
                    for item in images
                    if not item.get("destination_repository", "").startswith("apps/")
                ),
                key=lambda item: json.dumps(item, sort_keys=True),
            )
            return first_party, lock

        if others(previous) != others(current):
            raise ValueError("Automatic promotion changed non-first-party lock policy")


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
            # Forgejo reports "statuses": null before the first status lands.
            for item in status.get("statuses") or []
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
            print(f"Required validation passed; merged promotion PR #{number}")
            return
        if latest and latest.get("status") in {"failure", "error"}:
            raise ValueError("Required homelab validation failed for promotion PR")
        time.sleep(15)
    raise TimeoutError("Timed out waiting for homelab validation on promotion PR")


def main() -> None:
    try:
        current_source(dict(os.environ))
    except SupersededSource:
        if os.environ.get("CI_PIPELINE_EVENT") == "deployment":
            raise
        print("Skipping promotion: main moved on; the newer commit promotes")
        return
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", "--", "registry/images.lock.json", "gitops"],
        text=True,
    ).splitlines()
    if not changed:
        return
    scope = os.environ.get("CI_AUTO_MERGE_PROMOTION", "")
    auto_merge = scope in {"nix-agent-site", "first-party"}
    if scope == "nix-agent-site":
        _assert_nix_agent_only(changed)
    elif scope == "first-party":
        _assert_first_party_digests_only(changed)
    if any(not Path(path).is_file() for path in changed):
        raise ValueError("Promotion cannot delete files")
    content = b"".join(
        path.encode() + b"\0" + _change_key(path, Path(path).read_bytes())
        for path in sorted(changed)
    )
    # Keyed by scope and content, not by main commit: a rerun on a newer main
    # with the same promotion finds its open PR instead of opening another.
    label = scope or "review"
    branch = f"registry-promotion/{label}-{hashlib.sha256(content).hexdigest()[:12]}"
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
    if not recovery:
        for pull in superseded_pulls(pulls, label, branch):
            _api(
                token,
                f"/pulls/{pull['number']}",
                data={"state": "closed"},
                method="PATCH",
            )
            print(f"Closed superseded promotion PR #{pull['number']}")
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
