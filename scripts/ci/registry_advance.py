"""Advance eligible upstream image updates on a schedule, one step per run.

Renovate proposes image updates as PRs that change one declaration and carry
their own inventory and lock records. Those records overlap every other
registry change, so a Renovate branch goes stale as soon as anything else
merges and the merge policy refuses it (35 sat for a week in 2026-11). The
schedule therefore takes only Renovate's declaration change and rebuilds it
on current main, adding just that image's records (scripts/registry/advance.py
`mirror`); merging that mirrors the image through registry-lock-import and
Renovate closes its own PR. Workloads then move in a separate change
(scripts/registry/ordering.py). Each run takes one step:

- adopt: a consumer references an upstream mirror digest no longer declared,
  and the single declared replacement is locked and served by the registry:
  open a PR that moves that repository's consumers and merge it on the
  required checks;
- mirror: otherwise rebuild the oldest eligible open Renovate image bump on
  current main as its own PR and merge it on the required checks. Every run
  rebuilds from main and force-pushes the schedule's branch, so a PR left
  stale by an interrupted run is refreshed rather than stuck.

Before either step, a run settles the newest adopt it merged in the last day:
once Flux has applied it, the moved workloads must roll out and run the new
digests (scripts/registry/advance.py `verify`, through the read-only
registry-advance identity). A healthy adopt is marked verified; a failed one
is reverted by a PR that names the digest, and that digest is never adopted
again. Nothing new is adopted while an adopt is unsettled.

Eligible updates keep the same major version, outside the denied namespaces
and repositories (config/registry/advance-policy.json). Every consumer is a
Deployment whose main container has a readiness probe, or a CronJob, and
none uses the image in an init container. A RollingUpdate keeps the old pod
serving; a Recreate restarts and is reverted if it does not come back; a
CronJob's next run fails and alerts. StatefulSets, DaemonSets and everything
else stay with a person or the controller.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

from scripts.registry.advance import (
    SOURCE,
    adoptions,
    declarations,
    declared,
    mirror,
    verify,
)

FORGEJO = "https://git.rupan.dev/api/v1/repos/JEFF7712/homelab"
REGISTRY = "https://registry.rupan.dev"
RENOVATE = "homelab-renovate"
POLICY = Path("config/registry/advance-policy.json")
DECLARATION = re.compile(r"^gitops/[^/]+/registry-sources\.ya?ml$")
RECORDS = {"registry/images.inventory.json", "registry/images.lock.json"}
IDENTITY = ["-c", "user.name=Homelab CI", "-c", "user.email=ci@rupan.dev"]
ADOPT_BRANCH = "registry-advance/adopt-"
REVERT_BRANCH = "registry-advance/revert-"
VERIFIED = "<!-- registry-advance: verified -->"
REVERTED = "<!-- registry-advance: reverted -->"
SETTLE_WINDOW = 24 * 3600
MIRROR_REFERENCE = re.compile(r"registry\.rupan\.dev/[^\s\"'@]+@sha256:[0-9a-f]{64}")
REVERTED_DIGEST = re.compile(r"^reverted: (\S+@sha256:[0-9a-f]{64})$", re.MULTILINE)


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
        kind = document.get("kind")
        spec = document.get("spec") or {}
        if kind == "CronJob":
            template = ((spec.get("jobTemplate") or {}).get("spec") or {}).get(
                "template"
            ) or {}
        elif kind == "Deployment":
            template = spec.get("template") or {}
        else:
            return f"{path}: {name} is a {kind}"
        pod = template.get("spec") or {}
        for container in pod.get("initContainers") or []:
            if old_reference in str(container.get("image", "")):
                return f"{path}: {name} uses it in an init container"
        for container in pod.get("containers") or []:
            if kind != "Deployment":
                continue
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


def bump_declaration(root: Path, old: str, new: str) -> str | None:
    """Apply a Renovate declaration bump to this tree; return the file changed.

    None when no declaration still names the old reference: main already moved
    past it, and Renovate closes its PR once main declares the new one.
    """
    for path in sorted((root / "gitops").glob("*/registry-sources.y*ml")):
        text = path.read_text()
        if old in text:
            path.write_text(text.replace(old, new, 1))
            return path.relative_to(root).as_posix()
    return None


def rebuild_mirror(root: Path, old: str, new: str) -> list[str] | None:
    """Stage Renovate's bump plus only its new records, on the current tree.

    `mirror` resolves the declarations a commit added, so the bump is
    committed, mirrored against its parent, and the commit undone, leaving
    the declaration and records staged for one clean commit.
    """
    changed = bump_declaration(root, old, new)
    if changed is None:
        return None
    subprocess.run(["git", "add", "--", changed], cwd=root, check=True)
    subprocess.run(
        ["git", *IDENTITY, "commit", "-q", "-m", "declaration"], cwd=root, check=True
    )
    try:
        mirror(root, "HEAD~1")
    finally:
        subprocess.run(["git", "reset", "-q", "--soft", "HEAD~1"], cwd=root, check=True)
    paths = [changed, *sorted(RECORDS)]
    subprocess.run(["git", "add", "--", *paths], cwd=root, check=True)
    return paths


def publish(token: str, branch: str, title: str, body: str, paths: list[str]) -> None:
    """Commit paths on the current tree, force-push the schedule's branch, and
    merge its PR on the required checks."""
    from scripts.ci.promote_commit import _auto_merge_pull

    subprocess.run(["git", "add", "--", *paths], check=True)
    subprocess.run(
        ["git", *IDENTITY, "commit", "-q", "-m", f"{title}\n\n{body}"], check=True
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
                "--force",
                "https://git.rupan.dev/JEFF7712/homelab.git",
                f"HEAD:refs/heads/{branch}",
            ],
            check=True,
            env=dict(os.environ, CI_PUBLISH_TOKEN=token),
        )
    pull = next(
        (
            p
            for p in api(token, "/pulls?state=open&limit=100")
            if p["head"]["ref"] == branch
        ),
        None,
    )
    if pull is None:
        pull = api(
            token,
            "/pulls",
            data={"base": "main", "head": branch, "title": title, "body": body},
        )
        print(f"opened {pull['html_url']}")
    else:
        print(f"refreshed {pull['html_url']}")
    _auto_merge_pull(token, pull)


def publish_adopt(token: str, repository: str, digest: str, changed: list[str]) -> None:
    branch = f"registry-advance/adopt-{hashlib.sha256((repository + digest).encode()).hexdigest()[:12]}"
    title = f"apps: move {repository.removeprefix('upstream/')} to {digest[:19]}"
    body = (
        "Point the consumers at the mirror main already imports "
        "(scheduled registry-advance, scripts/ci/registry_advance.py)."
    )
    publish(token, branch, title, body, changed)


def publish_mirror(
    token: str, number: int, old: str, new: str, paths: list[str]
) -> None:
    branch = f"registry-advance/mirror-{hashlib.sha256((old + new).encode()).hexdigest()[:12]}"
    title = f"registry: declare {new.split('@')[0].removeprefix('docker.io/')}"
    body = (
        f"Renovate #{number}'s declaration change, rebuilt on current main with "
        "only its new inventory and lock records (scheduled registry-advance, "
        "scripts/ci/registry_advance.py). Renovate closes its PR once main "
        "declares the new image."
    )
    publish(token, branch, title, body, paths)


def mirror_pairs(diff: str) -> dict[str, dict[str, str]]:
    """{path: {new mirror reference: old one}} from an adopt PR's diff."""
    pairs: dict[str, dict[str, str]] = {}
    path = ""
    removed: list[str] = []
    added: list[str] = []

    def flush() -> None:
        if path and removed and len(removed) == len(added):
            pairs.setdefault(path, {}).update(dict(zip(added, removed, strict=True)))

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            flush()
            path, removed, added = "", [], []
        elif line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("-") and not line.startswith("---"):
            removed += MIRROR_REFERENCE.findall(line)
        elif line.startswith("+") and not line.startswith("+++"):
            added += MIRROR_REFERENCE.findall(line)
    flush()
    return pairs


