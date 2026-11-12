"""Advance upstream images in two ordered steps, so agents can take Renovate's bumps.

Renovate bumps an upstream declaration (`registry-sources.yaml` or
`base-images.yaml`). Nothing runs it until the image is mirrored and the
consumers point at the mirror, and those must land in order
(scripts/registry/ordering.py):

`mirror --base <ref>` resolves only the declarations the change added since
<ref> and merges their records into `registry/images.inventory.json` and
`registry/images.lock.json`. A full regeneration rewrites unrelated record IDs
(#570), so nothing else is touched. A destination tag another record already
holds is dropped, because `registry-lock-import` never repoints a tag; the
retention tag still names the digest.

`adopt` then rewrites each consumer that references an upstream mirror digest
no longer declared, when exactly one current declaration of that repository
is locked. Anything ambiguous is reported and left alone.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping
from typing import Any

INVENTORY = "registry/images.inventory.json"
LOCK = "registry/images.lock.json"
DECLARATIONS = re.compile(r"(^|/)(registry-sources|base-images)\.ya?ml$")
SOURCE = re.compile(
    r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?::\d+)?/[^\s\"'@]+@sha256:[0-9a-f]{64}"
)
MIRROR = re.compile(
    r"registry\.rupan\.dev/(upstream/[A-Za-z0-9._/-]+?)(?::[A-Za-z0-9._-]+)?@(sha256:[0-9a-f]{64})"
)


def git(root: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout


def added_sources(diff: str) -> set[str]:
    """Upstream references on lines a unified diff adds to declaration files."""
    found: set[str] = set()
    current = ""
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else ""
        elif line.startswith("+") and DECLARATIONS.search(current):
            found.update(SOURCE.findall(line))
    return {ref for ref in found if not ref.startswith("registry.rupan.dev/")}


def select(
    inventory: Mapping[str, Any], existing: set[str], sources: set[str]
) -> list[dict]:
    """New upstream inventory records whose source is one of the added references."""
    return [
        record
        for record in inventory["images"]
        if record["id"] not in existing
        and record.get("kind") == "upstream"
        and (record.get("source") or {}).get("reference") in sources
    ]


def merge_lock(lock: dict[str, Any], records: Iterable[dict[str, Any]]) -> list[str]:
    """Add lock records by id, dropping destination tags another record holds."""
    held: dict[str, set[str]] = {}
    for entry in lock["images"]:
        held.setdefault(entry["destination_repository"], set()).update(
            entry.get("destination_tags", [])
        )
    present = {entry["id"] for entry in lock["images"]}
    added = []
    for record in records:
        if record["id"] in present:
            continue
        taken = held.setdefault(record["destination_repository"], set())
        record = dict(record)
        record["destination_tags"] = [
            tag for tag in record.get("destination_tags", []) if tag not in taken
        ]
        taken.update(record["destination_tags"])
        present.add(record["id"])
        lock["images"].append(record)
        added.append(record["id"])
    lock["images"].sort(key=lambda entry: entry["id"])
    return added


def mirror(root: pathlib.Path, base: str) -> list[str]:
    sources = added_sources(
        git(root, "diff", f"{base}...HEAD", "--", "gitops", "registry")
    )
    if not sources:
        return []
    committed_inventory = json.loads((root / INVENTORY).read_text())
    with tempfile.TemporaryDirectory(prefix="registry-advance-") as scratch:
        work = pathlib.Path(scratch)
        run = [sys.executable, "-m", "scripts.registry", "--root", str(root)]
        subprocess.run(
            [*run, "inventory", "--output", str(work / "inventory.json")],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        fresh = json.loads((work / "inventory.json").read_text())
        records = select(
            fresh, {r["id"] for r in committed_inventory["images"]}, sources
        )
        missing = sources - {r["source"]["reference"] for r in records}
        if missing:
            raise SystemExit(
                "no new inventory record for: " + ", ".join(sorted(missing))
            )
        fresh["images"] = records
        (work / "subset.json").write_text(json.dumps(fresh))
        subprocess.run(
            [
                *run,
                "resolve",
                "--inventory",
                str(work / "subset.json"),
                "--output",
                str(work / "lock.json"),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        resolved = json.loads((work / "lock.json").read_text())["images"]
    if {r["id"] for r in resolved} != {r["id"] for r in records}:
        raise SystemExit("resolve did not return every selected record")
    committed_inventory["images"].extend(records)
    (root / INVENTORY).write_text(json.dumps(committed_inventory, indent=2) + "\n")
    lock = json.loads((root / LOCK).read_text())
    added = merge_lock(lock, resolved)
    (root / LOCK).write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    return added


def declared(lock: Mapping[str, Any]) -> dict[str, set[str]]:
    """Locked upstream digests by destination repository."""
    found: dict[str, set[str]] = {}
    for entry in lock["images"]:
        if entry.get("kind") == "upstream":
            found.setdefault(entry["destination_repository"], set()).add(
                entry["digest"]
            )
    return found


def declarations(root: pathlib.Path) -> dict[str, set[str]]:
    """Currently declared upstream digests by mirror repository."""
    found: dict[str, set[str]] = {}
    for path in sorted((root / "gitops").rglob("*")):
        if path.is_file() and DECLARATIONS.search(path.name):
            for reference in SOURCE.findall(path.read_text()):
                name, digest = reference.split("@")
                repository = re.sub(r":[A-Za-z0-9._-]+$", "", name)
                found.setdefault("upstream/" + repository, set()).add(digest)
    return found


def adoptions(
    files: Iterable[tuple[str, str]],
    current: Mapping[str, set[str]],
    locked: Mapping[str, set[str]],
) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Per file, old -> new mirror references to rewrite, and what was skipped."""
    rewrites: dict[str, dict[str, str]] = {}
    skipped: list[str] = []
    for path, text in files:
        if DECLARATIONS.search(path):
            continue
        for match in MIRROR.finditer(text):
            repository, digest = match[1], match[2]
            wanted = current.get(repository, set())
            if not wanted or digest in wanted:
                continue
            ready = wanted & locked.get(repository, set())
            if len(wanted) != 1 or not ready:
                skipped.append(
                    f"{path}: {repository}@{digest} has {len(wanted)} declarations, "
                    f"{len(ready)} locked"
                )
                continue
            new = next(iter(ready))
            old = match[0]
            rewrites.setdefault(path, {})[old] = (
                f"registry.rupan.dev/{repository}@{new}"
            )
    return rewrites, skipped


