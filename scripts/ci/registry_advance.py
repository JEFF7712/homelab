"""Advance eligible upstream image updates on a schedule, one step per run.

Renovate's image PRs already carry the new declaration and its inventory and
lock records (post-upgrade `scripts.registry.declarations`), so merging one
mirrors the image through registry-lock-import. Workloads then move in a
separate change (scripts/registry/ordering.py). Each run takes one step:

- adopt: a consumer references an upstream mirror digest no longer declared,
  and the single declared replacement is locked and served by the registry:
  open a PR that moves that repository's consumers and merge it on the
  required checks;
- mirror: otherwise merge the oldest eligible open Renovate image PR on its
  required checks.

Only updates that cannot take a service down are eligible: the same major
version, every consumer a RollingUpdate Deployment whose container (not an
init container) has a readiness probe, outside the denied namespaces and
repositories (config/registry/advance-policy.json). A bad image stalls its
rollout while the old pod keeps serving, and KubeDeploymentReplicasMismatch
fires. Everything else stays with a person or the controller.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

from scripts.registry.advance import SOURCE, adoptions, declarations, declared

FORGEJO = "https://git.rupan.dev/api/v1/repos/JEFF7712/homelab"
REGISTRY = "https://registry.rupan.dev"
RENOVATE = "homelab-renovate"
POLICY = Path("config/registry/advance-policy.json")
DECLARATION = re.compile(r"^gitops/[^/]+/registry-sources\.ya?ml$")
RECORDS = {"registry/images.inventory.json", "registry/images.lock.json"}


def repository_of(reference: str) -> str:
    return "upstream/" + re.sub(r":[A-Za-z0-9._-]+$", "", reference.split("@")[0])


def major(tag: str | None) -> str | None:
    match = re.match(r"v?(\d+)", tag or "")
    return match[1] if match else None


def tags(lock: Mapping[str, Any], repository: str) -> dict[str, str | None]:
    """Upstream source tag of each locked digest of a mirror repository."""
    return {
        entry["digest"]: (entry.get("source") or {}).get("tag")
        for entry in lock["images"]
        if entry.get("destination_repository") == repository
    }


def renovate_bump(files: list[str], diff: str) -> tuple[str, str] | None:
    """(old, new) source reference of a Renovate PR that changes one declaration."""
    declarations_changed = [f for f in files if DECLARATION.match(f)]
    if len(declarations_changed) != 1 or set(files) - RECORDS - set(
        declarations_changed
    ):
        return None
    removed, added = [], []
    current = ""
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:]
        elif current == declarations_changed[0] and not line.startswith(("---", "+++")):
            if line.startswith("-"):
                removed += SOURCE.findall(line)
            elif line.startswith("+"):
                added += SOURCE.findall(line)
    if len(removed) != 1 or len(added) != 1:
        return None
    if repository_of(removed[0]) != repository_of(added[0]):
        return None
    return removed[0], added[0]


def workloads_using(
    files: Iterable[tuple[str, str]], reference: str
) -> list[tuple[str, dict]]:
    found = []
    for path, text in files:
        if reference not in text:
            continue
        for document in yaml.safe_load_all(text):
            if isinstance(document, dict) and reference in yaml.safe_dump(document):
                found.append((path, document))
    return found


def ineligible(
    repository: str,
    old_tag: str | None,
    new_tag: str | None,
    documents: list[tuple[str, dict]],
    old_reference: str,
    policy: Mapping[str, Any],
) -> str | None:
    """None when the scheduled path may take this update, else the reason."""
    if repository in policy.get("deny_repositories", []):
        return "repository is denied"
    if major(old_tag) is None or major(old_tag) != major(new_tag):
        return f"not a same-major update ({old_tag} -> {new_tag})"
    if not documents:
        return "no workload references the old mirror"
    for path, document in documents:
        metadata = document.get("metadata") or {}
        name = metadata.get("name")
        if metadata.get("namespace") in policy.get("deny_namespaces", []):
            return f"{path}: namespace {metadata.get('namespace')} is denied"
        if document.get("kind") != "Deployment":
            return f"{path}: {name} is a {document.get('kind')}"
        spec = document.get("spec") or {}
        if (spec.get("strategy") or {}).get("type", "RollingUpdate") != "RollingUpdate":
            return f"{path}: {name} does not roll"
        pod = (spec.get("template") or {}).get("spec") or {}
        for container in pod.get("initContainers") or []:
            if old_reference in str(container.get("image", "")):
                return f"{path}: {name} uses it in an init container"
        for container in pod.get("containers") or []:
            if old_reference in str(container.get("image", "")) and not container.get(
                "readinessProbe"
            ):
                return f"{path}: {name}/{container.get('name')} has no readiness probe"
    return None


def adopt_candidates(
    files: list[tuple[str, str]],
    current: Mapping[str, set[str]],
    lock: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> list[tuple[str, dict[str, dict[str, str]], str]]:
    """(repository, rewrites, new digest) per eligible repository, and why others wait."""
    locked = declared(lock)
    rewrites, _ = adoptions(files, current, locked)
    by_repository: dict[str, dict[str, dict[str, str]]] = {}
    for path, pairs in rewrites.items():
        for old, new in pairs.items():
            repository = re.sub(r"^registry\.rupan\.dev/", "", old.split("@")[0])
            repository = re.sub(r":[A-Za-z0-9._-]+$", "", repository)
            by_repository.setdefault(repository, {}).setdefault(path, {})[old] = new
    candidates = []
    for repository, repository_rewrites in sorted(by_repository.items()):
        known = tags(lock, repository)
        pairs = {(o, n) for p in repository_rewrites.values() for o, n in p.items()}
        olds = {o.split("@")[1] for o, _ in pairs}
        new = next(iter({n.split("@")[1] for _, n in pairs}))
        reasons = [
            ineligible(
                repository,
                known.get(old),
                known.get(new),
                workloads_using(files, f"{repository}@{old}"),
                f"{repository}@{old}",
                policy,
            )
            for old in sorted(olds)
        ]
        reason = next((r for r in reasons if r), None)
        if reason:
            print(f"adopt {repository}: not eligible: {reason}")
            continue
        candidates.append((repository, repository_rewrites, new))
    return candidates


def api(token: str, path: str, *, data: dict | None = None, raw: bool = False) -> Any:
    request = urllib.request.Request(
        FORGEJO + path,
        data=None if data is None else json.dumps(data).encode(),
        headers={"Authorization": f"token {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
    return body.decode() if raw else (json.loads(body) if body else {})


def registry_serves(repository: str, digest: str) -> bool:
    password = Path(os.environ["REGISTRY_NODE_PASSWORD_FILE"]).read_text().strip()
    auth = base64.b64encode(f"node:{password}".encode()).decode()
    request = urllib.request.Request(
        f"{REGISTRY}/v2/{repository}/manifests/{digest}",
        method="HEAD",
        headers={
            "Authorization": f"Basic {auth}",
            "Accept": ", ".join(
                [
                    "application/vnd.oci.image.index.v1+json",
                    "application/vnd.oci.image.manifest.v1+json",
                    "application/vnd.docker.distribution.manifest.list.v2+json",
                    "application/vnd.docker.distribution.manifest.v2+json",
                ]
            ),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30):
            return True
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise


def publish_adopt(token: str, repository: str, digest: str, changed: list[str]) -> None:
    from scripts.ci.promote_commit import _auto_merge_pull

    branch = f"registry-advance/adopt-{hashlib.sha256((repository + digest).encode()).hexdigest()[:12]}"
    existing = next(
        (
            p
            for p in api(token, "/pulls?state=open&limit=100")
            if p["head"]["ref"] == branch
        ),
        None,
    )
    if existing:
        print(f"adopt {repository}: awaiting {existing['html_url']}")
        _auto_merge_pull(token, existing)
        return
    title = f"apps: move {repository.removeprefix('upstream/')} to {digest[:19]}"
    body = (
        "Point the consumers at the mirror main already imports "
        "(scheduled registry-advance, scripts/ci/registry_advance.py)."
    )
    subprocess.run(["git", "add", "--", *changed], check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Homelab CI",
            "-c",
            "user.email=ci@rupan.dev",
            "commit",
            "-q",
            "-m",
            f"{title}\n\n{body}",
        ],
        check=True,
    )
    with tempfile.TemporaryDirectory(prefix="forgejo-publish-") as temporary:
        helper = Path(temporary) / "credential"
        helper.write_text(
            "#!/usr/bin/env bash\nif [[ $1 == get ]]; then\n"
            '  printf "%s\\n" "username=homelab-publisher" "password=$CI_PUBLISH_TOKEN"\nfi\n'
        )
        helper.chmod(0o700)
        subprocess.run(
            [
                "git",
                "-c",
                "credential.helper=",
                "-c",
                f"credential.https://git.rupan.dev.helper={helper}",
                "push",
                "https://git.rupan.dev/JEFF7712/homelab.git",
                f"HEAD:refs/heads/{branch}",
            ],
            check=True,
            env=dict(os.environ, CI_PUBLISH_TOKEN=token),
        )
    pull = api(
        token,
        "/pulls",
        data={"base": "main", "head": branch, "title": title, "body": body},
    )
    print(f"adopt {repository}: opened {pull['html_url']}")
    _auto_merge_pull(token, pull)


def main() -> int:
    from scripts.ci.operation import SupersededSource, current_source
    from scripts.ci.promote_commit import _auto_merge_pull

    try:
        current_source(dict(os.environ))
    except SupersededSource:
        print("Skipping: main moved on; the newer commit advances")
        return 0
    root = Path.cwd()
    token = os.environ["FORGEJO_PUBLISH_TOKEN"]
    policy = json.loads(POLICY.read_text())
    lock = json.loads((root / "registry/images.lock.json").read_text())
    files = [
        (path.relative_to(root).as_posix(), path.read_text())
        for path in sorted((root / "gitops").rglob("*"))
        if path.is_file() and path.suffix in (".yaml", ".yml")
    ]
    for repository, rewrites, digest in adopt_candidates(
        files, declarations(root), lock, policy
    ):
        if not registry_serves(repository, digest):
            print(f"adopt {repository}: waiting for the import of {digest}")
            continue
        for path, pairs in rewrites.items():
            text = (root / path).read_text()
            for old, new in pairs.items():
                text = text.replace(old, new)
            (root / path).write_text(text)
        publish_adopt(token, repository, digest, sorted(rewrites))
        return 0
    for pull in sorted(
        api(token, "/pulls?state=open&limit=100"), key=lambda p: p["number"]
    ):
        if (pull.get("user") or {}).get("login") != RENOVATE:
            continue
        number = pull["number"]
        changed = [f["filename"] for f in api(token, f"/pulls/{number}/files")]
        found = renovate_bump(changed, api(token, f"/pulls/{number}.diff", raw=True))
        if found is None:
            continue
        old, new = found
        repository = repository_of(new)
        old_digest = old.split("@")[1]
        reason = ineligible(
            repository,
            old.split("@")[0].rsplit(":", 1)[-1] if ":" in old.split("@")[0] else None,
            new.split("@")[0].rsplit(":", 1)[-1] if ":" in new.split("@")[0] else None,
            workloads_using(files, f"{repository}@{old_digest}"),
            f"{repository}@{old_digest}",
            policy,
        )
        if reason:
            print(f"mirror #{number} {repository}: not eligible: {reason}")
            continue
        print(f"mirror #{number} {repository}: merging on the required checks")
        _auto_merge_pull(token, pull)
        return 0
    print("nothing to advance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