def reverted_digests(pulls: Iterable[Mapping[str, Any]]) -> set[str]:
    """Mirror references a merged revert named; they are never adopted again."""
    return {
        reference
        for pull in pulls
        if str((pull.get("head") or {}).get("ref", "")).startswith(REVERT_BRANCH)
        and pull.get("merged")
        for reference in REVERTED_DIGEST.findall(pull.get("body") or "")
    }


def unsettled_adopt(
    pulls: Iterable[Mapping[str, Any]], comments: Any, now: float
) -> Mapping[str, Any] | None:
    """The newest adopt merged in the window with no verdict yet."""
    for pull in sorted(
        pulls, key=lambda p: str(p.get("merged_at") or ""), reverse=True
    ):
        if not str((pull.get("head") or {}).get("ref", "")).startswith(ADOPT_BRANCH):
            continue
        merged = parse_time(pull.get("merged_at"))
        if merged is None or now - merged > SETTLE_WINDOW:
            return None
        bodies = " ".join(c.get("body", "") for c in comments(pull["number"]))
        if VERIFIED in bodies or REVERTED in bodies:
            return None
        return pull
    return None


def parse_time(value: Any) -> float | None:
    if not value:
        return None
    import datetime

    return datetime.datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    ).timestamp()


def flux_applied(
    kustomizations: list[Mapping[str, Any]],
    paths: list[str],
    merged_at: float,
    committed: Any,
) -> bool:
    """Whether every Flux Kustomization owning a path applied a revision at
    least as new as the merge. Main only moves forward through merges, so a
    newer applied commit contains it."""
    for path in paths:
        owners = [
            k
            for k in kustomizations
            if path.startswith(
                str((k.get("spec") or {}).get("path", "")).lstrip("./") + "/"
            )
        ]
        if not owners:
            continue
        owner = max(owners, key=lambda k: len(str(k["spec"]["path"])))
        revision = str((owner.get("status") or {}).get("lastAppliedRevision", ""))
        sha = revision.rsplit(":", 1)[-1]
        applied = committed(sha) if sha else None
        if applied is None or applied < merged_at:
            return False
    return True


