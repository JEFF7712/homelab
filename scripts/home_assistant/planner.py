from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .adapters import get_adapter
from .canonical import canonical_hash, canonical_json, compute_baseline_hash
from .client import HomeAssistantClient
from .models import (
    ActionType,
    ApplyPlan,
    BaselineRecord,
    DiffReport,
    DiffStatus,
    JournalEntry,
    LockError,
    PlanAction,
    ResourceDocument,
    StalePlanError,
)

LOCK_TIMEOUT_SECONDS = 900.0  # 15 minutes


class Planner:
    def __init__(
        self,
        repo_root: Path,
        instance: str = "homelab-01",
        client: HomeAssistantClient | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.instance = instance
        self.client = client
        self.state_dir = repo_root / ".agent-state" / "home-assistant" / instance
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_file = self.state_dir / "lock.json"
        self.baseline_file = self.state_dir / "baseline.json"
        self.journal_dir = self.state_dir / "journal"
        self.journal_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    def load_baseline(self) -> BaselineRecord | None:
        if self.client is not None:
            cluster_data = self.client.get_cluster_baseline(self.instance)
            if cluster_data:
                rec = BaselineRecord.from_dict(cluster_data)
                self.save_baseline_local(rec)
                return rec
            elif cluster_data is None:
                return None

        if not self.baseline_file.is_file():
            return None
        try:
            data = json.loads(self.baseline_file.read_text(encoding="utf-8"))
            return BaselineRecord.from_dict(data)
        except Exception:
            return None

    def save_baseline_local(self, baseline: BaselineRecord) -> None:
        self.baseline_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp_file = self.baseline_file.with_suffix(
            f".tmp.{os.getpid()}.{uuid.uuid4().hex}"
        )
        content = json.dumps(baseline.to_dict(), indent=2)
        fd = os.open(temp_file, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with open(fd, "w", encoding="utf-8") as f:
            f.write(content)
        temp_file.replace(self.baseline_file)

    def save_baseline(self, baseline: BaselineRecord) -> None:
        baseline.content_hash = compute_baseline_hash(baseline.resources)
        self.save_baseline_local(baseline)
        if self.client is not None:
            self.client.save_cluster_baseline(self.instance, baseline.to_dict())

    def acquire_lock(self, owner: str, plan_id: str) -> None:
        now = time.time()
        lock_data = {
            "owner": owner,
            "plan_id": plan_id,
            "acquired_at": now,
            "expires_at": now + LOCK_TIMEOUT_SECONDS,
        }
        content = json.dumps(lock_data, indent=2)

        local_acquired = False
        while not local_acquired:
            tmp_name = f"lock.tmp.{os.getpid()}.{uuid.uuid4().hex}"
            tmp_file = self.state_dir / tmp_name
            try:
                fd = os.open(tmp_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with open(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                os.link(tmp_file, self.lock_file)
                local_acquired = True
            except FileExistsError:
                try:
                    stat_res = self.lock_file.stat()
                    file_age = time.time() - stat_res.st_mtime
                except OSError:
                    continue

                try:
                    raw_text = self.lock_file.read_text(encoding="utf-8")
                    data = json.loads(raw_text) if raw_text.strip() else {}
                except (json.JSONDecodeError, OSError):
                    data = {}

                expires_at = data.get("expires_at", 0)
                held_owner = data.get("owner", "unknown")
                held_plan = data.get("plan_id", "unknown")

                if (file_age < 15.0 and not expires_at) or time.time() < expires_at:
                    raise LockError(
                        f"Apply lock already held by '{held_owner}' for plan '{held_plan}' until {expires_at or (stat_res.st_mtime + LOCK_TIMEOUT_SECONDS)}"
                    )

                try:
                    self.lock_file.unlink(missing_ok=True)
                except OSError:
                    pass
            finally:
                tmp_file.unlink(missing_ok=True)

        if self.client is not None:
            try:
                self.client.acquire_cluster_lock(
                    self.instance, owner, plan_id, int(LOCK_TIMEOUT_SECONDS)
                )
            except Exception:
                self.lock_file.unlink(missing_ok=True)
                raise

    def renew_lock(self, plan_id: str, owner: str) -> None:
        if self.lock_file.is_file():
            try:
                data = json.loads(self.lock_file.read_text(encoding="utf-8"))
                if data.get("plan_id") == plan_id and data.get("owner") == owner:
                    data["expires_at"] = time.time() + LOCK_TIMEOUT_SECONDS
                    tmp_renew = (
                        self.state_dir / f"lock.renew.{os.getpid()}.{uuid.uuid4().hex}"
                    )
                    tmp_renew.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    os.replace(tmp_renew, self.lock_file)
            except Exception:
                pass
        if self.client is not None:
            try:
                self.client.renew_cluster_lock(
                    self.instance, owner, plan_id, int(LOCK_TIMEOUT_SECONDS)
                )
            except Exception:
                pass

    def release_lock(self, plan_id: str, owner: str | None = None) -> None:
        if self.lock_file.is_file():
            try:
                data = json.loads(self.lock_file.read_text(encoding="utf-8"))
                if data.get("plan_id") == plan_id and (
                    owner is None or data.get("owner") == owner
                ):
                    self.lock_file.unlink(missing_ok=True)
            except Exception:
                pass
        if self.client is not None and owner is not None:
            try:
                self.client.release_cluster_lock(self.instance, owner, plan_id)
            except Exception:
                pass

    def record_journal(self, entry: JournalEntry) -> None:
        journal_file = self.journal_dir / f"{entry.plan_id}.jsonl"
        with journal_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict()) + "\n")

    def create_plan(
        self,
        diff_report: DiffReport,
        git_revision: str,
        baseline_hash: str,
        selected_keys: set[str] | None = None,
        ha_version: str = "",
        source_hash: str = "",
    ) -> ApplyPlan:
        """Create an immutable apply plan from a diff report."""
        actions: list[PlanAction] = []
        skipped: list[str] = []
        plan_id = f"plan-{uuid.uuid4().hex[:8]}"

        for item in diff_report.items:
            rk_str = str(item.resource_key)
            if (
                selected_keys is not None
                and rk_str not in selected_keys
                and item.key not in selected_keys
            ):
                continue

            # Check for unresolved conflicts
            if item.status == DiffStatus.CONFLICT:
                raise ValueError(
                    f"Cannot generate plan with unresolved conflict on {rk_str}"
                )

            # Check for unknown / failed reads
            if item.status == DiffStatus.UNKNOWN:
                raise ValueError(
                    f"Cannot generate plan with unknown live state on {rk_str}: {item.details}"
                )

            # Observe-only adapters cannot be mutated. An explicitly selected
            # resource is still an error; otherwise skip it so one
            # observe-only drift cannot block the rest of the plan.
            adapter = get_adapter(item.kind)
            if item.status == DiffStatus.GIT_CHANGE and not adapter.supports_mutation:
                if selected_keys is not None and (
                    rk_str in selected_keys or item.key in selected_keys
                ):
                    raise ValueError(
                        f"Cannot plan mutation for observe-only resource {rk_str}. "
                        f"Resource kind '{item.kind}' is observe-only and cannot be mutated."
                    )
                skipped.append(rk_str)
                continue

            # Git changes are planned
            if item.status == DiffStatus.GIT_CHANGE:
                if item.baseline_hash is None and item.live_hash is None:
                    # New resource in Git
                    actions.append(
                        PlanAction(
                            kind=item.kind,
                            key=item.key,
                            action=ActionType.CREATE,
                            before=None,
                            after=item.git,
                            expected_live_hash=None,
                        )
                    )
                elif item.git_hash is None:
                    # Deleted from Git
                    actions.append(
                        PlanAction(
                            kind=item.kind,
                            key=item.key,
                            action=ActionType.DELETE,
                            before=item.live,
                            after=None,
                            expected_live_hash=item.live_hash,
                        )
                    )
                else:
                    # Updated in Git
                    actions.append(
                        PlanAction(
                            kind=item.kind,
                            key=item.key,
                            action=ActionType.UPDATE,
                            before=item.live,
                            after=item.git,
                            expected_live_hash=item.live_hash,
                        )
                    )

        now_iso = datetime.now(UTC).isoformat()
        return ApplyPlan(
            plan_id=plan_id,
            timestamp=now_iso,
            instance=self.instance,
            git_revision=git_revision,
            baseline_hash=baseline_hash,
            actions=actions,
            ha_version=ha_version,
            source_hash=source_hash,
            skipped=skipped,
        )

    def execute_plan(
        self,
        plan: ApplyPlan,
        client: HomeAssistantClient,
        git_resources: dict[str, ResourceDocument],
        owner: str = "agent-cli",
    ) -> list[JournalEntry]:
        """Execute an apply plan with locking, read-before-write checks, journaling, and verification."""
        if self.client is None:
            self.client = client

        # 1. Validate plan target instance
        if plan.instance != self.instance:
            raise StalePlanError(
                f"Plan target instance '{plan.instance}' does not match executor instance '{self.instance}'"
            )

        # 2. Validate plan age TTL (plans expire after 30 minutes)
        try:
            plan_dt = datetime.fromisoformat(plan.timestamp)
            age = (datetime.now(UTC) - plan_dt).total_seconds()
            if age > 1800:
                raise StalePlanError(
                    f"Plan '{plan.plan_id}' expired ({int(age)}s old > 1800s TTL). Please generate a fresh plan."
                )
        except (ValueError, TypeError):
            pass

        # 3. Acquire atomic lock (both local and cluster)
        self.acquire_lock(owner=owner, plan_id=plan.plan_id)
        journal: list[JournalEntry] = []

        try:
            # 4. Under shared lock: validate baseline binding
            baseline = self.load_baseline()
            if baseline is None:
                if plan.baseline_hash and plan.baseline_hash not in (
                    "uninitialized",
                    "bootstrap",
                    "revert",
                ):
                    raise StalePlanError(
                        f"Missing or uninitialized baseline. Explicit bootstrap required before executing plan '{plan.plan_id}' expecting baseline hash '{plan.baseline_hash}'."
                    )
                baseline = BaselineRecord(
                    instance=self.instance,
                    source_commit=plan.git_revision,
                    content_hash=plan.baseline_hash,
                    timestamp=datetime.now(UTC).isoformat(),
                    resources={},
                )
            elif (
                plan.baseline_hash
                and plan.baseline_hash != "revert"
                and plan.baseline_hash != baseline.content_hash
            ):
                raise StalePlanError(
                    f"Stale plan baseline refusal: plan expects baseline '{plan.baseline_hash}', "
                    f"but current baseline is '{baseline.content_hash}'"
                )

            # 5. Under shared lock: validate HA version binding if specified
            if plan.ha_version and plan.ha_version != "unknown":
                try:
                    health = client.check_health()
                    live_ver = health.get("version")
                    if live_ver and live_ver != plan.ha_version:
                        raise StalePlanError(
                            f"Plan HA version mismatch: plan was created for HA '{plan.ha_version}', but target is running '{live_ver}'"
                        )
                except StalePlanError:
                    raise
                except Exception:
                    pass

            # 6. Under shared lock: validate source tree fingerprint if specified
            if plan.source_hash and git_resources:
                source_payload = canonical_json(
                    {rk: d.desired for rk, d in sorted(git_resources.items())}
                )
                current_src_hash = hashlib.sha256(
                    source_payload.encode("utf-8")
                ).hexdigest()
                if current_src_hash != plan.source_hash:
                    raise StalePlanError(
                        "Plan source fingerprint mismatch: Git source tree modified since plan was generated."
                    )

            # 7. Under shared lock: validate planned actions match current Git source definitions
            for action in plan.actions:
                rk_str = f"{action.kind}/{action.key}"
                git_doc = git_resources.get(rk_str)
                adapter = get_adapter(action.kind)
                if not adapter.supports_mutation:
                    raise StalePlanError(
                        f"Cannot execute mutation on observe-only resource {rk_str}."
                    )
                if action.action in (ActionType.CREATE, ActionType.UPDATE):
                    if git_doc is None:
                        raise StalePlanError(
                            f"Stale plan refusal on {rk_str}: resource was removed from Git source since plan generation."
                        )
                    canon_git = adapter.canonicalize(git_doc).desired
                    canon_after = adapter.canonicalize(
                        ResourceDocument(
                            kind=action.kind, key=action.key, desired=action.after
                        )
                    ).desired
                    if canonical_hash(canon_git) != canonical_hash(canon_after):
                        raise StalePlanError(
                            f"Stale plan refusal on {rk_str}: Git source modified since plan generation."
                        )
                elif action.action == ActionType.DELETE:
                    if git_doc is not None:
                        raise StalePlanError(
                            f"Stale plan refusal on {rk_str}: planned for deletion but now present in Git source."
                        )

            for action in plan.actions:
                self.renew_lock(plan_id=plan.plan_id, owner=owner)
                rk_str = f"{action.kind}/{action.key}"
                now_str = datetime.now(UTC).isoformat()
                adapter = get_adapter(action.kind)

                # Record start
                entry_start = JournalEntry(
                    plan_id=plan.plan_id,
                    resource=rk_str,
                    action=action.action.value,
                    status="started",
                    timestamp=now_str,
                )
                self.record_journal(entry_start)
                journal.append(entry_start)

                # 1. Read-before-write check
                live_docs = {
                    d.key: adapter.canonicalize(d)
                    for d in adapter.export_from_live(client)
                }
                current_live_doc = live_docs.get(action.key)
                current_live_hash = (
                    canonical_hash(current_live_doc.desired)
                    if current_live_doc and current_live_doc.desired
                    else None
                )

                if current_live_hash != action.expected_live_hash:
                    err_msg = (
                        f"Stale plan refusal on {rk_str}: expected live hash "
                        f"{action.expected_live_hash}, but found {current_live_hash}"
                    )
                    entry_fail = JournalEntry(
                        plan_id=plan.plan_id,
                        resource=rk_str,
                        action=action.action.value,
                        status="failed",
                        timestamp=datetime.now(UTC).isoformat(),
                        error=err_msg,
                    )
                    self.record_journal(entry_fail)
                    journal.append(entry_fail)
                    raise StalePlanError(err_msg)

                # 2. Execute mutation
                try:
                    adapter.apply(client, action)
                    entry_applied = JournalEntry(
                        plan_id=plan.plan_id,
                        resource=rk_str,
                        action=action.action.value,
                        status="applied",
                        timestamp=datetime.now(UTC).isoformat(),
                    )
                    self.record_journal(entry_applied)
                    journal.append(entry_applied)
                except Exception as exc:
                    entry_fail = JournalEntry(
                        plan_id=plan.plan_id,
                        resource=rk_str,
                        action=action.action.value,
                        status="failed",
                        timestamp=datetime.now(UTC).isoformat(),
                        error=str(exc),
                    )
                    self.record_journal(entry_fail)
                    journal.append(entry_fail)
                    raise

                # 3. Readback verification
                if action.action == ActionType.DELETE:
                    # Verify absence
                    post_live_docs = {
                        d.key: d for d in adapter.export_from_live(client)
                    }
                    verified = action.key not in post_live_docs
                else:
                    target_doc = git_resources.get(rk_str)
                    if target_doc is None:
                        target_doc = ResourceDocument(
                            kind=action.kind, key=action.key, desired=action.after
                        )
                    verified = adapter.verify(client, target_doc)

                if not verified:
                    err_msg = f"Readback verification failed on {rk_str}"
                    entry_unverified = JournalEntry(
                        plan_id=plan.plan_id,
                        resource=rk_str,
                        action=action.action.value,
                        status="failed",
                        timestamp=datetime.now(UTC).isoformat(),
                        error=err_msg,
                    )
                    self.record_journal(entry_unverified)
                    journal.append(entry_unverified)
                    raise RuntimeError(err_msg)

                # 4. Advance baseline checkpoint for this verified resource
                if action.action == ActionType.DELETE:
                    baseline.resources.pop(rk_str, None)
                else:
                    norm_desired = adapter.canonicalize(
                        ResourceDocument(
                            kind=action.kind, key=action.key, desired=action.after
                        )
                    ).desired
                    baseline.resources[rk_str] = {
                        "canonical_hash": canonical_hash(norm_desired),
                        "desired": norm_desired,
                        "verified_at": datetime.now(UTC).isoformat(),
                    }
                baseline.timestamp = datetime.now(UTC).isoformat()
                self.save_baseline(baseline)

                entry_verified = JournalEntry(
                    plan_id=plan.plan_id,
                    resource=rk_str,
                    action=action.action.value,
                    status="verified",
                    timestamp=datetime.now(UTC).isoformat(),
                )
                self.record_journal(entry_verified)
                journal.append(entry_verified)

            return journal

        finally:
            self.release_lock(plan.plan_id, owner=owner)
