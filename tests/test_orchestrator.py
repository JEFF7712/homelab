import contextlib
import io
import json
import sqlite3
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from scripts.orchestrator import __main__ as cli
from scripts.orchestrator import core
from scripts.orchestrator.core import (
    MAX_ATTEMPTS,
    PAYLOAD_LIMIT,
    Evidence,
    OrchestratorError,
    Store,
    TaskSpec,
    filesystem_type,
)

POLICY = "policy-1"
CRITERIA = ("tests pass", "README documents the flag")


def spec(**overrides: object) -> TaskSpec:
    values: dict[str, object] = {
        "project": "sandbox",
        "title": "Add a --dry-run flag",
        "goal": "Let operators preview changes",
        "acceptance_criteria": CRITERIA,
    }
    values.update(overrides)
    return TaskSpec(**values)  # type: ignore[arg-type]


def evidence(**overrides: object) -> Evidence:
    values: dict[str, object] = {
        "pull_request": "JEFF7712/sandbox#3",
        "head_sha": "a" * 40,
        "ci_state": "success",
        "criteria_met": CRITERIA,
        "summary": "flag added",
    }
    values.update(overrides)
    return Evidence(**values)  # type: ignore[arg-type]


class StoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "orchestrator.sqlite"
        self.store = Store(self.path, POLICY, controller=True)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def running(self, now: float = 0.0) -> tuple[str, str]:
        task, _ = self.store.create_task("rupan", f"key-{now}", spec(), now)
        claimed = self.store.claim(
            "sched", "openai-codex", "gpt-5.6-sol", "pi", 60, now
        )
        assert claimed is not None
        self.store.transition(task.id, "running", "sched", "started", now, "claimed")
        return task.id, claimed[1].id


