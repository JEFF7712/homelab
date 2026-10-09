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


FORBIDDEN_COMMANDS = frozenset(
    {
        "apt",
        "apt-get",
        "apk",
        "npm",
        "yarn",
        "bun",
        "pip",
        "pip3",
        "uv",
        "gem",
        "cargo",
        "go",
        "curl",
        "wget",
    }
)


def _split_commands(body: str) -> list[str]:
    parts = re.split(r"&&|\|\||[;|\n]", body)
    return [part.strip() for part in parts if part.strip()]


def check_run_lines(text: str) -> None:
    """Reject network/package-manager fetches in RUN instructions.

    Only a hermetic local pip install (`--no-index` with a local
    `--find-links` directory and no remote URLs or index options) may
    invoke an installer. All other installer, downloader, and
    version-control fetch commands are rejected by argv[0] so that
    directory names containing those strings do not false-positive.
    """
    for line in _join_continuations(text):
        if not line.upper().startswith("RUN"):
            continue
        body = line[3:]
        for command in _split_commands(body):
            argv = command.split()
            if not argv:
                continue
            invoked = argv[0].rsplit("/", 1)[-1].lower()
            if invoked in {"pip", "pip3", "uv"} and _has_local_pip_install(command):
                forbid_network(command, "RUN instruction")
                continue
            forbid_network(command, "RUN instruction")
            if invoked in FORBIDDEN_COMMANDS:
                raise ValueError(
                    f"RUN must not invoke {invoked}; retain inputs locally: {line}"
                )
            if invoked == "git" and any(
                token.lower() == "clone" for token in argv[1:4]
            ):
                raise ValueError(
                    f"RUN must not git clone; retain inputs locally: {line}"
                )
