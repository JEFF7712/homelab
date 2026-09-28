from __future__ import annotations

import hashlib
import os
import posixpath
import stat
import subprocess
from pathlib import Path

from .models import (
    ChangeCounts,
    ChangedPath,
    ChangeKind,
    ChangeSource,
    GitBase,
    GitState,
)


class GitStateError(RuntimeError):
    pass


class GitBaseError(GitStateError):
    def __init__(self, base: str, message: str) -> None:
        self.base = base
        super().__init__(message)


MAX_GITLINK_DEPTH = 8


def _git(
    *arguments: str,
    cwd: Path | None = None,
    allow_failure: bool = False,
) -> bytes:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 and not allow_failure:
        message = os.fsdecode(result.stderr).strip() or "git command failed"
        raise GitStateError(message)
    return result.stdout


def _normalise_path(path: bytes) -> bytes:
    normalised = posixpath.normpath(path)
    if normalised == b".":
        raise GitStateError("git returned an empty repository path")
    return normalised.removeprefix(b"./")


def _without_output_terminator(output: bytes) -> bytes:
    return output[:-1] if output.endswith(b"\n") else output


def _excluded(path: bytes) -> bool:
    return (
        path == b".agent-state"
        or path.startswith(b".agent-state/")
        or path == b"3d-prints"
        or path.startswith(b"3d-prints/")
    )


def _kind(status: bytes) -> ChangeKind:
    code = status[:1]
    return {
        b"A": ChangeKind.ADDED,
        b"C": ChangeKind.COPIED,
        b"D": ChangeKind.DELETED,
        b"M": ChangeKind.MODIFIED,
        b"R": ChangeKind.RENAMED,
        b"T": ChangeKind.TYPE_CHANGED,
        b"U": ChangeKind.UNMERGED,
    }.get(code, ChangeKind.MODIFIED)


def _decode_path(path: bytes) -> str:
    return os.fsdecode(path)


def _parse_name_status(output: bytes, source: ChangeSource) -> list[ChangedPath]:
    fields = output.split(b"\0")
    changes: list[ChangedPath] = []
    index = 0
    while index < len(fields) - 1:
        status = fields[index]
        index += 1
        if not status:
            continue
        kind = _kind(status)
        if kind in (ChangeKind.RENAMED, ChangeKind.COPIED):
            old_path = _normalise_path(fields[index])
            path = _normalise_path(fields[index + 1])
            index += 2
            if _excluded(old_path) or _excluded(path):
                continue
            changes.append(
                ChangedPath(
                    path=_decode_path(path),
                    path_bytes=path,
                    kind=kind,
                    source=source,
                    old_path=_decode_path(old_path),
                    old_path_bytes=old_path,
                )
            )
            continue
        path = _normalise_path(fields[index])
        index += 1
        if _excluded(path):
            continue
        changes.append(
            ChangedPath(
                path=_decode_path(path),
                path_bytes=path,
                kind=kind,
                source=source,
            )
        )
    return changes


def _untracked(root: Path) -> list[ChangedPath]:
    output = _git("ls-files", "--others", "--exclude-standard", "-z", cwd=root)
    changes = []
    for raw_path in output.split(b"\0"):
        if not raw_path:
            continue
        path = _normalise_path(raw_path)
        if _excluded(path):
            continue
        changes.append(
            ChangedPath(
                path=_decode_path(path),
                path_bytes=path,
                kind=ChangeKind.UNTRACKED,
                source=ChangeSource.UNTRACKED,
            )
        )
    return changes


