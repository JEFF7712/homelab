from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .adapters import ADAPTERS, get_adapter, try_get_adapter
from .adapters.core import render_config_configmap, sync_core_to_gitops
from .canonical import (
    canonical_hash,
    canonical_json,
    compute_baseline_hash,
    detect_secrets,
    sanitize_error,
    to_json_compatible,
)
from .client import (
    HomeAssistantClient,
    MockHomeAssistantClient,
)
from .compare import compare_three_way
from .models import (
    ActionType,
    ApplyPlan,
    BaselineRecord,
    DiffStatus,
    ExitCode,
    InventoryReport,
    PlanAction,
    ResourceDocument,
    SurfaceInventory,
)
from .planner import LockError, Planner, StalePlanError
from .source import (
    adopt_resources,
    get_source_path,
    load_source_tree,
    remove_resource,
)


def find_repo_root() -> Path:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return Path(res.stdout.strip())
    except Exception:
        pass
    return Path.cwd()


def get_git_revision(repo_root: Path) -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return "unknown"


def resolve_token(
    token_arg: str | None, token_file_arg: str | None, repo_root: Path
) -> str:
    if token_arg:
        return token_arg
    if token_file_arg and os.path.isfile(token_file_arg):
        return Path(token_file_arg).read_text(encoding="utf-8").strip()
    env_token = os.environ.get("HASS_TOKEN")
    if env_token:
        return env_token
    env_file = os.environ.get("HASS_TOKEN_FILE")
    if env_file and os.path.isfile(env_file):
        return Path(env_file).read_text(encoding="utf-8").strip()

    # Look for local token under .agent-state/home-assistant/token
    local_state_token = repo_root / ".agent-state" / "home-assistant" / "token"
    if local_state_token.is_file():
        return local_state_token.read_text(encoding="utf-8").strip()

    return ""


def get_client(args: argparse.Namespace, repo_root: Path) -> HomeAssistantClient:
    if getattr(args, "mock", False):
        return MockHomeAssistantClient()
    token = resolve_token(
        getattr(args, "token", None), getattr(args, "token_file", None), repo_root
    )
    url = getattr(args, "url", None) or os.environ.get(
        "HASS_URL", "http://10.0.40.13:8123"
    )
    return HomeAssistantClient(base_url=url, token=token)


def output_result(
    data: dict[str, Any], json_output: bool, human_formatter: Any = None
) -> None:
    if json_output:
        print(json.dumps(data, indent=2))
    elif human_formatter:
        human_formatter(data)
    else:
        print(json.dumps(data, indent=2))


# ----------------------------------------------------------------------
# Command: inventory
# ----------------------------------------------------------------------
def cmd_inventory(args: argparse.Namespace, repo_root: Path) -> int:
    client = get_client(args, repo_root)
    try:
        health = client.check_health()
    except Exception as exc:
        err_res = {
            "status": "unavailable",
            "error": str(exc),
            "schema_version": "1.0",
        }
        output_result(err_res, args.json)
        return ExitCode.UNAVAILABLE.value

    surfaces: list[SurfaceInventory] = []
    # Probe each surface safely
    try:
        autos = client.list_entities()
        auto_count = sum(
            1 for e in autos if e.get("entity_id", "").startswith("automation.")
        )
        surfaces.append(
            SurfaceInventory(
                "automations",
                auto_count,
                "ui-editable",
                "Home Assistant UI automations",
            )
        )
        script_count = sum(
            1 for e in autos if e.get("entity_id", "").startswith("script.")
        )
        surfaces.append(
            SurfaceInventory(
                "scripts", script_count, "ui-editable", "Home Assistant UI scripts"
            )
        )
        scene_count = sum(
            1 for e in autos if e.get("entity_id", "").startswith("scene.")
        )
        surfaces.append(
            SurfaceInventory(
                "scenes", scene_count, "ui-editable", "Home Assistant UI scenes"
            )
        )
    except Exception:
        pass

    try:
        dashboards = client.list_dashboards()
        surfaces.append(
            SurfaceInventory(
                "dashboards",
                len(dashboards),
                "ui-editable",
                "Lovelace storage dashboards",
            )
        )
    except Exception:
        pass

    try:
        areas = client.list_areas()
        surfaces.append(
            SurfaceInventory(
                "areas", len(areas), "ui-editable", "Area registry entries"
            )
        )
    except Exception:
        pass

    try:
        devices = client.list_devices()
        surfaces.append(
            SurfaceInventory(
                "devices", len(devices), "ui-editable", "Device registry entries"
            )
        )
    except Exception:
        pass

    try:
        entities = client.list_entities()
        surfaces.append(
            SurfaceInventory(
                "entities", len(entities), "ui-editable", "Entity registry entries"
            )
        )
    except Exception:
        pass

    try:
        entries = client.list_config_entries()
        surfaces.append(
            SurfaceInventory(
                "integrations",
                len(entries),
                "observe-only",
                "Config entry integrations",
            )
        )
    except Exception:
        pass

    summary = {s.surface: s.count for s in surfaces}
    report = InventoryReport(
        instance=args.instance,
        ha_version=health.get("version", "unknown"),
        timestamp=datetime.now(UTC).isoformat(),
        capabilities={
            "status": "online",
            "writable_apis": [
                "automations",
                "scripts",
                "scenes",
                "dashboards",
                "areas",
                "devices",
                "entities",
            ],
        },
        surfaces=surfaces,
        unsupported=["oauth_credentials", "zha_pairing", "raw_storage"],
        summary=summary,
    )

    # Optionally write to home-assistant/inventory.yaml if directory exists
    ha_dir = repo_root / "home-assistant"
    if ha_dir.is_dir():
        from .canonical import dump_yaml

        inv_file = ha_dir / "inventory.yaml"
        inv_file.write_text(dump_yaml(report.to_dict()), encoding="utf-8")

    def _fmt(d: dict[str, Any]) -> None:
        print(f"Home Assistant Inventory ({d['instance']} - v{d['ha_version']})")
        print("-" * 50)
        for s in d.get("surfaces", []):
            print(
                f"  {s['surface']:<15}: {s['count']:>3} items ({s['category']}) - {s['description']}"
            )
        print("\nUnsupported / Runtime-only surfaces:")
        for u in d.get("unsupported", []):
            print(f"  - {u}")

    output_result(report.to_dict(), args.json, _fmt)
    return ExitCode.CLEAN.value