def settle(root: Path, token: str, pull: Mapping[str, Any]) -> str:
    """Verify an adopt the schedule merged, or revert it. Returns the outcome."""
    number = pull["number"]
    diff = api(token, f"/pulls/{number}.diff", raw=True)
    pairs = mirror_pairs(diff)
    paths = sorted(pairs)
    kubectl = os.environ.get("KUBECTL", "kubectl")
    kustomizations = json.loads(
        subprocess.run(
            [kubectl, "-n", "flux-system", "get", "kustomizations", "-o", "json"],
            check=True,
            capture_output=True,
            text=True,
            cwd="/tmp",
        ).stdout
    )["items"]

    def committed(sha: str) -> float | None:
        try:
            commit = api(token, f"/git/commits/{sha}")
        except urllib.error.HTTPError:
            return None
        return parse_time(
            ((commit.get("commit") or {}).get("committer") or {}).get("date")
        )

    merged_at = parse_time(pull.get("merged_at")) or time.time()
    if not flux_applied(kustomizations, paths, merged_at, committed):
        print(f"settle #{number}: waiting for Flux to apply it")
        return "waiting"
    failures = verify(root, paths, timeout="5m")
    if not failures:
        api(
            token,
            f"/issues/{number}/comments",
            data={"body": f"{VERIFIED}\nRolled out and running the new digests."},
        )
        print(f"settle #{number}: verified")
        return "verified"
    for path, rewrites in pairs.items():
        text = (root / path).read_text()
        for new, old in rewrites.items():
            text = text.replace(new, old)
        (root / path).write_text(text)
    names = sorted({new for rewrites in pairs.values() for new in rewrites})
    branch = f"{REVERT_BRANCH}{number}"
    title = f"apps: revert #{number}, its rollout failed"
    body = (
        f"#{number} did not roll out (scheduled registry-advance):\n\n"
        + "\n".join(f"- {failure}" for failure in failures)
        + "\n\n"
        + "\n".join(
            f"reverted: {name.removeprefix('registry.rupan.dev/')}" for name in names
        )
    )
    publish(token, branch, title, body, paths)
    api(
        token,
        f"/issues/{number}/comments",
        data={"body": f"{REVERTED}\nRollout failed; reverted by {branch}."},
    )
    print(f"settle #{number}: reverted")
    return "reverted"


def main() -> int:
    from scripts.ci.operation import SupersededSource, current_source

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
    closed = api(token, "/pulls?state=closed&sort=recentupdate&limit=50")
    pending = unsettled_adopt(
        closed,
        lambda number: api(token, f"/issues/{number}/comments"),
        time.time(),
    )
    if pending is not None and settle(root, token, pending) != "verified":
        return 0
    blocked = reverted_digests(closed)
    for repository, rewrites, digest in adopt_candidates(
        files, declarations(root), lock, policy
    ):
        if f"{repository}@{digest}" in blocked:
            print(
                f"adopt {repository}: {digest} was reverted; waiting for a newer image"
            )
            continue
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
        paths = rebuild_mirror(root, old, new)
        if paths is None:
            print(f"mirror #{number} {repository}: main no longer declares {old}")
            continue
        print(f"mirror #{number} {repository}: rebuilding on main")
        publish_mirror(token, number, old, new, paths)
        return 0
    print("nothing to advance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