def _base_state(root: Path, head: str | None, base: str | None) -> GitBase:
    if base is None:
        return GitBase(False, None, None, None)
    if head is None:
        raise GitBaseError(
            base, f"Git base '{base}' is unavailable because HEAD has no commit"
        )
    resolved = _git(
        "rev-parse",
        "--verify",
        "--quiet",
        f"{base}^{{commit}}",
        cwd=root,
        allow_failure=True,
    )
    if not resolved:
        raise GitBaseError(
            base, f"Git base '{base}' is unavailable. Fetch or choose a valid commit."
        )
    resolved_base = _without_output_terminator(resolved).decode("ascii")
    merge_base = _git("merge-base", resolved_base, head, cwd=root, allow_failure=True)
    if not merge_base:
        raise GitBaseError(base, f"Git base '{base}' has no merge base with HEAD.")
    return GitBase(
        True,
        base,
        resolved_base,
        _without_output_terminator(merge_base).decode("ascii"),
    )


def _index_entries(root: Path, path: bytes) -> tuple[tuple[bytes, bytes, bytes], ...]:
    output = _git(
        "ls-files",
        "--stage",
        "-z",
        "--",
        _decode_path(path),
        cwd=root,
    )
    entries = []
    for record in output.split(b"\0"):
        metadata, separator, _ = record.partition(b"\t")
        if not separator:
            continue
        fields = metadata.split(b" ")
        if len(fields) != 3:
            raise GitStateError("git returned an invalid index entry")
        entries.append((fields[0], fields[1], fields[2]))
    return tuple(entries)


def _head_mode(root: Path, path: bytes) -> bytes:
    output = _git("ls-tree", "-z", "HEAD", "--", _decode_path(path), cwd=root)
    record = output.split(b"\0", 1)[0]
    metadata, separator, _ = record.partition(b"\t")
    fields = metadata.split(b" ")
    if not separator or len(fields) != 3:
        raise GitStateError("git returned an invalid HEAD tree entry")
    return fields[0]


def _conflict_content(root: Path, path: bytes) -> bytes:
    entries = _index_entries(root, path)
    stages = sorted(stage + b":" + object_id for _, object_id, stage in entries)
    if not stages:
        raise GitStateError("git returned no conflict stages for an unmerged path")
    return b"unmerged\0" + b"\0".join(stages)


def _repository_identity(root: Path) -> tuple[int, int]:
    try:
        metadata = root.stat()
    except OSError as error:
        raise GitStateError(f"cannot inspect gitlink '{root}': {error}") from error
    return metadata.st_dev, metadata.st_ino


def _write_change_fingerprint(
    digest: hashlib._Hash,
    root: Path,
    change: ChangedPath,
    depth: int,
    ancestors: frozenset[tuple[int, int]],
) -> None:
    digest.update(change.source.value.encode("ascii"))
    digest.update(b"\0")
    digest.update(change.kind.value.encode("ascii"))
    digest.update(b"\0")
    digest.update(change.old_path_bytes or b"")
    digest.update(b"\0")
    digest.update(change.path_bytes)
    digest.update(b"\0")
    content = _content(root, change, depth, ancestors)
    digest.update(b"missing\0" if content is None else hashlib.sha256(content).digest())
    digest.update(b"\0")


def _deduplicate_changes(changes: list[ChangedPath]) -> tuple[ChangedPath, ...]:
    deduplicated = {
        (change.source, change.kind, change.old_path_bytes, change.path_bytes): change
        for change in changes
    }
    return tuple(
        sorted(
            deduplicated.values(),
            key=lambda change: (
                change.source.value,
                change.kind.value,
                change.old_path_bytes or b"",
                change.path_bytes,
            ),
        )
    )


def _local_changes(root: Path) -> tuple[ChangedPath, ...]:
    changes = _parse_name_status(
        _git("diff", "--cached", "--name-status", "-z", "-M", cwd=root),
        ChangeSource.INDEX,
    )
    changes.extend(
        _parse_name_status(
            _git("diff", "--name-status", "-z", "-M", cwd=root),
            ChangeSource.WORKTREE,
        )
    )
    changes.extend(_untracked(root))
    return _deduplicate_changes(changes)