# ----------------------------------------------------------------------
# Command: capture
# ----------------------------------------------------------------------
def cmd_capture(args: argparse.Namespace, repo_root: Path) -> int:
    client = get_client(args, repo_root)
    try:
        client.check_health()
    except Exception as exc:
        output_result(
            {"status": "unavailable", "error": sanitize_error(exc)}, args.json
        )
        return ExitCode.UNAVAILABLE.value

    capture_id = f"cap-{int(datetime.now(UTC).timestamp())}"
    capture_dir = (
        repo_root
        / ".agent-state"
        / "home-assistant"
        / args.instance
        / "captures"
        / capture_id
    )
    capture_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(capture_dir, 0o700)
    except Exception:
        pass

    captured_docs: dict[str, Any] = {}
    secret_findings: list[str] = []
    adapter_errors: dict[str, str] = {}

    for kind, adapter in ADAPTERS.items():
        try:
            docs = adapter.export_from_live(client)
            for d in docs:
                rk_str = str(d.resource_key)
                findings = detect_secrets(d.desired, path=rk_str)
                if findings:
                    secret_findings.extend(findings)
                captured_docs[rk_str] = d.to_dict()
        except Exception as exc:
            err_msg = sanitize_error(exc)
            adapter_errors[kind] = err_msg
            captured_docs[f"{kind}/error"] = {"error": err_msg}

    if secret_findings:
        output_result(
            {
                "status": "error",
                "message": f"Capture rejected: sensitive credentials detected in live state: {'; '.join(secret_findings)}",
                "secret_findings": secret_findings,
            },
            args.json,
        )
        return ExitCode.CONFLICT_OR_INVALID.value

    out_file = capture_dir / "capture.json"
    meta = {
        "capture_id": capture_id,
        "instance": args.instance,
        "timestamp": datetime.now(UTC).isoformat(),
        "resources": to_json_compatible(captured_docs),
    }
    out_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    try:
        os.chmod(out_file, 0o600)
    except Exception:
        pass

    if adapter_errors:
        output_result(
            {
                "status": "incomplete",
                "capture_id": capture_id,
                "path": str(out_file.relative_to(repo_root)),
                "count": len(captured_docs),
                "errors": adapter_errors,
            },
            args.json,
            lambda d: print(
                f"Capture incomplete: {len(d['errors'])} adapter(s) failed ({', '.join(d['errors'].keys())}). "
                f"Saved partial capture to {d['path']}"
            ),
        )
        return ExitCode.UNAVAILABLE.value

    output_result(
        {
            "status": "captured",
            "capture_id": capture_id,
            "path": str(out_file.relative_to(repo_root)),
            "count": len(captured_docs),
        },
        args.json,
        lambda d: print(f"Captured {d['count']} resources to {d['path']}"),
    )
    return ExitCode.CLEAN.value


def canonicalize_docs(
    docs: dict[str, ResourceDocument],
) -> dict[str, ResourceDocument]:
    result: dict[str, ResourceDocument] = {}
    for rk_str, doc in docs.items():
        try:
            adapter = get_adapter(doc.kind)
            result[rk_str] = adapter.canonicalize(doc)
        except Exception:
            result[rk_str] = doc
    return result


def export_live_docs(
    client: HomeAssistantClient,
) -> tuple[dict[str, ResourceDocument], dict[str, str]]:
    live_docs: dict[str, ResourceDocument] = {}
    errors: dict[str, str] = {}
    for kind, adapter in ADAPTERS.items():
        try:
            docs = adapter.export_from_live(client)
            for d in docs:
                live_docs[str(d.resource_key)] = adapter.canonicalize(d)
        except Exception as exc:
            errors[f"{kind}/all"] = str(exc)
    return live_docs, errors


# ----------------------------------------------------------------------
# Command: diff
# ----------------------------------------------------------------------
def cmd_diff(args: argparse.Namespace, repo_root: Path) -> int:
    client = get_client(args, repo_root)
    planner = Planner(repo_root, args.instance, client=client)
    try:
        baseline_rec = planner.load_baseline()
    except Exception as exc:
        output_result(
            {"status": "unavailable", "error": sanitize_error(exc)}, args.json
        )
        return ExitCode.UNAVAILABLE.value

    raw_baseline: dict[str, ResourceDocument] = {}
    if baseline_rec:
        for rk, rdata in baseline_rec.resources.items():
            kind, key = rk.split("/", 1)
            raw_baseline[rk] = ResourceDocument(
                kind=kind, key=key, desired=rdata.get("desired")
            )
    baseline_docs = canonicalize_docs(raw_baseline)
    git_docs = canonicalize_docs(load_source_tree(repo_root))

    live_docs, errors = export_live_docs(client)

    diff_report = compare_three_way(
        instance=args.instance,
        baseline=baseline_docs,
        git=git_docs,
        live=live_docs,
        errors=errors,
    )

    if args.select:
        selected_set = set(args.select)
        diff_report.items = [
            item
            for item in diff_report.items
            if str(item.resource_key) in selected_set or item.key in selected_set
        ]
        counts: dict[str, int] = {}
        for item in diff_report.items:
            counts[item.status.value] = counts.get(item.status.value, 0) + 1
        diff_report.summary = counts

    def _fmt(d: dict[str, Any]) -> None:
        print(f"Home Assistant Three-Way Diff ({d['instance']})")
        print("=" * 60)
        summary = d.get("summary", {})
        print(f"Summary: {summary}\n")
        for item in d.get("items", []):
            st = item["status"]
            if st != DiffStatus.CLEAN.value:
                print(
                    f"[{st.upper():<10}] {item['kind']}/{item['key']}: {item['details']}"
                )

    output_result(diff_report.to_dict(), args.json, _fmt)

    if diff_report.has_conflicts:
        return ExitCode.CONFLICT_OR_INVALID.value
    if diff_report.has_drift:
        return ExitCode.DRIFT_OR_PENDING.value
    return ExitCode.CLEAN.value


