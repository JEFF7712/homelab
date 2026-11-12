"""Render a gitops kustomization the way Flux builds it, for manifest tests.

ConfigMaps whose data comes from a `configMapGenerator` exist only in the
rendered output, so tests read them here instead of from a source manifest.
"""

from __future__ import annotations

import functools
import subprocess
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]


@functools.cache
def rendered(app: str) -> tuple[dict[str, Any], ...]:
    """Every object `kubectl kustomize gitops/<app>` produces."""
    output = subprocess.run(
        ["kubectl", "kustomize", str(ROOT / "gitops" / app)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return tuple(doc for doc in yaml.safe_load_all(output) if isinstance(doc, dict))


def config_map(app: str, name: str) -> dict[str, Any]:
    """The rendered ConfigMap `name` from gitops/<app>; fails when absent."""
    matches = [
        doc
        for doc in rendered(app)
        if doc["kind"] == "ConfigMap" and doc["metadata"]["name"] == name
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one ConfigMap {name} in gitops/{app}")
    return matches[0]
