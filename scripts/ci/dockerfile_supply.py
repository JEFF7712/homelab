from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

LOCAL_IMAGE = re.compile(
    r"registry\.rupan\.dev/upstream/[A-Za-z0-9_./-]+(?::[A-Za-z0-9_.-]+)?@sha256:[a-f0-9]{64}"
)
REMOTE = re.compile(r"https?://|ftp://", re.IGNORECASE)


@dataclass
class Supply:
    """Parsed Dockerfile supply surface."""

    path: Path
    bases: list[str] = field(default_factory=list)
    stages: set[str] = field(default_factory=set)
    copy_sources: list[str] = field(default_factory=list)


def _join_continuations(text: str) -> list[str]:
    lines: list[str] = []
    current = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            current += stripped[:-1] + " "
            continue
        current += stripped
        lines.append(current)
        current = ""
    if current.strip():
        lines.append(current.strip())
    return lines


def parse_dockerfile(path: Path) -> Supply:
    """Parse a Dockerfile, rejecting constructs that hide supply.

    Raises ValueError on line continuations inside comments, JSON-array
    forms are passed through untouched; only shell-form supply directives
    are inspected.
    """
    supply = Supply(path=path)
    text = path.read_text(encoding="utf-8")
    for line in _join_continuations(text):
        upper = line.upper()
        if upper.startswith("FROM"):
            tokens = line.split()
            image = ""
            stage = ""
            position = 1
            while position < len(tokens):
                token = tokens[position]
                if token.startswith("--platform="):
                    position += 1
                    continue
                if token.startswith("--"):
                    position += 2
                    continue
                if token.upper() == "AS":
                    if position + 1 < len(tokens):
                        stage = tokens[position + 1]
                    break
                if not image:
                    image = token
                position += 1
            if not image:
                raise ValueError(f"Unparseable FROM directive: {line}")
            if not LOCAL_IMAGE.fullmatch(image):
                raise ValueError(
                    "Base image must be a digest-pinned local upstream mirror "
                    f"registry.rupan.dev/upstream/<path>@sha256:<digest>: {line}"
                )
            supply.bases.append(image)
            if stage:
                if not re.fullmatch(r"[A-Za-z0-9_.-]+", stage):
                    raise ValueError(f"Invalid stage name: {stage}")
                supply.stages.add(stage)
        elif upper.startswith("COPY"):
            match = re.search(r"--from=([^\s]+)", line, re.IGNORECASE)
            if match:
                supply.copy_sources.append(match.group(1))
        elif upper.startswith("ADD"):
            raise ValueError("ADD is not allowed; use COPY of retained local files")
    return supply


def check_copy_sources(supply: Supply) -> None:
    """Every COPY --from must name a local stage or a local digest image."""
    for source in supply.copy_sources:
        if source in supply.stages:
            continue
        if LOCAL_IMAGE.fullmatch(source):
            continue
        raise ValueError(
            f"COPY --from must reference a declared stage or a digest-pinned "
            f"local image, got: {source}"
        )


def forbid_network(line: str, what: str) -> None:
    """Reject remote URLs in a Dockerfile instruction line."""
    if REMOTE.search(line):
        raise ValueError(f"{what} must not reference remote URLs: {line}")


def _has_local_pip_install(body: str) -> bool:
    """True when a pip install line is hermetic: local find-links, no index."""
    lowered = body.lower()
    if "pip" not in lowered and " uv " not in f" {lowered} ":
        return False
    if "--no-index" not in body:
        return False
    match = re.search(r"--find-links=([^\s]+)", body)
    if not match or "://" in match.group(1):
        return False
    for forbidden in (
        "--index-url",
        "--extra-index-url",
        "https://",
        "http://",
    ):
        if forbidden in body:
            return False
    return "install" in lowered


def check_run_lines(text: str) -> None:
    """Reject network/package-manager fetches in RUN instructions.

    The only permitted installer is a hermetic local pip install
    (`--no-index` with a local `--find-links` directory and no remote
    URLs or index options). Everything else that reaches the network
    or a distribution package manager is rejected.
    """
    for line in _join_continuations(text):
        if not line.upper().startswith("RUN"):
            continue
        body = line[3:]
        if _has_local_pip_install(body):
            forbid_network(body, "RUN instruction")
            continue
        forbid_network(body, "RUN instruction")
        lowered = body.lower()
        for pattern, name in (
            (r"\bapt(-get)?\b", "apt"),
            (r"\bapk\b", "apk"),
            (r"\bnpm\b", "npm"),
            (r"\byarn\b", "yarn"),
            (r"\bbun\b", "bun"),
            (r"\bpip\d?\b", "pip"),
            (r"\buv\b", "uv"),
            (r"\bgem\b", "gem"),
            (r"\bcargo\b", "cargo"),
            (r"\bgo\s+(install|get)\b", "go"),
            (r"\bgit\s+clone\b", "git clone"),
            (r"\bcurl\b", "curl"),
            (r"\bwget\b", "wget"),
        ):
            if re.search(pattern, lowered):
                raise ValueError(
                    f"RUN must not invoke {name}; retain inputs locally: {line}"
                )