def _tracked_gitlinks(root: Path) -> tuple[ChangedPath, ...]:
    output = _git("ls-files", "--stage", "-z", cwd=root)
    gitlinks = []
    for record in output.split(b"\0"):
        metadata, separator, raw_path = record.partition(b"\t")
        if not separator:
            continue
        fields = metadata.split(b" ")
        if len(fields) != 3:
            raise GitStateError("git returned an invalid index entry")
        mode, _, stage = fields
        path = _normalise_path(raw_path)
        if mode != b"160000" or stage != b"0" or _excluded(path):
            continue
        gitlinks.append(
            ChangedPath(
                path=_decode_path(path),
                path_bytes=path,
                kind=ChangeKind.MODIFIED,
                source=ChangeSource.WORKTREE,
            )
        )
    return tuple(gitlinks)


def _unreported_dirty_gitlinks(
    root: Path,
    depth: int = 0,
    ancestors: frozenset[tuple[int, int]] = frozenset(),
) -> tuple[ChangedPath, ...]:
    if depth >= MAX_GITLINK_DEPTH:
        return ()
    identity = _repository_identity(root)
    if identity in ancestors:
        return ()
    nested_ancestors = ancestors | {identity}
    changes = []
    for gitlink in _tracked_gitlinks(root):
        target = root / os.fsdecode(gitlink.path_bytes)
        try:
            metadata = target.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise GitStateError(
                f"cannot inspect gitlink '{gitlink.path}': {error}"
            ) from error
        if not stat.S_ISDIR(metadata.st_mode):
            continue
        is_repository = _git(
            "rev-parse",
            "--is-inside-work-tree",
            cwd=target,
            allow_failure=True,
        )
        if is_repository != b"true\n":
            continue
        if _local_changes(target) or _unreported_dirty_gitlinks(
            target,
            depth + 1,
            nested_ancestors,
        ):
            changes.append(gitlink)
    return tuple(changes)


def _nested_checkout_fingerprint(
    root: Path,
    depth: int,
    ancestors: frozenset[tuple[int, int]],
) -> bytes:
    head = _git("rev-parse", "--verify", "HEAD", cwd=root, allow_failure=True)
    status = _git("status", "--porcelain=v1", "-z", cwd=root)
    identity = _repository_identity(root)
    if identity in ancestors:
        return b"gitlink-cycle\0" + hashlib.sha256(status).digest()
    if depth >= MAX_GITLINK_DEPTH:
        return b"gitlink-depth-limit\0" + head + hashlib.sha256(status).digest()
    digest = hashlib.sha256()
    digest.update(b"nested-git-state-v1\0")
    digest.update(head)
    digest.update(b"\0")
    nested_ancestors = ancestors | {identity}
    changes = list(_local_changes(root))
    changes.extend(_unreported_dirty_gitlinks(root, depth, ancestors))
    for change in _deduplicate_changes(changes):
        _write_change_fingerprint(digest, root, change, depth + 1, nested_ancestors)
    return digest.digest()


def _gitlink_content(
    target: Path,
    depth: int,
    ancestors: frozenset[tuple[int, int]],
) -> bytes:
    head = _git("rev-parse", "--verify", "HEAD", cwd=target, allow_failure=True)
    if not head:
        raise GitStateError(f"gitlink '{target}' has no checked out commit")
    return (
        b"gitlink\0"
        + _without_output_terminator(head)
        + b"\0"
        + _nested_checkout_fingerprint(target, depth, ancestors)
    )


def _worktree_content(
    root: Path,
    change: ChangedPath,
    depth: int,
    ancestors: frozenset[tuple[int, int]],
) -> bytes | None:
    target = root / os.fsdecode(change.path_bytes)
    try:
        metadata = target.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise GitStateError(
            f"cannot inspect working path '{change.path}': {error}"
        ) from error
    if stat.S_ISLNK(metadata.st_mode):
        try:
            return b"symlink\0" + os.fsencode(os.readlink(target))
        except OSError as error:
            raise GitStateError(
                f"cannot read symlink '{change.path}': {error}"
            ) from error
    if stat.S_ISREG(metadata.st_mode):
        try:
            mode = stat.S_IFMT(metadata.st_mode) | (metadata.st_mode & 0o111)
            return b"file\0" + f"{mode:o}".encode("ascii") + b"\0" + target.read_bytes()
        except OSError as error:
            raise GitStateError(
                f"cannot read working path '{change.path}': {error}"
            ) from error
    if stat.S_ISDIR(metadata.st_mode):
        entries = _index_entries(root, change.path_bytes)
        if any(mode == b"160000" for mode, _, _ in entries):
            return _gitlink_content(target, depth, ancestors)
        return b"directory\0"
    return b"special\0" + str(metadata.st_mode).encode("ascii")


