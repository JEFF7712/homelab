"""Compare rendered website manifests by meaning, not by text.

Each site under gitops/websites is rendered with `kubectl kustomize` and parsed,
so comments, key order and YAML formatting drop out. Named list entries
(containers, ports, volumes, env) are keyed by name, which keeps a reorder or an
insertion from reporting every later element as changed. Migrating a site to a
generated manifest must produce no output here against the pre-migration ref;
a normalization change must produce exactly the fields it intends to change.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WEBSITES = "gitops/websites"
WORKTREE = "WORKTREE"

Site = dict[str, Any]


def resource_key(doc: dict[str, Any]) -> str:
    metadata = doc.get("metadata", {})
    namespace = metadata.get("namespace", "")
    return f"{doc['kind']}/{namespace}/{metadata['name']}"


def canonical_site(documents: list[Any]) -> Site:
    site: Site = {}
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        key = resource_key(doc)
        if key in site:
            raise ValueError(f"duplicate resource {key}")
        site[key] = doc
    return dict(sorted(site.items()))


def _by_name(value: list[Any]) -> dict[str, Any] | None:
    names = [
        item["name"] for item in value if isinstance(item, dict) and "name" in item
    ]
    if len(names) != len(value) or len(set(names)) != len(value):
        return None
    return {item["name"]: item for item in value}


def diff_values(before: Any, after: Any, path: str = "") -> list[tuple[str, Any, Any]]:
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        changes = []
        for key in sorted(before.keys() | after.keys()):
            child = f"{path}.{key}" if path else str(key)
            changes += diff_values(
                before.get(key, _MISSING), after.get(key, _MISSING), child
            )
        return changes
    if isinstance(before, list) and isinstance(after, list):
        old, new = _by_name(before), _by_name(after)
        if old is not None and new is not None:
            changes = []
            for name in sorted(old.keys() | new.keys()):
                changes += diff_values(
                    old.get(name, _MISSING), new.get(name, _MISSING), f"{path}[{name}]"
                )
            return changes
        if len(before) == len(after):
            changes = []
            for index, (item, other) in enumerate(zip(before, after, strict=True)):
                changes += diff_values(item, other, f"{path}[{index}]")
            return changes
    return [(path, before, after)]


def diff_sites(base: dict[str, Site], head: dict[str, Site]) -> list[str]:
    lines: list[str] = []
    for site in sorted(base.keys() | head.keys()):
        if site not in head:
            lines.append(f"{site}: site removed")
            continue
        if site not in base:
            lines.append(f"{site}: site added")
            continue
        old, new = base[site], head[site]
        for key in sorted(old.keys() | new.keys()):
            if key not in new:
                lines.append(f"{site}: {key} removed")
                continue
            if key not in old:
                lines.append(f"{site}: {key} added")
                continue
            for path, before, after in diff_values(old[key], new[key]):
                lines.append(f"{site}: {key} {path}: {_show(before)} -> {_show(after)}")
    return lines


class _Missing:
    pass


_MISSING = _Missing()


def _show(value: Any) -> str:
    if isinstance(value, _Missing):
        return "<absent>"
    return json.dumps(value, sort_keys=True)


def render_site(directory: Path) -> Site:
    output = subprocess.run(
        ["kubectl", "kustomize", str(directory)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return canonical_site(list(yaml.safe_load_all(output)))


def render_tree(root: Path) -> dict[str, Site]:
    websites = root / WEBSITES
    return {
        directory.name: render_site(directory)
        for directory in sorted(websites.iterdir())
        if (directory / "kustomization.yaml").is_file()
    }


def render_ref(ref: str) -> dict[str, Site]:
    if ref == WORKTREE:
        return render_tree(ROOT)
    archive = subprocess.run(
        ["git", "-C", str(ROOT), "archive", "--format=tar", ref, WEBSITES],
        check=True,
        capture_output=True,
    ).stdout
    with tempfile.TemporaryDirectory(prefix="websites-") as temporary:
        with tarfile.open(fileobj=BytesIO(archive)) as tar:
            tar.extractall(temporary, filter="data")
        return render_tree(Path(temporary))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot", help="print canonical JSON")
    snapshot.add_argument("--ref", default=WORKTREE)
    diff = commands.add_parser("diff", help="print field-level changes")
    diff.add_argument("base", help="git ref to compare from")
    diff.add_argument("head", nargs="?", default=WORKTREE)
    args = parser.parse_args(argv)

    if args.command == "snapshot":
        json.dump(render_ref(args.ref), sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    lines = diff_sites(render_ref(args.base), render_ref(args.head))
    for line in lines:
        print(line)
    return 1 if lines else 0


if __name__ == "__main__":
    raise SystemExit(main())
