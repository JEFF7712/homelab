"""Fail on trailing whitespace in repository Markdown files.

Skipped trees are not owned prose: vendored dependency content (for example,
OpenCode manages .opencode/node_modules itself and ignores it via
.opencode/.gitignore) and CAD sources under 3d-prints/ (binaries, generated
code, and PRDs that use Markdown hard-break trailing spaces).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote, urlsplit

from scripts.agent.__main__ import build_parser

ROOT = Path(__file__).resolve().parents[2]

SKIP_DIRECTORIES = frozenset(
    {".git", ".agent-state", ".agent-cache", ".worktrees", "node_modules", "3d-prints"}
)
MAINTAINED_DOCS = ("README.md", "AGENTS.md", "AGENT_MAP.md", "docs/agent-workflow.md")
CLI_CONTRACTS = {
    "agent-context": ("context", ("--json", "--task")),
    "agent-run": ("agent-run", ()),
    "agent-verify": ("verify", ("--json", "--task", "--record")),
    "doctor": ("doctor", ("--json",)),
    "check-changed": ("check-changed", ("--json", "--select-only")),
    "task-new": ("task-new", ("--json", "--template")),
    "task-resume": ("task-resume", ("--json",)),
    "task-checkpoint": ("task-checkpoint", ("--json",)),
    "task-export": ("task-export", ("--replace",)),
    "status": ("status", ("--json", "--timeout", "--record")),
}


def maintained_docs(root: Path) -> Iterator[Path]:
    for relative in MAINTAINED_DOCS:
        path = root / relative
        if path.is_file():
            yield path
    for relative in ("docs/runbooks", "docs/network", "docs/decisions"):
        yield from sorted((root / relative).rglob("*.md"))


def prose_lines(path: Path, *, strip_code: bool = False) -> Iterator[tuple[int, str]]:
    fence = ""
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            value = marker.group(1)
            if not fence:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence):
                fence = ""
            continue
        if not fence:
            yield number, re.sub(r"`+[^`]*`+", "", line) if strip_code else line


def _anchors(path: Path) -> set[str]:
    result = set()
    counts: dict[str, int] = {}
    for _, line in prose_lines(path):
        heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if heading:
            slug = re.sub(r"[^\w -]", "", heading.group(1).lower()).replace(" ", "-")
            count = counts.get(slug, 0)
            counts[slug] = count + 1
            result.add(f"{slug}-{count}" if count else slug)
        result.update(re.findall(r"(?:id|name)=[\"\']([^\"\']+)[\"\']", line))
    return result


def local_link_errors(root: Path) -> list[str]:
    errors = []
    for path in maintained_docs(root):
        for number, line in prose_lines(path, strip_code=True):
            targets = re.findall(r"\[[^\]\n]*\]\((<[^>]+>|[^)\n]+)\)", line)
            definition = re.match(r"^\s{0,3}\[[^\]]+\]:\s*(<[^>]+>|\S+)", line)
            if definition:
                targets.append(definition.group(1))
            for value in targets:
                target = value[1:-1] if value.startswith("<") else value.split()[0]
                url = urlsplit(target)
                if url.scheme or url.netloc:
                    continue
                destination = (
                    (root / unquote(url.path).lstrip("/"))
                    if url.path.startswith("/")
                    else (path.parent / unquote(url.path) if url.path else path)
                )
                prefix = f"{path.relative_to(root)}:{number}"
                if not destination.exists():
                    errors.append(f"{prefix}: missing local link target {target}")
                elif (
                    url.fragment
                    and destination.suffix == ".md"
                    and unquote(url.fragment).removeprefix("user-content-")
                    not in _anchors(destination)
                ):
                    errors.append(f"{prefix}: missing Markdown anchor {target}")
    return errors


def command_contract_errors(root: Path) -> list[str]:
    result = subprocess.run(
        [
            "just",
            "--justfile",
            str(root / "justfile"),
            "--dump",
            "--dump-format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode:
        return ["justfile: cannot inspect recipe contracts with just --dump"]
    recipes = json.loads(result.stdout)["recipes"]
    errors = []
    for path in maintained_docs(root):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for recipe in re.findall(
                r"(?:`|^\s*)just\s+([a-z][a-z0-9-]*)(?![a-z0-9*-])", line
            ):
                if recipe not in recipes:
                    errors.append(
                        f"{path.relative_to(root)}:{number}: unknown just recipe {recipe}"
                    )
    parser = build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ).choices
    for recipe, (command, required_options) in CLI_CONTRACTS.items():
        if recipe not in recipes:
            errors.append(f"justfile: missing workflow recipe {recipe}")
            continue
        body = "\n".join(
            row[0] if row and isinstance(row[0], str) else ""
            for row in recipes[recipe]["body"]
        )
        if not re.search(
            rf"\bpython -m scripts\.agent {re.escape(command)}(?:\s|$)", body
        ):
            errors.append(f"justfile: {recipe} must invoke scripts.agent {command}")
        if command not in subparsers:
            errors.append(f"agent CLI: missing command {command}")
            continue
        options = {
            option
            for action in subparsers[command]._actions
            for option in action.option_strings
        }
        for option in required_options:
            if option not in options:
                errors.append(
                    f"agent CLI: {command} is missing documented option {option}"
                )
    return errors


def iter_markdown_files(root: Path) -> Iterator[Path]:
    for path in root.rglob("*.md"):
        if SKIP_DIRECTORIES.intersection(path.parts):
            continue
        yield path


def trailing_whitespace_errors(root: Path) -> list[str]:
    errors = []
    for path in iter_markdown_files(root):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.rstrip() != line:
                errors.append(f"{path.relative_to(root)}:{number}: trailing whitespace")
    return errors


def main() -> int:
    errors = trailing_whitespace_errors(ROOT) + local_link_errors(ROOT)
    try:
        errors.extend(command_contract_errors(ROOT))
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        errors.append(f"documentation contracts unavailable: {type(error).__name__}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