class TaskCreationTests(StoreCase):
    def test_retried_create_returns_the_same_task(self) -> None:
        first, created = self.store.create_task("rupan", "k1", spec(), 1.0)
        again, created_again = self.store.create_task("rupan", "k1", spec(), 2.0)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.id, again.id)
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_reused_key_with_different_request_is_a_conflict(self) -> None:
        self.store.create_task("rupan", "k1", spec(), 1.0)
        with self.assertRaises(OrchestratorError) as raised:
            self.store.create_task("rupan", "k1", spec(title="Something else"), 2.0)
        self.assertEqual(raised.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_keys_are_scoped_per_principal(self) -> None:
        a, _ = self.store.create_task("rupan", "k1", spec(), 1.0)
        b, _ = self.store.create_task("other", "k1", spec(), 1.0)
        self.assertNotEqual(a.id, b.id)

    def test_acceptance_criteria_are_required(self) -> None:
        with self.assertRaises(OrchestratorError) as raised:
            self.store.create_task("rupan", "k1", spec(acceptance_criteria=()), 1.0)
        self.assertEqual(raised.exception.code, "INVALID_SPEC")

    def test_unknown_dependency_is_refused(self) -> None:
        with self.assertRaises(OrchestratorError) as raised:
            self.store.create_task(
                "rupan", "k1", spec(depends_on=("task_missing",)), 1.0
            )
        self.assertEqual(raised.exception.code, "NOT_FOUND")

    def test_dependency_cycles_are_refused(self) -> None:
        a, _ = self.store.create_task("rupan", "a", spec(), 1.0)
        b, _ = self.store.create_task("rupan", "b", spec(depends_on=(a.id,)), 1.0)
        c, _ = self.store.create_task("rupan", "c", spec(depends_on=(b.id,)), 1.0)
        for task_id, depends_on in ((a.id, c.id), (a.id, a.id)):
            with self.assertRaises(OrchestratorError) as raised:
                self.store.add_dependency(task_id, depends_on)
            self.assertEqual(raised.exception.code, "DEPENDENCY_CYCLE")

    def test_state_survives_reopening_the_database(self) -> None:
        task, _ = self.store.create_task("rupan", "k1", spec(), 1.0)
        self.store.close()
        self.store = Store(self.path, POLICY, controller=True)
        self.assertEqual(self.store.get_task(task.id).title, spec().title)
        self.assertEqual(
            self.store.create_task("rupan", "k1", spec(), 2.0)[0].id, task.id
        )


class TransitionTests(StoreCase):
    def test_invalid_and_stale_transitions_are_refused(self) -> None:
        task, _ = self.store.create_task("rupan", "k1", spec(), 1.0)
        with self.assertRaises(OrchestratorError) as raised:
            self.store.transition(task.id, "running", "x", "skip", 2.0)
        self.assertEqual(raised.exception.code, "INVALID_TRANSITION")
        with self.assertRaises(OrchestratorError) as raised:
            self.store.transition(task.id, "cancelled", "x", "late", 2.0, "running")
        self.assertEqual(raised.exception.code, "STALE")
        self.assertEqual(self.store.get_task(task.id).status, "ready")

    def test_history_records_every_transition(self) -> None:
        task_id, _ = self.running()
        self.assertEqual(
            [(old, new) for old, new, _, _ in self.store.history(task_id)],
            [(None, "ready"), ("ready", "claimed"), ("claimed", "running")],
        )

    def validating(self) -> str:
        task_id, attempt = self.running()
        self.store.settle_attempt(attempt, "sched", {"final_text": "done"}, 1.0)
        return task_id

    def test_review_and_completion_cannot_be_reached_by_plain_transitions(
        self,
    ) -> None:
        task_id = self.validating()
        for target in ("in_review", "completed"):
            with self.assertRaises(OrchestratorError) as raised:
                self.store.transition(task_id, target, "worker", "trust me", 2.0)
            self.assertEqual(raised.exception.code, "EVIDENCE_REQUIRED")

    def test_verification_requires_passing_ci_and_every_criterion(self) -> None:
        task_id = self.validating()
        for bad in (
            evidence(ci_state="failure"),
            evidence(criteria_met=CRITERIA[:1]),
            evidence(head_sha=""),
            evidence(head_sha="abc"),
            evidence(pull_request=""),
        ):
            with self.assertRaises(OrchestratorError) as raised:
                self.store.record_verification(task_id, bad, "verifier", 2.0)
            self.assertEqual(raised.exception.code, "EVIDENCE_REJECTED")
            self.assertEqual(self.store.get_task(task_id).status, "validating")
        self.store.record_verification(task_id, evidence(), "verifier", 3.0)
        self.assertEqual(self.store.get_task(task_id).status, "in_review")
        done = self.store.complete(task_id, "controller", 4.0)
        self.assertEqual(done.status, "completed")
        completed = [
            e for e in self.store.events(task_id) if e["type"] == "task.completed"
        ]
        self.assertEqual(completed[0]["payload"]["head_sha"], "a" * 40)
        self.assertEqual(completed[0]["payload"]["verifier"], "verifier")

    def test_completion_needs_review_state_and_a_stored_verification(self) -> None:
        task_id = self.validating()
        with self.assertRaises(OrchestratorError) as raised:
            self.store.complete(task_id, "controller", 2.0)
        self.assertEqual(raised.exception.code, "EVIDENCE_REQUIRED")

    def test_only_the_controller_verifies_and_completes(self) -> None:
        task_id = self.validating()
        reader = Store(self.path, POLICY)
        try:
            for call in (
                lambda: reader.record_verification(task_id, evidence(), "w", 2.0),
                lambda: reader.complete(task_id, "w", 2.0),
            ):
                with self.assertRaises(OrchestratorError) as raised:
                    call()
                self.assertEqual(raised.exception.code, "NOT_CONTROLLER")
        finally:
            reader.close()


class SchedulingTests(StoreCase):
    def claim(self, now: float = 1.0, max_running: int = 1):
        self.store.set_max_running(max_running)
        return self.store.claim("sched", "openai-codex", "m", "pi", 60, now)

    def test_claims_follow_priority_and_dependencies(self) -> None:
        base, _ = self.store.create_task("rupan", "base", spec(), 1.0)
        self.store.create_task(
            "rupan", "child", spec(depends_on=(base.id,), priority=9), 1.0
        )
        low, _ = self.store.create_task("rupan", "low", spec(priority=-1), 1.0)
        first = self.claim(max_running=3)
        second = self.claim(max_running=3)
        assert first is not None and second is not None
        self.assertEqual(first[0].id, base.id)
        self.assertEqual(second[0].id, low.id)
        self.assertIsNone(self.claim(max_running=3))

    def test_dependents_become_claimable_after_completion(self) -> None:
        base_id, attempt = self.running()
        child, _ = self.store.create_task(
            "rupan", "child", spec(depends_on=(base_id,)), 1.0
        )
        self.assertIsNone(self.claim(max_running=5))
        self.store.settle_attempt(attempt, "sched", {"final_text": "done"}, 2.0)
        self.store.record_verification(base_id, evidence(), "verifier", 2.0)
        self.store.complete(base_id, "controller", 2.0)
        claimed = self.claim(3.0)
        assert claimed is not None
        self.assertEqual(claimed[0].id, child.id)

    def test_concurrency_cap_holds_across_threads(self) -> None:
        for index in range(6):
            self.store.create_task("rupan", f"k{index}", spec(), 1.0)
        self.store.set_max_running(2)
        results: list[object] = []

        def run() -> None:
            for _ in range(3):
                results.append(self.store.claim("s", "p", "m", "pi", 60, 1.0))

        threads = [threading.Thread(target=run) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sum(result is not None for result in results), 2)

    def test_only_one_controller_may_open_the_store(self) -> None:
        with self.assertRaises(OrchestratorError) as raised:
            Store(self.path, POLICY, controller=True)
        self.assertEqual(raised.exception.code, "CONTROLLER_ACTIVE")
        reader = Store(self.path, POLICY)
        try:
            with self.assertRaises(OrchestratorError) as raised:
                reader.claim("s", "p", "m", "pi", 60, 1.0)
            self.assertEqual(raised.exception.code, "NOT_CONTROLLER")
            with self.assertRaises(OrchestratorError):
                reader.reap(1.0)
        finally:
            reader.close()

    def test_cancelled_task_lease_cannot_be_renewed(self) -> None:
        task_id, attempt = self.running(0.0)
        self.store.cancel(task_id, "rupan", "stop", 1.0)
        with self.assertRaises(OrchestratorError) as raised:
            self.store.renew_lease(attempt, "sched", 60, 2.0)
        self.assertEqual(raised.exception.code, "LEASE_LOST")

    def test_token_lookup_requires_an_unexpired_lease(self) -> None:
        self.store.create_task("rupan", "k", spec(), 0.0)
        claimed = self.store.claim("sched", "p", "m", "pi", 60, 0.0, "sha256:t")
        assert claimed is not None
        self.assertIsNotNone(self.store.attempt_for_token("sha256:t", 30.0))
        self.assertIsNone(self.store.attempt_for_token("sha256:t", 61.0))

    def test_reaped_attempt_is_fenced(self) -> None:
        _, attempt = self.running(0.0)
        self.store.reap(100.0)
        cancels = self.store.attempt_messages(attempt, "worker.cancel")
        self.assertEqual([m.payload["reason"] for m in cancels], ["lease expired"])

    def test_settle_is_atomic_and_needs_the_live_lease(self) -> None:
        task_id, attempt = self.running(0.0)
        with self.assertRaises(OrchestratorError) as raised:
            self.store.settle_attempt(attempt, "intruder", {"final_text": "x"}, 1.0)
        self.assertEqual(raised.exception.code, "LEASE_LOST")
        with self.assertRaises(OrchestratorError):
            self.store.settle_attempt(attempt, "sched", {"final_text": "x"}, 61.0)
        self.assertEqual(self.store.get_task(task_id).status, "running")
        task = self.store.settle_attempt(attempt, "sched", {"final_text": "x"}, 2.0)
        self.assertEqual(task.status, "validating")
        self.assertIsNotNone(self.store.attempt(attempt).finished)

    def test_answers_and_cancels_are_scoped_to_their_attempt(self) -> None:
        _, attempt = self.running(0.0)
        for index in range(60):
            with self.store.transaction() as db:
                self.store._enqueue(
                    db,
                    f"noise{index}",
                    "worker.answer",
                    None,
                    {"attempt_id": "other"},
                    0.0,
                )
        self.store.ask("q1", attempt, "p", (), True, 600, 1.0)
        self.store.answer("q1", 1, "yes", "rupan", 2.0)
        mine = self.store.attempt_messages(attempt, "worker.answer")
        self.assertEqual([m.payload["answer"] for m in mine], ["yes"])

    def test_expired_lease_is_retried_then_failed(self) -> None:
        task, _ = self.store.create_task("rupan", "k", spec(), 0.0)
        for number in range(1, MAX_ATTEMPTS + 1):
            claimed = self.claim(float(number * 100))
            assert claimed is not None
            self.assertEqual(claimed[1].number, number)
            self.assertEqual(self.store.reap(float(number * 100 + 30)), [])
            self.assertEqual(self.store.reap(float(number * 100 + 61)), [claimed[1].id])
        self.assertEqual(self.store.get_task(task.id).status, "failed")

    def test_lost_lease_cannot_be_renewed(self) -> None:
        _, attempt = self.running(0.0)
        self.store.renew_lease(attempt, "sched", 60, 30.0)
        with self.assertRaises(OrchestratorError) as raised:
            self.store.renew_lease(attempt, "other", 60, 31.0)
        self.assertEqual(raised.exception.code, "LEASE_LOST")
        self.store.reap(200.0)
        with self.assertRaises(OrchestratorError):
            self.store.renew_lease(attempt, "sched", 60, 201.0)

    def test_cancel_requests_worker_stop_and_keeps_the_slot_until_confirmed(
        self,
    ) -> None:
        task_id, attempt = self.running()
        self.store.create_task("rupan", "next", spec(), 1.0)
        self.store.cancel(task_id, "rupan", "changed my mind", 2.0)
        self.assertEqual(self.store.get_task(task_id).status, "cancelled")
        cancels = self.store.pending_messages("worker.cancel")
        self.assertEqual([m.payload["attempt_id"] for m in cancels], [attempt])
        self.assertIsNone(self.claim(3.0))
        self.store.finish_attempt(attempt, "terminated", True, "adapter", 4.0)
        self.assertEqual(self.store.get_task(task_id).status, "cancelled")
        self.assertIsNotNone(self.claim(5.0))

    def test_settled_attempt_leaves_a_validating_task_alone(self) -> None:
        task_id, attempt = self.running()
        self.store.transition(task_id, "validating", "adapter", "settled", 2.0)
        self.store.finish_attempt(attempt, "settled", False, "adapter", 3.0)
        self.assertEqual(self.store.get_task(task_id).status, "validating")
        self.assertIsNone(self.store.reap(1000.0) or None)

    def test_finishing_an_attempt_twice_is_harmless(self) -> None:
        task_id, attempt = self.running()
        self.store.finish_attempt(attempt, "crash", True, "adapter", 2.0)
        self.store.finish_attempt(attempt, "crash", True, "adapter", 3.0)
        self.assertEqual(self.store.get_task(task_id).status, "ready")


class QuestionTests(StoreCase):
    def test_first_answer_wins_and_resumes_the_task(self) -> None:
        task_id, attempt = self.running()
        self.store.ask(
            "q1", attempt, "local or oauth?", ("local", "oauth"), True, 600, 1.0
        )
        self.assertEqual(self.store.get_task(task_id).status, "needs_input")
        answered = self.store.answer("q1", 1, "oauth", "rupan", 2.0)
        self.assertEqual(answered.answer, "oauth")
        self.assertEqual(self.store.get_task(task_id).status, "running")
        with self.assertRaises(OrchestratorError) as raised:
            self.store.answer("q1", 1, "local", "rupan", 3.0)
        self.assertEqual(raised.exception.code, "ALREADY_ANSWERED")
        answers = self.store.pending_messages("worker.answer")
        self.assertEqual([m.payload["answer"] for m in answers], ["oauth"])

    def test_task_resumes_only_after_every_blocking_question(self) -> None:
        task_id, attempt = self.running()
        self.store.ask("q1", attempt, "first?", (), True, 600, 1.0)
        self.store.ask("q2", attempt, "second?", (), True, 600, 1.0)
        self.store.answer("q1", 1, "a", "rupan", 2.0)
        self.assertEqual(self.store.get_task(task_id).status, "needs_input")
        self.store.answer("q2", 1, "b", "rupan", 3.0)
        self.assertEqual(self.store.get_task(task_id).status, "running")

    def test_redelivered_question_is_stored_once(self) -> None:
        _, attempt = self.running()
        self.store.ask("q1", attempt, "p", (), True, 600, 1.0)
        self.store.ask("q1", attempt, "p", (), True, 600, 1.5)
        self.assertEqual(len(self.store.pending_messages("question.open")), 1)

    def test_answers_to_an_old_revision_are_rejected(self) -> None:
        _, attempt = self.running()
        self.store.ask("q1", attempt, "a or b?", ("a", "b"), True, 600, 1.0)
        self.store.revise("q1", "a, b or c?", ("a", "b", "c"), 600, 2.0)
        with self.assertRaises(OrchestratorError) as raised:
            self.store.answer("q1", 1, "a", "rupan", 3.0)
        self.assertEqual(raised.exception.code, "STALE")
        self.assertEqual(self.store.answer("q1", 2, "c", "rupan", 4.0).answer, "c")

    def test_overdue_question_cannot_be_revived_by_revision(self) -> None:
        _, attempt = self.running()
        self.store.ask("q1", attempt, "p", (), True, 60, 1.0)
        with self.assertRaises(OrchestratorError) as raised:
            self.store.revise("q1", "p2", (), 600, 100.0)
        self.assertEqual(raised.exception.code, "STALE")
        with self.assertRaises(OrchestratorError):
            self.store.answer("q1", 2, "x", "rupan", 101.0)

    def test_cancelled_attempt_closes_its_questions(self) -> None:
        task_id, attempt = self.running()
        self.store.ask("q1", attempt, "p", (), True, 600, 1.0)
        self.store.cancel(task_id, "rupan", "stop", 2.0)
        self.store.finish_attempt(attempt, "terminated", False, "adapter", 3.0)
        with self.assertRaises(OrchestratorError) as raised:
            self.store.answer("q1", 1, "late", "rupan", 4.0)
        self.assertEqual(raised.exception.code, "STALE")
        self.assertEqual(self.store.attempt_messages(attempt, "worker.answer"), [])

    def test_answer_outside_the_choices_is_rejected(self) -> None:
        _, attempt = self.running()
        self.store.ask("q1", attempt, "a or b?", ("a", "b"), True, 600, 1.0)
        with self.assertRaises(OrchestratorError) as raised:
            self.store.answer("q1", 1, "rm -rf", "rupan", 2.0)
        self.assertEqual(raised.exception.code, "INVALID_ANSWER")

    def test_expired_question_rejects_answers_and_stops_the_worker(self) -> None:
        task_id, attempt = self.running()
        self.store.ask("q1", attempt, "p", (), True, 60, 1.0)
        with self.assertRaises(OrchestratorError) as raised:
            self.store.answer("q1", 1, "late", "rupan", 100.0)
        self.assertEqual(raised.exception.code, "STALE")
        self.assertEqual(self.store.get_task(task_id).status, "needs_input")
        cancels = self.store.pending_messages("worker.cancel")
        self.assertEqual([m.payload["attempt_id"] for m in cancels], [attempt])
        self.store.finish_attempt(attempt, "question expired", True, "adapter", 101.0)
        self.assertEqual(self.store.get_task(task_id).status, "ready")

    def test_questions_of_a_finished_attempt_are_closed(self) -> None:
        _, attempt = self.running()
        self.store.ask("q1", attempt, "p", (), False, 600, 1.0)
        self.store.finish_attempt(attempt, "crash", True, "adapter", 2.0)
        with self.assertRaises(OrchestratorError) as raised:
            self.store.answer("q1", 1, "x", "rupan", 3.0)
        self.assertEqual(raised.exception.code, "STALE")
        with self.assertRaises(OrchestratorError):
            self.store.ask("q2", attempt, "p", (), True, 600, 4.0)


class ApprovalTests(StoreCase):
    ACTION = {
        "kind": "open_pull_request",
        "repository": "JEFF7712/sandbox",
        "base": "main",
    }

    def test_approval_covers_only_the_exact_action_once(self) -> None:
        task_id, attempt = self.running()
        approval, created = self.store.request_approval(
            task_id, attempt, self.ACTION, "rupan", 600, 1.0
        )
        again, created_again = self.store.request_approval(
            task_id, attempt, self.ACTION, "rupan", 600, 1.5
        )
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(approval.id, again.id)
        consume = self.store.consume
        self.assertFalse(consume(task_id, attempt, self.ACTION, "rupan", 2.0))
        self.store.decide(approval.id, True, "rupan", 2.0)
        changed = {**self.ACTION, "base": "release"}
        self.assertFalse(consume(task_id, attempt, changed, "rupan", 3.0))
        self.assertFalse(consume(task_id, attempt, self.ACTION, "other", 3.0))
        self.assertFalse(consume(task_id, attempt, self.ACTION, "rupan", 700.0))
        self.assertTrue(consume(task_id, attempt, self.ACTION, "rupan", 3.0))
        self.assertFalse(consume(task_id, attempt, self.ACTION, "rupan", 4.0))

    def test_approval_dies_with_its_attempt(self) -> None:
        task_id, attempt = self.running(0.0)
        approval, _ = self.store.request_approval(
            task_id, attempt, self.ACTION, "rupan", 600, 1.0
        )
        self.store.decide(approval.id, True, "rupan", 2.0)
        self.assertFalse(
            self.store.consume(task_id, "att_x", self.ACTION, "rupan", 3.0)
        )
        self.store.reap(100.0)
        retry = self.store.claim("sched", "p", "m", "pi", 600, 101.0)
        assert retry is not None
        self.assertFalse(
            self.store.consume(task_id, attempt, self.ACTION, "rupan", 102.0)
        )
        self.assertFalse(
            self.store.consume(task_id, retry[1].id, self.ACTION, "rupan", 102.0)
        )

    def test_approval_for_a_cancelled_task_is_void(self) -> None:
        task_id, attempt = self.running()
        approval, _ = self.store.request_approval(
            task_id, attempt, self.ACTION, "rupan", 600, 1.0
        )
        self.store.decide(approval.id, True, "rupan", 2.0)
        self.store.cancel(task_id, "rupan", "stop", 3.0)
        self.assertFalse(
            self.store.consume(task_id, attempt, self.ACTION, "rupan", 4.0)
        )

    def test_policy_change_invalidates_approvals(self) -> None:
        task_id, attempt = self.running()
        approval, _ = self.store.request_approval(
            task_id, attempt, self.ACTION, "rupan", 600, 1.0
        )
        self.store.decide(approval.id, True, "rupan", 2.0)
        self.store.close()
        self.store = Store(self.path, "policy-2", controller=True)
        self.assertFalse(
            self.store.consume(task_id, attempt, self.ACTION, "rupan", 3.0)
        )

    def test_only_the_principal_may_decide_and_only_once(self) -> None:
        task_id, attempt = self.running()
        approval, _ = self.store.request_approval(
            task_id, attempt, self.ACTION, "rupan", 600, 1.0
        )
        with self.assertRaises(OrchestratorError) as raised:
            self.store.decide(approval.id, True, "intruder", 2.0)
        self.assertEqual(raised.exception.code, "FORBIDDEN")
        self.store.decide(approval.id, False, "rupan", 2.0)
        with self.assertRaises(OrchestratorError) as raised:
            self.store.decide(approval.id, True, "rupan", 3.0)
        self.assertEqual(raised.exception.code, "STALE")
        self.assertFalse(
            self.store.consume(task_id, attempt, self.ACTION, "rupan", 3.0)
        )

    def test_expired_approval_cannot_be_decided(self) -> None:
        task_id, attempt = self.running()
        approval, _ = self.store.request_approval(
            task_id, attempt, self.ACTION, "rupan", 60, 1.0
        )
        with self.assertRaises(OrchestratorError) as raised:
            self.store.decide(approval.id, True, "rupan", 100.0)
        self.assertEqual(raised.exception.code, "STALE")
        fresh, created = self.store.request_approval(
            task_id, attempt, self.ACTION, "rupan", 60, 101.0
        )
        self.assertTrue(created)
        self.assertNotEqual(fresh.id, approval.id)


class EventAndOutboxTests(StoreCase):
    def test_redelivered_event_is_recorded_once(self) -> None:
        task_id, attempt = self.running()
        self.assertTrue(
            self.store.record_event(
                "e1", task_id, attempt, "worker.progress", "w", {"n": 1}, 1.0
            )
        )
        self.assertFalse(
            self.store.record_event(
                "e1", task_id, attempt, "worker.progress", "w", {"n": 2}, 2.0
            )
        )
        progress = [e for e in self.store.events(task_id) if e["id"] == "e1"]
        self.assertEqual(progress[0]["payload"], {"n": 1})

    def test_oversized_payloads_are_truncated(self) -> None:
        task_id, attempt = self.running()
        self.store.record_event(
            "e1",
            task_id,
            attempt,
            "worker.log",
            "w",
            {"text": "x" * PAYLOAD_LIMIT},
            1.0,
        )
        payload = self.store.events(task_id)[0]["payload"]
        self.assertTrue(payload["truncated"])

    def test_status_notifications_are_acknowledged_once(self) -> None:
        task_id, _ = self.running()
        messages = self.store.pending_messages("task.status")
        self.assertEqual([m.payload["to"] for m in messages], ["claimed"])
        self.store.delivery_failed(messages[0].key, "telegram 502")
        self.assertEqual(self.store.pending_messages("task.status")[0].attempts, 1)
        self.store.acknowledge(messages[0].key, 2.0)
        self.store.acknowledge(messages[0].key, 3.0)
        self.assertEqual(self.store.pending_messages("task.status"), [])
        self.store.transition(task_id, "validating", "w", "done", 4.0)
        self.assertEqual(self.store.pending_messages("task.status"), [])


class StorageTests(StoreCase):
    def mounts(self, kind: str) -> Path:
        mounts = Path(self.tmp.name) / "mounts"
        mounts.write_text(
            "/dev/nvme0n1p2 / ext4 rw 0 0\n"
            f"server:/export {self.tmp.name} {kind} rw 0 0\n"
        )
        return mounts

    def test_network_filesystems_are_refused(self) -> None:
        for kind in ("nfs4", "cifs", "fuse.sshfs"):
            with self.assertRaises(OrchestratorError) as raised:
                Store(self.path, POLICY, mounts=self.mounts(kind))
            self.assertEqual(raised.exception.code, "STORAGE")
        Store(self.path, POLICY, mounts=self.mounts("ext4")).close()

    def test_filesystem_type_uses_the_deepest_mount(self) -> None:
        mounts = self.mounts("nfs4")
        self.assertEqual(filesystem_type(Path(self.tmp.name) / "x", mounts), "nfs4")
        self.assertEqual(filesystem_type(Path("/srv"), mounts), "ext4")

    def test_newer_schema_is_refused(self) -> None:
        self.store.db.execute("PRAGMA user_version=99")
        self.store.close()
        with self.assertRaises(OrchestratorError) as raised:
            self.store = Store(self.path, POLICY, controller=True)
        self.assertEqual(raised.exception.code, "SCHEMA")
        self.store = Store(
            Path(self.tmp.name) / "other.sqlite", POLICY, controller=True
        )

    def test_migration_snapshots_then_upgrades(self) -> None:
        task, _ = self.store.create_task("rupan", "k1", spec(), 1.0)
        self.store.close()
        upgrade = "ALTER TABLE tasks ADD COLUMN note TEXT;"
        with (
            mock.patch.object(core, "MIGRATIONS", (*core.MIGRATIONS, upgrade)),
            mock.patch.object(core, "SCHEMA_VERSION", 2),
        ):
            self.store = Store(self.path, POLICY, controller=True)
            self.assertEqual(self.store.schema_version(), 2)
        (snapshot,) = self.path.parent.glob(self.path.name + ".pre-v2-*.sqlite")
        old = sqlite3.connect(snapshot)
        self.assertEqual(old.execute("PRAGMA user_version").fetchone()[0], 1)
        old.close()
        self.assertEqual(self.store.get_task(task.id).title, spec().title)
        columns = {row[1] for row in self.store.db.execute("PRAGMA table_info(tasks)")}
        self.assertIn("note", columns)

    def test_only_the_controller_upgrades_a_store(self) -> None:
        self.store.close()
        upgrade = "ALTER TABLE tasks ADD COLUMN note TEXT;"
        with (
            mock.patch.object(core, "MIGRATIONS", (*core.MIGRATIONS, upgrade)),
            mock.patch.object(core, "SCHEMA_VERSION", 2),
        ):
            with self.assertRaises(OrchestratorError) as raised:
                Store(self.path, POLICY)
            self.assertEqual(raised.exception.code, "SCHEMA")
            self.store = Store(self.path, POLICY, controller=True)
            self.assertEqual(self.store.schema_version(), 2)

    def test_failed_migration_leaves_the_store_unchanged(self) -> None:
        task, _ = self.store.create_task("rupan", "k1", spec(), 1.0)
        self.store.close()
        broken = "ALTER TABLE tasks ADD COLUMN note TEXT; SELECT * FROM missing;"
        with (
            mock.patch.object(core, "MIGRATIONS", (*core.MIGRATIONS, broken)),
            mock.patch.object(core, "SCHEMA_VERSION", 2),
            self.assertRaises(sqlite3.OperationalError),
        ):
            Store(self.path, POLICY, controller=True)
        self.store = Store(self.path, POLICY, controller=True)
        self.assertEqual(self.store.schema_version(), 1)
        columns = {row[1] for row in self.store.db.execute("PRAGMA table_info(tasks)")}
        self.assertNotIn("note", columns)
        self.assertEqual(self.store.get_task(task.id).id, task.id)

    def test_backup_is_private_consistent_and_never_overwrites(self) -> None:
        task, _ = self.store.create_task("rupan", "k1", spec(), 1.0)
        destination = Path(self.tmp.name) / "backup.sqlite"
        self.store.backup(destination)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
        with self.assertRaises(OrchestratorError) as raised:
            self.store.backup(destination)
        self.assertEqual(raised.exception.code, "EXISTS")
        restored = Store(destination, POLICY)
        self.assertEqual(restored.get_task(task.id).title, spec().title)
        restored.close()

    def test_restart_recovers_work_from_a_crashed_controller(self) -> None:
        task, _ = self.store.create_task("rupan", "k1", spec(), 0.0)
        claimed = self.store.claim("dead-controller", "p", "m", "pi", 60, 0.0)
        assert claimed is not None
        self.store.close()
        self.store = Store(self.path, POLICY, controller=True)
        self.assertEqual(self.store.reap(61.0), [claimed[1].id])
        self.assertEqual(self.store.get_task(task.id).status, "ready")
        again = self.store.claim("new-controller", "p", "m", "pi", 60, 62.0)
        assert again is not None
        self.assertEqual(again[1].number, 2)

    def test_cli_reports_check_and_backup_as_json(self) -> None:
        self.store.create_task("rupan", "k1", spec(), 1.0)
        destination = Path(self.tmp.name) / "cli-backup.sqlite"
        for argv in (
            ["check", "--db", str(self.path)],
            ["backup", "--db", str(self.path), str(destination)],
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(argv), 0)
            result = json.loads(output.getvalue())
            self.assertTrue(result["ok"])
            self.assertEqual(result["schema_version"], core.SCHEMA_VERSION)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            missing = Path(self.tmp.name) / "missing.sqlite"
            self.assertEqual(cli.main(["check", "--db", str(missing)]), 1)
        self.assertEqual(json.loads(output.getvalue())["code"], "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