def _content(
    root: Path,
    change: ChangedPath,
    depth: int = 0,
    ancestors: frozenset[tuple[int, int]] = frozenset(),
) -> bytes | None:
    if change.kind == ChangeKind.UNMERGED:
        return _conflict_content(root, change.path_bytes)
    if change.kind == ChangeKind.DELETED:
        return None
    if change.source in (ChangeSource.WORKTREE, ChangeSource.UNTRACKED):
        return _worktree_content(root, change, depth, ancestors)
    if change.source == ChangeSource.INDEX:
        modes = sorted(
            mode + b":" + stage
            for mode, _, stage in _index_entries(root, change.path_bytes)
        )
        if not modes:
            raise GitStateError("git returned no index entry for a tracked path")
        revision = ":./"
        marker = b"index\0" + b"\0".join(modes) + b"\0"
    else:
        revision = "HEAD:./"
        marker = b"committed\0" + _head_mode(root, change.path_bytes) + b"\0"
    return marker + _git(
        "show",
        f"{revision}{_decode_path(change.path_bytes)}",
        cwd=root,
    )


def _fingerprint(
    root: Path, head: str | None, base: GitBase, changes: tuple[ChangedPath, ...]
) -> str:
    digest = hashlib.sha256()
    digest.update(b"agent-git-state-v1\0")
    for value in (head, base.resolved, base.merge_base):
        digest.update((value or "").encode("ascii"))
        digest.update(b"\0")
    for change in changes:
        _write_change_fingerprint(digest, root, change, 0, frozenset())
    return digest.hexdigest()


def _counts(changes: tuple[ChangedPath, ...]) -> ChangeCounts:
    return ChangeCounts(
        index=sum(change.source == ChangeSource.INDEX for change in changes),
        worktree=sum(change.source == ChangeSource.WORKTREE for change in changes),
        untracked=sum(change.source == ChangeSource.UNTRACKED for change in changes),
        committed=sum(change.source == ChangeSource.COMMITTED for change in changes),
    )


def collect_git_state(cwd: Path | None = None, base: str | None = None) -> GitState:
    root_output = _git("rev-parse", "--show-toplevel", cwd=cwd)
    root = Path(os.fsdecode(_without_output_terminator(root_output))).resolve()
    head_output = _git("rev-parse", "--verify", "HEAD", cwd=root, allow_failure=True)
    head = (
        _without_output_terminator(head_output).decode("ascii") if head_output else None
    )
    branch_output = _git(
        "symbolic-ref", "--quiet", "--short", "HEAD", cwd=root, allow_failure=True
    )
    branch = (
        os.fsdecode(_without_output_terminator(branch_output))
        if branch_output
        else None
    )
    git_base = _base_state(root, head, base)

    changes = list(_local_changes(root))
    changes.extend(_unreported_dirty_gitlinks(root))
    if git_base.available and git_base.merge_base:
        changes.extend(
            _parse_name_status(
                _git(
                    "diff",
                    "--name-status",
                    "-z",
                    "-M",
                    git_base.merge_base,
                    "HEAD",
                    cwd=root,
                ),
                ChangeSource.COMMITTED,
            )
        )

    ordered = _deduplicate_changes(changes)
    counts = _counts(ordered)
    return GitState(
        root=root,
        branch=branch,
        detached=branch is None,
        head=head,
        base=git_base,
        counts=counts,
        changes=ordered,
        dirty=counts.local_total > 0,
        fingerprint=_fingerprint(root, head, git_base, ordered),
    )