# ----------------------------------------------------------------------
# Command: adopt
# ----------------------------------------------------------------------
def cmd_adopt(args: argparse.Namespace, repo_root: Path) -> int:
    client = get_client(args, repo_root)
    try:
        client.check_health()
    except Exception as exc:
        output_result(
            {"status": "unavailable", "error": sanitize_error(exc)}, args.json
        )
        return ExitCode.UNAVAILABLE.value

    selected = set(args.select) if args.select else None
    if not selected and not getattr(args, "all", False):
        err_res = {
            "status": "error",
            "message": "Specify --select <kind/key> or --all to adopt live resources",
        }
        output_result(err_res, args.json, lambda d: print(f"Error: {d['message']}"))
        return ExitCode.CONFLICT_OR_INVALID.value

    # Load baseline, git source, and live state
    planner = Planner(repo_root, args.instance, client=client)
    try:
        baseline_rec = planner.load_baseline()
    except Exception as exc:
        output_result(
            {"status": "unavailable", "error": sanitize_error(exc)}, args.json
        )
        return ExitCode.UNAVAILABLE.value

    raw_baseline: dict[str, ResourceDocument] = {}
    if baseline_rec:
        for rk, rdata in baseline_rec.resources.items():
            kind, key = rk.split("/", 1)
            raw_baseline[rk] = ResourceDocument(
                kind=kind, key=key, desired=rdata.get("desired")
            )
    baseline_docs = canonicalize_docs(raw_baseline)
    git_docs = canonicalize_docs(load_source_tree(repo_root))
    live_docs, errors = export_live_docs(client)

    diff_report = compare_three_way(
        instance=args.instance,
        baseline=baseline_docs,
        git=git_docs,
        live=live_docs,
        errors=errors,
    )

    # Validate selected keys
    if selected:
        all_keys = {str(item.resource_key) for item in diff_report.items} | {
            item.key for item in diff_report.items
        }
        unknown_keys = [k for k in selected if k not in all_keys]
        if unknown_keys:
            err_res = {
                "status": "error",
                "message": f"Selected resource(s) not found: {', '.join(sorted(unknown_keys))}",
            }
            output_result(err_res, args.json, lambda d: print(f"Error: {d['message']}"))
            return ExitCode.CONFLICT_OR_INVALID.value
        candidate_items = [
            item
            for item in diff_report.items
            if str(item.resource_key) in selected or item.key in selected
        ]

        # Explicitly reject git_change when explicitly selected
        git_changes = [
            item for item in candidate_items if item.status == DiffStatus.GIT_CHANGE
        ]
        if git_changes:
            gc_keys = [str(item.resource_key) for item in git_changes]
            err_res = {
                "status": "error",
                "message": f"Adoption blocked: {', '.join(gc_keys)} has status 'git_change' "
                "(unapplied committed Git change, not a UI experiment). "
                "Adoption only imports live UI experiments into Git. "
                "To deploy Git changes, run plan and apply. To discard Git changes, use git checkout or git revert.",
                "git_changes": gc_keys,
            }
            output_result(err_res, args.json, lambda d: print(f"Error: {d['message']}"))
            return ExitCode.CONFLICT_OR_INVALID.value

        items_to_adopt = [
            item for item in candidate_items if item.status == DiffStatus.EXPERIMENT
        ]
    else:
        candidate_items = diff_report.items
        items_to_adopt = [
            item for item in diff_report.items if item.status == DiffStatus.EXPERIMENT
        ]

    # Reject unresolved conflicts
    conflicts = [item for item in candidate_items if item.status == DiffStatus.CONFLICT]
    if conflicts:
        conflict_keys = [str(item.resource_key) for item in conflicts]
        err_res = {
            "status": "error",
            "message": f"Adoption blocked: unresolved conflicts on {', '.join(conflict_keys)}. "
            "Git desired state and live state diverged independently. "
            "Resolve conflicts manually or revert live state before adopting.",
            "conflicts": conflict_keys,
        }
        output_result(err_res, args.json, lambda d: print(f"Error: {d['message']}"))
        return ExitCode.CONFLICT_OR_INVALID.value

    # Reject unknown live states caused by errors
    unknowns = [item for item in candidate_items if item.status == DiffStatus.UNKNOWN]
    if unknowns:
        unknown_keys = [str(item.resource_key) for item in unknowns]
        err_res = {
            "status": "error",
            "message": f"Adoption blocked: live state unknown or export failed for {', '.join(unknown_keys)}.",
            "unknown": unknown_keys,
        }
        output_result(err_res, args.json, lambda d: print(f"Error: {d['message']}"))
        return ExitCode.CONFLICT_OR_INVALID.value

    if not items_to_adopt:
        res = {
            "status": "adopted",
            "instance": args.instance,
            "adopted_count": 0,
            "files": [],
            "deleted": [],
        }
        output_result(res, args.json, lambda d: print("No changes detected to adopt."))
        return ExitCode.CLEAN.value

    # Check for uncommitted Git changes on target files
    dirty_files: list[str] = []
    for item in items_to_adopt:
        target_path = get_source_path(repo_root, item.kind, item.key)
        try:
            res = subprocess.run(
                ["git", "status", "--porcelain", "--", str(target_path)],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                dirty_files.append(str(target_path.relative_to(repo_root)))
        except Exception:
            pass

    if dirty_files:
        err_res = {
            "status": "error",
            "message": f"Adoption blocked: target destination file(s) have uncommitted Git changes: {', '.join(dirty_files)}. Commit or stash them before adopting.",
            "dirty_files": dirty_files,
        }
        output_result(err_res, args.json, lambda d: print(f"Error: {d['message']}"))
        return ExitCode.CONFLICT_OR_INVALID.value

    # Preflight secret check: reject plaintext credentials before any file write
    secret_findings: list[str] = []
    for item in items_to_adopt:
        if item.live is not None:
            findings = detect_secrets(item.live, path=str(item.resource_key))
            secret_findings.extend(findings)

    if secret_findings:
        err_res = {
            "status": "error",
            "message": f"Adoption blocked: sensitive credentials detected in live resources: {'; '.join(secret_findings)}. Replace with !secret references before adopting.",
            "secret_findings": secret_findings,
        }
        output_result(err_res, args.json, lambda d: print(f"Error: {d['message']}"))
        return ExitCode.CONFLICT_OR_INVALID.value

    # Separate writes from deletions
    deletions = [item for item in items_to_adopt if item.live is None]
    writes = [item for item in items_to_adopt if item.live is not None]

    if deletions:
        if not getattr(args, "allow_delete", False):
            del_keys = [str(item.resource_key) for item in deletions]
            err_res = {
                "status": "error",
                "message": f"Adoption includes UI deletion of {', '.join(del_keys)}. Specify --allow-delete to confirm removing source files.",
                "deletions": del_keys,
            }
            output_result(err_res, args.json, lambda d: print(f"Error: {d['message']}"))
            return ExitCode.CONFLICT_OR_INVALID.value

        # Dependency check: check if any remaining Git resources depend on the deleted resource
        del_refs = {f"{item.kind}.{item.key}" for item in deletions} | {
            f"{item.kind}/{item.key}" for item in deletions
        }
        remaining_git = {
            k: v
            for k, v in git_docs.items()
            if k not in {str(d.resource_key) for d in deletions}
        }
        dependency_conflicts: list[str] = []
        for rem_rk, rem_doc in remaining_git.items():
            rem_str = json.dumps(to_json_compatible(rem_doc.desired))
            for ref in del_refs:
                if ref in rem_str:
                    dependency_conflicts.append(f"{ref} is referenced by {rem_rk}")

        if dependency_conflicts and not getattr(args, "force", False):
            err_res = {
                "status": "error",
                "message": f"Adoption blocked: deleted resource(s) still referenced in Git source: {'; '.join(dependency_conflicts)}. Use --force to override.",
                "dependencies": dependency_conflicts,
            }
            output_result(err_res, args.json, lambda d: print(f"Error: {d['message']}"))
            return ExitCode.CONFLICT_OR_INVALID.value

    written_paths: list[Path] = []
    if writes:
        docs_to_write = [
            ResourceDocument(kind=item.kind, key=item.key, desired=item.live)
            for item in writes
            if item.live is not None
        ]
        written_paths = adopt_resources(repo_root, docs_to_write, selected_keys=None)

    deleted_paths: list[Path] = []
    for item in deletions:
        dest = get_source_path(repo_root, item.kind, item.key)
        if remove_resource(repo_root, item.kind, item.key):
            deleted_paths.append(dest)

    res = {
        "status": "adopted",
        "instance": args.instance,
        "adopted_count": len(written_paths) + len(deleted_paths),
        "files": [str(p.relative_to(repo_root)) for p in written_paths],
        "deleted": [str(p.relative_to(repo_root)) for p in deleted_paths],
    }

    def _fmt(d: dict[str, Any]) -> None:
        print(f"Adopted {d['adopted_count']} resources into Git source:")
        for f in d.get("files", []):
            print(f"  [WRITTEN] {f}")
        for f in d.get("deleted", []):
            print(f"  [DELETED] {f}")
        print(
            "\nNote: Adoption updates local source files. It does not advance the deployment baseline."
        )

    output_result(res, args.json, _fmt)
    return ExitCode.CLEAN.value


# ----------------------------------------------------------------------
# Command: validate
# ----------------------------------------------------------------------
def cmd_validate(args: argparse.Namespace, repo_root: Path) -> int:
    if getattr(args, "sync_gitops", False):
        sync_core_to_gitops(repo_root)

    git_docs = load_source_tree(repo_root)
    errors: list[str] = []
    secret_findings: list[str] = []

    # Check GitOps ConfigMap sync for core configuration
    core_file = repo_root / "home-assistant" / "core" / "configuration.yaml"
    cm_file = repo_root / "gitops" / "home-assistant" / "config.yaml"
    if core_file.is_file() and cm_file.is_file():
        cm_raw = cm_file.read_text(encoding="utf-8")
        expected_cm, _ = render_config_configmap(repo_root)
        if cm_raw.strip() != expected_cm.strip():
            errors.append(
                "Core configuration in gitops/home-assistant/config.yaml is out of sync with home-assistant/core/configuration.yaml. "
                "Run 'python -m scripts.home_assistant validate --sync-gitops' to synchronize."
            )

    skipped_unknown_kind: list[str] = []
    for rk_str, doc in git_docs.items():
        # Schema validation. `try_get_adapter` makes this pass robust to a
        # single bad resource (e.g. a YAML that parses with the wrong kind):
        # we surface the unknown kind as a warning and continue, instead of
        # failing the whole pipeline. The strict `get_adapter` is reserved
        # for plan/apply so a bad kind still aborts there.
        adapter = try_get_adapter(doc.kind)
        if adapter is None:
            skipped_unknown_kind.append(f"{rk_str}: kind '{doc.kind}' has no adapter")
        else:
            try:
                errs = adapter.validate(doc)
            except Exception as e:
                errs = [f"adapter raised during validation: {e}"]
            for e in errs:
                errors.append(f"{rk_str}: {e}")

        # Secret detection
        findings = detect_secrets(doc.desired, path=rk_str)
        secret_findings.extend(findings)

    is_valid = (
        len(errors) == 0
        and len(secret_findings) == 0
        and len(skipped_unknown_kind) == 0
    )
    res = {
        "status": "valid" if is_valid else "invalid",
        "resources_checked": len(git_docs),
        "errors": errors,
        "secret_findings": secret_findings,
        "skipped_unknown_kind": skipped_unknown_kind,
    }

    def _fmt(d: dict[str, Any]) -> None:
        print(f"Validation checked {d['resources_checked']} resources:")
        if d["status"] == "valid":
            print("  All resources passed offline validation cleanly.")
        else:
            if d.get("errors"):
                print("\nSchema / sync errors:")
                for e in d["errors"]:
                    print(f"  - {e}")
            if d.get("skipped_unknown_kind"):
                print("\nResources with unknown kind (skipped, do not block):")
                for s in d["skipped_unknown_kind"]:
                    print(f"  - {s}")
            if d.get("secret_findings"):
                print("\nSecret findings (must be replaced with !secret references):")
                for s in d["secret_findings"]:
                    print(f"  - {s}")

    output_result(res, args.json, _fmt)
    return ExitCode.CLEAN.value if is_valid else ExitCode.CONFLICT_OR_INVALID.value


# ----------------------------------------------------------------------
# Command: plan
# ----------------------------------------------------------------------
def cmd_plan(args: argparse.Namespace, repo_root: Path) -> int:
    client = get_client(args, repo_root)
    planner = Planner(repo_root, args.instance, client=client)
    try:
        baseline_rec = planner.load_baseline()
    except Exception as exc:
        output_result(
            {"status": "unavailable", "error": sanitize_error(exc)}, args.json
        )
        return ExitCode.UNAVAILABLE.value

    raw_baseline: dict[str, ResourceDocument] = {}
    baseline_hash = "uninitialized"
    if baseline_rec:
        baseline_hash = baseline_rec.content_hash
        for rk, rdata in baseline_rec.resources.items():
            kind, key = rk.split("/", 1)
            raw_baseline[rk] = ResourceDocument(
                kind=kind, key=key, desired=rdata.get("desired")
            )

    baseline_docs = canonicalize_docs(raw_baseline)
    git_docs = canonicalize_docs(load_source_tree(repo_root))
    try:
        health = client.check_health()
        ha_version = health.get("version", "unknown")
    except Exception as exc:
        output_result(
            {"status": "unavailable", "error": sanitize_error(exc)}, args.json
        )
        return ExitCode.UNAVAILABLE.value

    live_docs, errors = export_live_docs(client)

    diff_report = compare_three_way(
        instance=args.instance,
        baseline=baseline_docs,
        git=git_docs,
        live=live_docs,
        errors=errors,
    )

    selected = set(args.select) if args.select else None

    # Secret check on planned Git source
    secret_findings: list[str] = []
    for rk_str, doc in git_docs.items():
        if selected is None or rk_str in selected or doc.key in selected:
            findings = detect_secrets(doc.desired, path=rk_str)
            if findings:
                secret_findings.extend(findings)

    if secret_findings:
        output_result(
            {
                "status": "error",
                "message": f"Planning blocked: sensitive credentials detected in Git source: {'; '.join(secret_findings)}",
                "secret_findings": secret_findings,
            },
            args.json,
        )
        return ExitCode.CONFLICT_OR_INVALID.value

    git_rev = get_git_revision(repo_root)

    source_payload = canonical_json(
        {rk: d.desired for rk, d in sorted(git_docs.items())}
    )
    source_hash = hashlib.sha256(source_payload.encode("utf-8")).hexdigest()

    try:
        plan = planner.create_plan(
            diff_report=diff_report,
            git_revision=git_rev,
            baseline_hash=baseline_hash,
            selected_keys=selected,
            ha_version=ha_version,
            source_hash=source_hash,
        )
    except Exception as exc:
        output_result({"status": "error", "message": sanitize_error(exc)}, args.json)
        return ExitCode.CONFLICT_OR_INVALID.value

    # Save plan file
    plans_dir = repo_root / ".agent-state" / "home-assistant" / args.instance / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_file = plans_dir / f"{plan.plan_id}.json"
    plan_file.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")

    res = {
        "status": "planned",
        "plan_id": plan.plan_id,
        "plan_file": str(plan_file.relative_to(repo_root)),
        "actions_count": len(plan.actions),
        "actions": [a.to_dict() for a in plan.actions],
        "skipped": list(plan.skipped),
    }

    def _fmt(d: dict[str, Any]) -> None:
        print(f"Apply Plan generated: {d['plan_id']} ({d['actions_count']} actions)")
        print(f"Plan file: {d['plan_file']}\n")
        for a in d.get("actions", []):
            print(f"  {a['action'].upper():<8} {a['kind']}/{a['key']}")
        for s in d.get("skipped", []):
            print(f"  SKIPPED  {s} (observe-only, cannot mutate)")

    output_result(res, args.json, _fmt)
    return ExitCode.CLEAN.value


# ----------------------------------------------------------------------
# Command: apply
# ----------------------------------------------------------------------
def cmd_apply(args: argparse.Namespace, repo_root: Path) -> int:
    client = get_client(args, repo_root)
    planner = Planner(repo_root, args.instance, client=client)
    git_docs = canonicalize_docs(load_source_tree(repo_root))

    plan_path: Path | None = None
    if getattr(args, "plan_file", None):
        plan_path = Path(args.plan_file)
        if not plan_path.is_file():
            output_result(
                {
                    "status": "error",
                    "message": f"Plan file '{args.plan_file}' not found",
                },
                args.json,
            )
            return ExitCode.CONFLICT_OR_INVALID.value
        plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
        plan = ApplyPlan.from_dict(plan_data)
    else:
        # Generate plan on the fly if authorized
        baseline_rec = planner.load_baseline()
        raw_baseline: dict[str, ResourceDocument] = {}
        baseline_hash = baseline_rec.content_hash if baseline_rec else "uninitialized"
        if baseline_rec:
            for rk, rdata in baseline_rec.resources.items():
                kind, key = rk.split("/", 1)
                raw_baseline[rk] = ResourceDocument(
                    kind=kind, key=key, desired=rdata.get("desired")
                )

        baseline_docs = canonicalize_docs(raw_baseline)
        canon_git_docs = git_docs
        try:
            health = client.check_health()
            ha_version = health.get("version", "unknown")
        except Exception as exc:
            output_result(
                {"status": "unavailable", "error": sanitize_error(exc)}, args.json
            )
            return ExitCode.UNAVAILABLE.value

        live_docs, errors = export_live_docs(client)

        diff_report = compare_three_way(
            instance=args.instance,
            baseline=baseline_docs,
            git=canon_git_docs,
            live=live_docs,
            errors=errors,
        )
        selected = set(args.select) if args.select else None
        source_payload = canonical_json(
            {rk: d.desired for rk, d in sorted(canon_git_docs.items())}
        )
        source_hash = hashlib.sha256(source_payload.encode("utf-8")).hexdigest()

        try:
            plan = planner.create_plan(
                diff_report=diff_report,
                git_revision=get_git_revision(repo_root),
                baseline_hash=baseline_hash,
                selected_keys=selected,
                ha_version=ha_version,
                source_hash=source_hash,
            )
        except Exception as exc:
            output_result(
                {"status": "error", "message": sanitize_error(exc)}, args.json
            )
            return ExitCode.CONFLICT_OR_INVALID.value

    try:
        journal = planner.execute_plan(
            plan=plan,
            client=client,
            git_resources=git_docs,
        )
        res = {
            "status": "applied",
            "plan_id": plan.plan_id,
            "actions_executed": len(plan.actions),
            "journal": [j.to_dict() for j in journal],
        }
        output_result(
            res,
            args.json,
            lambda d: print(
                f"Successfully applied plan {d['plan_id']} ({d['actions_executed']} actions verified)."
            ),
        )
        return ExitCode.CLEAN.value
    except (LockError, StalePlanError, RuntimeError) as exc:
        output_result(
            {
                "status": "failed",
                "plan_id": plan.plan_id,
                "error": sanitize_error(exc),
            },
            args.json,
        )
        return ExitCode.CONFLICT_OR_INVALID.value
    except Exception as exc:
        output_result(
            {
                "status": "failed",
                "plan_id": plan.plan_id,
                "error": sanitize_error(exc),
            },
            args.json,
        )
        return ExitCode.UNAVAILABLE.value


SURFACE_TITLES: dict[str, str] = {
    "automation": "Automations",
    "script": "Scripts",
    "helper": "Helpers",
    "scene": "Scenes",
    "dashboard": "Dashboards",
    "core": "Core",
    "area": "Areas",
    "device": "Devices",
    "entity": "Entities",
    "label": "Labels",
    "floor": "Floors",
    "integration": "Integrations",
}


def build_verify_summary(
    git_docs: dict[str, ResourceDocument],
    mismatches: list[str],
    live_docs: dict[str, ResourceDocument],
) -> dict[str, Any]:
    mismatch_keys = {m.split(" (error:", 1)[0] for m in mismatches}
    unmanaged = sorted(rk for rk in live_docs if rk not in git_docs)
    unmanaged_set = set(unmanaged)
    kinds = sorted(
        {rk.split("/", 1)[0] for rk in git_docs if "/" in rk}
        | {rk.split("/", 1)[0] for rk in live_docs if "/" in rk}
    )
    surfaces = []
    for kind in kinds:
        managed_keys = [rk for rk in git_docs if rk.startswith(f"{kind}/")]
        drifted_keys = sorted(rk for rk in managed_keys if rk in mismatch_keys)
        unmanaged_keys = sorted(
            rk for rk in live_docs if rk.startswith(f"{kind}/") and rk in unmanaged_set
        )
        surfaces.append(
            {
                "surface": SURFACE_TITLES.get(kind, f"{kind}s"),
                "kind": kind,
                "managed": len(managed_keys),
                "converged": len(managed_keys) - len(drifted_keys),
                "drifted": len(drifted_keys),
                "unmanaged": len(unmanaged_keys),
            }
        )
    return {
        "surfaces": surfaces,
        "managed_total": len(git_docs),
        "managed_converged": len(git_docs) - len(mismatch_keys),
        "drifted": len(mismatch_keys),
        "unmanaged_count": len(unmanaged),
        "unmanaged": unmanaged,
    }


# ----------------------------------------------------------------------
# Command: verify
# ----------------------------------------------------------------------
def cmd_verify(args: argparse.Namespace, repo_root: Path) -> int:
    client = get_client(args, repo_root)
    try:
        health = client.check_health()
    except Exception as exc:
        output_result(
            {"status": "unavailable", "error": sanitize_error(exc)}, args.json
        )
        return ExitCode.UNAVAILABLE.value

    git_docs = load_source_tree(repo_root)
    mismatches: list[str] = []

    for rk_str, doc in git_docs.items():
        try:
            adapter = get_adapter(doc.kind)
            verified = adapter.verify(client, doc)
            if not verified:
                mismatches.append(rk_str)
        except Exception as exc:
            mismatches.append(f"{rk_str} (error: {sanitize_error(exc)})")

    is_verified = len(mismatches) == 0

    checkpoint_requested = getattr(args, "checkpoint", False) or getattr(
        args, "bootstrap", False
    )

    if is_verified and checkpoint_requested:
        # Check if local Git working tree has uncommitted modifications
        try:
            res = subprocess.run(
                ["git", "status", "--porcelain", "--", "home-assistant"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                output_result(
                    {
                        "status": "error",
                        "message": "Cannot checkpoint baseline: local Git working tree has uncommitted changes in home-assistant/. Commit changes before checkpointing.",
                    },
                    args.json,
                )
                return ExitCode.CONFLICT_OR_INVALID.value
        except Exception:
            pass

        planner = Planner(repo_root, args.instance, client=client)
        git_rev = get_git_revision(repo_root)
        now_str = datetime.now(UTC).isoformat()
        owner = f"verify-{os.getpid()}"
        try:
            planner.acquire_lock(owner=owner, plan_id="checkpoint")
            try:
                baseline = planner.load_baseline() or BaselineRecord(
                    schema_version="1.0",
                    instance=args.instance,
                    source_commit=git_rev,
                    content_hash="",
                    timestamp=now_str,
                    resources={},
                )
                baseline.source_commit = git_rev
                baseline.timestamp = now_str
                for rk_str, doc in git_docs.items():
                    baseline.resources[rk_str] = {
                        "canonical_hash": canonical_hash(doc.desired),
                        "desired": doc.desired,
                        "verified_at": now_str,
                    }
                baseline.content_hash = compute_baseline_hash(baseline.resources)
                planner.save_baseline(baseline)
            finally:
                planner.release_lock(plan_id="checkpoint", owner=owner)
        except Exception as exc:
            output_result(
                {
                    "status": "error",
                    "message": f"Failed to persist checkpoint baseline: {sanitize_error(exc)}",
                },
                args.json,
            )
            return ExitCode.CONFLICT_OR_INVALID.value

    try:
        live_docs, live_errors = export_live_docs(client)
    except Exception as exc:
        live_docs, live_errors = {}, {"export": sanitize_error(exc)}
    summary = build_verify_summary(git_docs, mismatches, live_docs)

    res = {
        "status": "verified" if is_verified else "drift_detected",
        "instance": args.instance,
        "ha_version": health.get("version"),
        "mismatches": mismatches,
        "capture_errors": live_errors,
        **summary,
    }
    if checkpoint_requested and is_verified:
        res["checkpointed"] = True

    def _fmt(d: dict[str, Any]) -> None:
        print(f"Home Assistant desired-state verification ({d['instance']})")
        print()
        for s in d.get("surfaces", []):
            print(
                f"{s['surface']:<15} {s['converged']:>3}/{s['managed']:<3} converged"
                f"  drifted: {s['drifted']:<3}  unmanaged: {s['unmanaged']}"
            )
        print()
        if d.get("capture_errors"):
            print("Live capture incomplete; unmanaged counts may be partial:")
            for k, e in d["capture_errors"].items():
                print(f"  - {k}: {e}")
            print()
        if d["status"] == "verified":
            print(
                f"Managed converged: {d['managed_converged']}/{d['managed_total']}; "
                f"unmanaged (report only): {d['unmanaged_count']}."
            )
            msg = "Home Assistant converged with Git."
            if d.get("checkpointed"):
                msg += " Checkpointed baseline successfully."
            print(msg)
        else:
            print("Drifted managed resources (must match Git):")
            for m in d["mismatches"]:
                print(f"  - {m}")
            print()
            unmanaged = d.get("unmanaged", [])
            print(f"Unmanaged HA resources (report only): {len(unmanaged)}")
            for u in unmanaged[:20]:
                print(f"  - {u}")
            if len(unmanaged) > 20:
                print(f"  ... and {len(unmanaged) - 20} more")

    output_result(res, args.json, _fmt)
    return ExitCode.CLEAN.value if is_verified else ExitCode.DRIFT_OR_PENDING.value


# ----------------------------------------------------------------------
# Command: revert
# ----------------------------------------------------------------------
def cmd_revert(args: argparse.Namespace, repo_root: Path) -> int:
    selected = set(args.select) if args.select else None
    if not selected:
        output_result(
            {
                "status": "error",
                "message": "Specify --select <kind/key> to revert specific live resources to Git state",
            },
            args.json,
        )
        return ExitCode.CONFLICT_OR_INVALID.value

    git_docs = load_source_tree(repo_root)
    client = get_client(args, repo_root)
    planner = Planner(repo_root, args.instance, client=client)

    # Build revert actions restoring live from git
    actions: list[PlanAction] = []
    for rk_str in selected:
        if "/" not in rk_str:
            continue
        kind, key = rk_str.split("/", 1)
        adapter = get_adapter(kind)
        try:
            live_docs = {
                d.key: adapter.canonicalize(d) for d in adapter.export_from_live(client)
            }
        except Exception as exc:
            output_result({"status": "failed", "error": sanitize_error(exc)}, args.json)
            return ExitCode.UNAVAILABLE.value

        live_doc = live_docs.get(key)
        live_hash = (
            canonical_hash(live_doc.desired) if live_doc and live_doc.desired else None
        )

        git_doc = git_docs.get(rk_str)
        if git_doc is None:
            # Revert to deleted
            actions.append(
                PlanAction(
                    kind=kind,
                    key=key,
                    action=ActionType.DELETE,
                    before=live_doc.desired if live_doc else None,
                    after=None,
                    expected_live_hash=live_hash,
                )
            )
        else:
            canon_git = adapter.canonicalize(git_doc)
            actions.append(
                PlanAction(
                    kind=kind,
                    key=key,
                    action=ActionType.UPDATE if live_doc else ActionType.CREATE,
                    before=live_doc.desired if live_doc else None,
                    after=canon_git.desired,
                    expected_live_hash=live_hash,
                )
            )

    plan = ApplyPlan(
        plan_id=f"revert-{int(datetime.now(UTC).timestamp())}",
        timestamp=datetime.now(UTC).isoformat(),
        instance=args.instance,
        git_revision=get_git_revision(repo_root),
        baseline_hash="revert",
        actions=actions,
    )

    try:
        journal = planner.execute_plan(plan=plan, client=client, git_resources=git_docs)
        output_result(
            {
                "status": "reverted",
                "plan_id": plan.plan_id,
                "journal": [j.to_dict() for j in journal],
            },
            args.json,
            lambda d: print(f"Successfully reverted {len(actions)} resources to Git."),
        )
        return ExitCode.CLEAN.value
    except Exception as exc:
        output_result({"status": "failed", "error": sanitize_error(exc)}, args.json)
        return ExitCode.CONFLICT_OR_INVALID.value


# ----------------------------------------------------------------------
# CLI parser
# ----------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "--json", action="store_true", help="Output machine-readable JSON"
    )
    common_parser.add_argument(
        "--instance", default="homelab-01", help="Home Assistant instance name"
    )
    common_parser.add_argument(
        "--url", default=None, help="Base URL of Home Assistant instance"
    )
    common_parser.add_argument(
        "--token", default=None, help="Home Assistant long-lived access token"
    )
    common_parser.add_argument(
        "--token-file", default=None, help="Path to file containing access token"
    )
    common_parser.add_argument(
        "--mock", action="store_true", help="Use offline credential-free mock client"
    )

    parser = argparse.ArgumentParser(
        prog="python -m scripts.home_assistant",
        description="Home Assistant configuration ownership and UI adoption CLI",
        parents=[common_parser],
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # inventory
    subparsers.add_parser(
        "inventory",
        parents=[common_parser],
        help="Inspect live capabilities and configuration surfaces",
    )

    # capture
    subparsers.add_parser(
        "capture",
        parents=[common_parser],
        help="Create sanitized local live state snapshot",
    )

    # diff
    diff_p = subparsers.add_parser(
        "diff",
        parents=[common_parser],
        help="Perform 3-way diff between Baseline, Git, and Live",
    )
    diff_p.add_argument(
        "--select",
        action="append",
        help="Select resource key to diff (e.g. automation/foo)",
    )

    # adopt
    adopt_p = subparsers.add_parser(
        "adopt",
        parents=[common_parser],
        help="Adopt selected live UI definitions into Git source files",
    )
    adopt_p.add_argument(
        "--select",
        action="append",
        help="Select resource key to adopt (e.g. automation/foo)",
    )
    adopt_p.add_argument(
        "--all", action="store_true", help="Adopt all discovered live resources"
    )
    adopt_p.add_argument(
        "--allow-delete",
        action="store_true",
        help="Allow adoption of live UI deletions by removing Git source files",
    )
    adopt_p.add_argument(
        "--force",
        action="store_true",
        help="Force adoption even if deleted resources are referenced by other resources",
    )

    # validate
    validate_p = subparsers.add_parser(
        "validate",
        parents=[common_parser],
        help="Perform offline schema, reference, and secret checks",
    )
    validate_p.add_argument(
        "--sync-gitops",
        action="store_true",
        help="Sync home-assistant/core/configuration.yaml to gitops/home-assistant/config.yaml and update deployment checksum",
    )

    # plan
    plan_p = subparsers.add_parser(
        "plan",
        parents=[common_parser],
        help="Produce immutable deployment plan for Git changes",
    )
    plan_p.add_argument("--select", action="append", help="Select resource key to plan")

    # apply
    apply_p = subparsers.add_parser(
        "apply",
        parents=[common_parser],
        help="Execute apply plan with verification and locking",
    )
    apply_p.add_argument(
        "--plan-file", default=None, help="Path to plan file to execute"
    )
    apply_p.add_argument(
        "--select", action="append", help="Select resource key to apply"
    )
    apply_p.add_argument(
        "-y", "--yes", action="store_true", help="Auto-confirm apply without prompting"
    )

    # verify
    verify_p = subparsers.add_parser(
        "verify",
        parents=[common_parser],
        help="Verify live state against Git and Baseline",
    )
    verify_p.add_argument(
        "--checkpoint",
        action="store_true",
        help="Advance shared cluster baseline checkpoint after successful verification (requires clean Git working tree)",
    )
    verify_p.add_argument(
        "--bootstrap",
        action="store_true",
        help="Initialize shared cluster baseline checkpoint from clean Git working tree",
    )

    # revert
    revert_p = subparsers.add_parser(
        "revert",
        parents=[common_parser],
        help="Revert selected live resources back to Git state",
    )
    revert_p.add_argument(
        "--select", action="append", required=True, help="Select resource key to revert"
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    repo_root = find_repo_root()

    commands = {
        "inventory": cmd_inventory,
        "capture": cmd_capture,
        "diff": cmd_diff,
        "adopt": cmd_adopt,
        "validate": cmd_validate,
        "plan": cmd_plan,
        "apply": cmd_apply,
        "verify": cmd_verify,
        "revert": cmd_revert,
    }

    cmd_fn = commands.get(args.command)
    if cmd_fn is None:
        parser.print_help()
        sys.exit(ExitCode.CONFLICT_OR_INVALID.value)

    exit_code = cmd_fn(args, repo_root)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
