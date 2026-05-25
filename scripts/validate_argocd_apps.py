#!/usr/bin/env python3
"""Validate repo-local Argo CD Application sources."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml


LOCAL_REPOS = {
    "https://gitlab.com/JEFF7712/homelab.git",
    "https://github.com/JEFF7712/homelab.git",
}


def iter_yaml_documents(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        yield from yaml.safe_load_all(handle)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    search_roots = [repo_root / "apps", repo_root / "bootstrap", repo_root / "infrastructure"]
    errors: list[str] = []
    checked = 0

    for root in search_roots:
        for manifest in sorted(root.rglob("*.yaml")):
            rel_manifest = manifest.relative_to(repo_root)
            try:
                docs = iter_yaml_documents(manifest)
                for index, doc in enumerate(docs, start=1):
                    if not isinstance(doc, dict):
                        continue
                    if doc.get("kind") != "Application":
                        continue

                    spec = doc.get("spec") or {}
                    source = spec.get("source") or {}
                    repo_url = source.get("repoURL")
                    source_path = source.get("path")
                    app_name = (doc.get("metadata") or {}).get("name", "<unnamed>")

                    if repo_url not in LOCAL_REPOS or source_path is None:
                        continue

                    checked += 1
                    if Path(source_path).is_absolute() or ".." in Path(source_path).parts:
                        errors.append(f"{rel_manifest} doc {index} ({app_name}): unsafe path {source_path!r}")
                        continue

                    target = repo_root / source_path
                    if not target.exists():
                        errors.append(f"{rel_manifest} doc {index} ({app_name}): missing path {source_path!r}")
                    elif not target.is_dir():
                        errors.append(f"{rel_manifest} doc {index} ({app_name}): path is not a directory {source_path!r}")
            except yaml.YAMLError as exc:
                errors.append(f"{rel_manifest}: YAML parse failed: {exc}")

    if errors:
        print("Argo CD Application path validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(f"Validated {checked} repo-local Argo CD Application paths.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
