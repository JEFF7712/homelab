import hashlib
import json
import os
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from scripts.ci.operation import current_source


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


def main() -> None:
    current_source(dict(os.environ))
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", "--", "registry/images.lock.json", "gitops"],
        text=True,
    ).splitlines()
    if not changed:
        return
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
        else "http://git.internal:3000/api/v1/repos/JEFF7712/homelab/pulls"
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
            else "http://git.internal:3000/JEFF7712/homelab.git"
        )
        credential_host = (
            "https://gitlab.com" if recovery else "http://git.internal:3000"
        )
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


if __name__ == "__main__":
    main()