def adopt(root: pathlib.Path) -> tuple[list[str], list[str]]:
    files = [
        (path.relative_to(root).as_posix(), path.read_text())
        for path in sorted((root / "gitops").rglob("*"))
        if path.is_file() and path.suffix in (".yaml", ".yml")
    ]
    lock = json.loads((root / LOCK).read_text())
    rewrites, skipped = adoptions(files, declarations(root), declared(lock))
    changed = []
    for path, pairs in rewrites.items():
        target = root / path
        text = target.read_text()
        for old, new in pairs.items():
            text = text.replace(old, new)
        target.write_text(text)
        changed.append(path)
    return changed, skipped


WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet"}


def workloads(text: str) -> list[tuple[str, str, str, set[str]]]:
    """(kind, namespace, name, mirror digests) for each workload in a manifest."""
    import yaml

    found = []
    for document in yaml.safe_load_all(text):
        if not isinstance(document, dict) or document.get("kind") not in WORKLOADS:
            continue
        metadata = document.get("metadata") or {}
        digests = {match[2] for match in MIRROR.finditer(yaml.safe_dump(document))}
        if digests:
            found.append(
                (
                    document["kind"],
                    metadata.get("namespace", "default"),
                    metadata["name"],
                    digests,
                )
            )
    return found


def verify(root: pathlib.Path, paths: list[str], timeout: str = "10m") -> list[str]:
    """Wait for each changed workload and require its pods to run the new digests."""
    import os

    kubectl = os.environ.get("KUBECTL", "kubectl")
    failures = []
    for path in paths:
        for kind, namespace, name, digests in workloads((root / path).read_text()):
            target = f"{kind.lower()}/{name}"
            rollout = subprocess.run(
                [
                    kubectl,
                    "-n",
                    namespace,
                    "rollout",
                    "status",
                    target,
                    f"--timeout={timeout}",
                ],
                capture_output=True,
                text=True,
                cwd="/tmp",
            )
            if rollout.returncode:
                failures.append(f"{namespace}/{target}: {rollout.stderr.strip()[:200]}")
                continue
            selector = subprocess.run(
                [
                    kubectl,
                    "-n",
                    namespace,
                    "get",
                    target,
                    "-o",
                    "jsonpath={.spec.selector.matchLabels}",
                ],
                capture_output=True,
                text=True,
                cwd="/tmp",
                check=True,
            ).stdout
            labels = ",".join(
                f"{k}={v}" for k, v in json.loads(selector or "{}").items()
            )
            pods = json.loads(
                subprocess.run(
                    [
                        kubectl,
                        "-n",
                        namespace,
                        "get",
                        "pods",
                        "-l",
                        labels,
                        "-o",
                        "json",
                    ],
                    capture_output=True,
                    text=True,
                    cwd="/tmp",
                    check=True,
                ).stdout
            )
            # Init containers report separately and have finished by now.
            running = {
                status.get("imageID", "").rsplit("@", 1)[-1]
                for pod in pods["items"]
                if (pod.get("status") or {}).get("phase") == "Running"
                for key in ("initContainerStatuses", "containerStatuses")
                for status in (pod.get("status") or {}).get(key, [])
            }
            if not digests <= running:
                failures.append(
                    f"{namespace}/{target}: running {sorted(running)}, wanted {sorted(digests)}"
                )
    return failures


