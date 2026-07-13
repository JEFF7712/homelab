#!/usr/bin/env python3
"""Validate the repository's Argo CD deployment inventory."""

from __future__ import annotations

import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


LOCAL_REPOS = {
    "https://gitlab.com/JEFF7712/homelab.git",
    "https://github.com/JEFF7712/homelab.git",
}

ALLOWED_APPS_KINDS = {
    "Application",
    "BackupTarget",
    "Namespace",
    "RecurringJob",
}

EXCLUDED_INFRASTRUCTURE_DIRS = {
    "autocompress",
}


@dataclass(frozen=True)
class ManifestDocument:
    path: Path
    index: int
    body: dict[str, Any]

    @property
    def label(self) -> str:
        return f"{self.path} doc {self.index}"


@dataclass(frozen=True)
class ValidationResult:
    applications: int
    errors: list[str]


def load_documents(repo_root: Path) -> tuple[list[ManifestDocument], list[str]]:
    documents: list[ManifestDocument] = []
    errors: list[str] = []

    for root_name in ("apps", "bootstrap", "infrastructure", "secrets"):
        root = repo_root / root_name
        chart_template_roots = {chart.parent / "templates" for chart in root.rglob("Chart.yaml")}
        for manifest in sorted((*root.rglob("*.yaml"), *root.rglob("*.yml"))):
            if any(template_root in manifest.parents for template_root in chart_template_roots):
                continue
            relative_path = manifest.relative_to(repo_root)
            try:
                with manifest.open("r", encoding="utf-8") as handle:
                    for index, body in enumerate(yaml.safe_load_all(handle), start=1):
                        if isinstance(body, dict):
                            documents.append(ManifestDocument(relative_path, index, body))
            except yaml.YAMLError as exc:
                errors.append(f"{relative_path}: YAML parse failed: {exc}")

    return documents, errors


def source_directories(repo_root: Path, document: ManifestDocument) -> tuple[list[tuple[Path, bool]], list[str]]:
    spec = document.body.get("spec") or {}
    sources = spec.get("sources") or [spec.get("source") or {}]
    directories: list[tuple[Path, bool]] = []
    errors: list[str] = []

    for source in sources:
        if source.get("repoURL") not in LOCAL_REPOS or source.get("path") is None:
            continue

        source_path = source["path"]
        candidate = Path(source_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            errors.append(f"{document.label}: unsafe local source path {source_path!r}")
            continue

        target = repo_root / candidate
        if not target.exists():
            errors.append(f"{document.label}: missing local source directory {source_path!r}")
            continue
        if not target.resolve().is_relative_to(repo_root.resolve()):
            errors.append(f"{document.label}: local source path {source_path!r} resolves outside the repository")
            continue
        if not target.is_dir():
            errors.append(f"{document.label}: local source path is not a directory {source_path!r}")
            continue

        recursive = (source.get("directory") or {}).get("recurse") is True
        directories.append((candidate, recursive))

    return directories, errors


def validate_repository(repo_root: Path) -> ValidationResult:
    documents, errors = load_documents(repo_root)
    applications = [document for document in documents if document.body.get("kind") == "Application"]
    documents_by_parent: dict[Path, list[ManifestDocument]] = defaultdict(list)
    names: dict[str, list[str]] = defaultdict(list)

    for document in documents:
        documents_by_parent[document.path.parent].append(document)

        if document.path.parts[0] == "apps":
            kind = document.body.get("kind", "<missing>")
            if kind not in ALLOWED_APPS_KINDS:
                errors.append(f"{document.label}: unexpected kind {kind} under apps/")

    for application in applications:
        metadata = application.body.get("metadata") or {}
        name = metadata.get("name", "<unnamed>")
        names[name].append(application.label)
        if metadata.get("namespace") != "argocd":
            errors.append(f"{application.label} ({name}): Application metadata namespace must be 'argocd'")

    for name, locations in sorted(names.items()):
        if len(locations) > 1:
            errors.append(f"duplicate Application name {name!r}: {', '.join(locations)}")

    root_applications = [application for application in applications if application.path == Path("bootstrap/root-app.yaml")]
    if len(root_applications) != 1:
        errors.append("bootstrap/root-app.yaml must contain exactly one Application")

    reachable: set[tuple[Path, int]] = set()
    referenced_infrastructure: set[str] = set()
    queue = deque(root_applications)

    while queue:
        application = queue.popleft()
        identity = (application.path, application.index)
        if identity in reachable:
            continue
        reachable.add(identity)

        directories, source_errors = source_directories(repo_root, application)
        errors.extend(error for error in source_errors if error not in errors)

        for directory, recursive in directories:
            if len(directory.parts) >= 2 and directory.parts[0] == "infrastructure":
                referenced_infrastructure.add(directory.parts[1])

            source_documents = (
                document
                for parent, nested_documents in documents_by_parent.items()
                if parent == directory or (recursive and directory in parent.parents)
                for document in nested_documents
            )
            for document in source_documents:
                if document.body.get("kind") == "Application":
                    queue.append(document)

    for application in applications:
        _, source_errors = source_directories(repo_root, application)
        errors.extend(error for error in source_errors if error not in errors)
        if (application.path, application.index) not in reachable:
            errors.append(f"{application.label}: Application is unreachable from bootstrap/root-app.yaml")

    infrastructure_root = repo_root / "infrastructure"
    for directory in sorted(path for path in infrastructure_root.iterdir() if path.is_dir()):
        if directory.name in EXCLUDED_INFRASTRUCTURE_DIRS:
            continue
        if directory.name not in referenced_infrastructure:
            relative_path = directory.relative_to(repo_root)
            errors.append(f"{relative_path}: no reachable local Application references this directory")

    return ValidationResult(applications=len(applications), errors=errors)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    result = validate_repository(repo_root)

    if result.errors:
        print("Argo CD deployment inventory validation failed:", file=sys.stderr)
        for error in result.errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(f"Validated {result.applications} reachable Argo CD Applications.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
