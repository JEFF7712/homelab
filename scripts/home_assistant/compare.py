from __future__ import annotations

from datetime import UTC, datetime

from .canonical import canonical_hash
from .models import ComparisonItem, DiffReport, DiffStatus, ResourceDocument


def hash_or_none(doc: ResourceDocument | None) -> str | None:
    if doc is None or doc.desired is None:
        return None
    return canonical_hash(doc.desired)


def compare_three_way(
    instance: str,
    baseline: dict[str, ResourceDocument],
    git: dict[str, ResourceDocument],
    live: dict[str, ResourceDocument],
    errors: dict[str, str] | None = None,
) -> DiffReport:
    """Perform a deterministic three-way comparison between Baseline (B), Git (G), and Live (L)."""
    errors = errors or {}
    errored_kinds: dict[str, str] = {}
    for err_k, err_msg in errors.items():
        if "/" in err_k:
            ek, ekey = err_k.split("/", 1)
            if ekey in ("all", "error"):
                errored_kinds[ek] = err_msg

    all_keys = (
        set(baseline.keys()) | set(git.keys()) | set(live.keys()) | set(errors.keys())
    )
    items: list[ComparisonItem] = []

    for rk_str in sorted(all_keys):
        if "/" not in rk_str:
            kind, key = "unknown", rk_str
        else:
            kind, key = rk_str.split("/", 1)

        b_doc = baseline.get(rk_str)
        g_doc = git.get(rk_str)
        l_doc = live.get(rk_str)

        if rk_str in errors:
            # If this is an aggregate error like 'kind/all' and there are concrete resources of this kind,
            # we will mark those resources UNKNOWN below. Only include aggregate if no resources exist.
            has_concrete = any(
                k.startswith(f"{kind}/") and k != rk_str
                for k in (set(baseline.keys()) | set(git.keys()) | set(live.keys()))
            )
            if not has_concrete or key not in ("all", "error"):
                items.append(
                    ComparisonItem(
                        kind=kind,
                        key=key,
                        status=DiffStatus.UNKNOWN,
                        details=f"Live capture error: {errors[rk_str]}",
                        baseline=b_doc.desired if b_doc else None,
                        git=g_doc.desired if g_doc else None,
                        live=None,
                    )
                )
            continue

        if kind in errored_kinds and l_doc is None:
            # Kind-wide collection failed: NEVER classify missing live state as deleted/absent!
            items.append(
                ComparisonItem(
                    kind=kind,
                    key=key,
                    status=DiffStatus.UNKNOWN,
                    details=f"Live capture error for {kind}: {errored_kinds[kind]}",
                    baseline=b_doc.desired if b_doc else None,
                    git=g_doc.desired if g_doc else None,
                    live=None,
                )
            )
            continue

        b_hash = hash_or_none(b_doc)
        g_hash = hash_or_none(g_doc)
        l_hash = hash_or_none(l_doc)

        b_present = b_hash is not None
        g_present = g_hash is not None
        l_present = l_hash is not None

        status: DiffStatus
        details = ""

        if b_present and g_present and l_present:
            if b_hash == g_hash == l_hash:
                status = DiffStatus.CLEAN
                details = "In sync across baseline, Git, and live"
            elif g_hash == b_hash and l_hash != b_hash:
                status = DiffStatus.EXPERIMENT
                details = "Live state changed in UI; available for local Git adoption"
            elif l_hash == b_hash and g_hash != b_hash:
                status = DiffStatus.GIT_CHANGE
                details = "Git desired state changed; eligible for planned deployment"
            elif g_hash == l_hash and g_hash != b_hash:
                status = DiffStatus.CONVERGED
                details = "Git and live state independently converged; ready to advance baseline"
            else:
                status = DiffStatus.CONFLICT
                details = (
                    "Conflict: Git and live configuration both modified independently"
                )

        elif not b_present:
            # Baseline does not have this resource
            if g_present and not l_present:
                status = DiffStatus.GIT_CHANGE
                details = "New resource defined in Git; eligible for planned deployment"
            elif not g_present and l_present:
                status = DiffStatus.EXPERIMENT
                details = "New resource created in UI; available for local Git adoption"
            elif g_present and l_present:
                if g_hash == l_hash:
                    status = DiffStatus.CONVERGED
                    details = (
                        "Resource present in Git and live with matching definition"
                    )
                else:
                    status = DiffStatus.CONFLICT
                    details = "Conflict: Resource exists in both Git and live without baseline and differs"
            else:
                status = DiffStatus.CLEAN

        elif b_present and not g_present and l_present:
            # Resource deleted in Git
            if l_hash == b_hash:
                status = DiffStatus.GIT_CHANGE
                details = (
                    "Resource removed from Git; eligible for planned deletion in live"
                )
            else:
                status = DiffStatus.CONFLICT
                details = "Conflict: Removed from Git but modified in live UI"

        elif b_present and g_present and not l_present:
            # Resource deleted in live UI
            if g_hash == b_hash:
                status = DiffStatus.EXPERIMENT
                details = (
                    "Resource removed in live UI; available for local Git deletion"
                )
            else:
                status = DiffStatus.CONFLICT
                details = "Conflict: Removed in live UI but modified in Git"

        else:
            # b_present and not g_present and not l_present
            status = DiffStatus.CLEAN
            details = "Resource removed from both Git and live"

        items.append(
            ComparisonItem(
                kind=kind,
                key=key,
                status=status,
                baseline_hash=b_hash,
                git_hash=g_hash,
                live_hash=l_hash,
                details=details,
                baseline=b_doc.desired if b_doc else None,
                git=g_doc.desired if g_doc else None,
                live=l_doc.desired if l_doc else None,
            )
        )

    summary: dict[str, int] = {}
    for it in items:
        s_val = it.status.value
        summary[s_val] = summary.get(s_val, 0) + 1

    now_iso = datetime.now(UTC).isoformat()
    return DiffReport(
        instance=instance, timestamp=now_iso, summary=summary, items=items
    )