def cronjobs(text: str) -> list[tuple[str, str, set[str]]]:
    """(namespace, name, mirror digests) for each CronJob in a manifest."""
    import yaml

    found = []
    for document in yaml.safe_load_all(text):
        if not isinstance(document, dict) or document.get("kind") != "CronJob":
            continue
        metadata = document.get("metadata") or {}
        digests = {match[2] for match in MIRROR.finditer(yaml.safe_dump(document))}
        if digests:
            found.append(
                (metadata.get("namespace", "default"), metadata["name"], digests)
            )
    return found


def scheduled_runs(
    root: pathlib.Path, paths: list[str], since: float
) -> tuple[list[str], list[str]]:
    """Failures and pending checks for each changed CronJob's runs on its new image.

    A CronJob is never run off schedule: several delete, upload or take hours.
    Its next scheduled run after the change is the check. A run counts when
    the CronJob created it after `since` with every new digest in its pod
    template; one that completed verifies the CronJob, and failed runs with
    none completed fail it.
    """
    import datetime
    import os

    kubectl = os.environ.get("KUBECTL", "kubectl")
    failures: list[str] = []
    waiting: list[str] = []
    for path in paths:
        for namespace, name, digests in cronjobs((root / path).read_text()):
            jobs = json.loads(
                subprocess.run(
                    [kubectl, "-n", namespace, "get", "jobs", "-o", "json"],
                    capture_output=True,
                    text=True,
                    cwd="/tmp",
                    check=True,
                ).stdout
            )["items"]
            runs = []
            for job in jobs:
                metadata = job.get("metadata") or {}
                owners = metadata.get("ownerReferences") or []
                if not any(
                    owner.get("kind") == "CronJob" and owner.get("name") == name
                    for owner in owners
                ):
                    continue
                created = datetime.datetime.fromisoformat(
                    str(metadata.get("creationTimestamp", ""))
                ).timestamp()
                template = json.dumps((job.get("spec") or {}).get("template") or {})
                ran = {match[2] for match in MIRROR.finditer(template)}
                if created >= since and digests <= ran:
                    runs.append(job)
            states = [
                {
                    condition.get("type")
                    for condition in (job.get("status") or {}).get("conditions") or []
                    if condition.get("status") == "True"
                }
                for job in runs
            ]
            target = f"{namespace}/cronjob/{name}"
            if any("Complete" in state for state in states):
                continue
            if any("Failed" in state for state in states):
                failures.append(f"{target}: its run on the new image failed")
            elif runs:
                waiting.append(f"{target}: its run on the new image is still running")
            else:
                waiting.append(f"{target}: no scheduled run on the new image yet")
    return failures, waiting


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    mirror_parser = commands.add_parser("mirror", help="lock the newly declared images")
    mirror_parser.add_argument("--base", required=True)
    commands.add_parser("adopt", help="point consumers at the declared mirrors")
    verify_parser = commands.add_parser(
        "verify", help="require adopted workloads to roll out on their new digests"
    )
    verify_parser.add_argument("paths", nargs="+")
    arguments = parser.parse_args(argv)
    if arguments.command == "mirror":
        added = mirror(arguments.root, arguments.base)
        print(json.dumps({"locked": added}))
        return 0
    if arguments.command == "verify":
        failures = verify(arguments.root, arguments.paths)
        print(json.dumps({"failures": failures}, indent=1))
        return 1 if failures else 0
    changed, skipped = adopt(arguments.root)
    print(json.dumps({"rewritten": changed, "skipped": skipped}, indent=1))
    return 1 if skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
