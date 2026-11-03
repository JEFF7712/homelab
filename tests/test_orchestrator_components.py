import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from scripts.orchestrator.controller import Controller, ControllerConfig
from scripts.orchestrator.core import Message, Store, Task, TaskSpec
from scripts.orchestrator.daemon import build
from scripts.orchestrator.forge import CommitStatus, PullRequest, parse_commands
from scripts.orchestrator.jobs import (
    JobSpec,
    KubernetesJobConfig,
    KubernetesJobManager,
    job_name,
    render_job,
)
from scripts.orchestrator.ntfy import render
from scripts.orchestrator.verifier import VerifierConfig, verify
from tests.orchestrator_fakes import CI_CONTEXT, FakeForgejo, bare_repository

SHA = "a" * 40


class CommandTests(unittest.TestCase):
    def test_commands_are_parsed_strictly(self) -> None:
        commands = parse_commands(
            "thanks\n/answer q_1 2 use oauth please\n/approve apr_9\n/decline\n"
            "/answer q_2 two bad\n/cancel because\nnot /cancel"
        )
        self.assertEqual(
            [(c.verb, c.target, c.revision, c.text) for c in commands],
            [
                ("answer", "q_1", 2, "use oauth please"),
                ("approve", "apr_9", None, ""),
                ("cancel", "", None, "because"),
            ],
        )


class FakeForge:
    def __init__(
        self, state: str, contexts: dict[str, str], approvers: set[str]
    ) -> None:
        self.state = state
        self.contexts = contexts
        self.approvers = approvers
        self.approval_sha: str | None = None

    def status(self, sha: str, creators: frozenset[str]) -> CommitStatus:
        self.creators = creators
        return CommitStatus(self.state, self.contexts)

    def approvals(self, number: int, sha: str) -> set[str]:
        self.approval_sha = sha
        return self.approvers


def task(criteria: tuple[str, ...]) -> Task:
    return Task(
        "task_1",
        "rupan",
        "p",
        "t",
        "g",
        criteria,
        (),
        0,
        None,
        "validating",
        "1",
        "v",
        0,
        0,
    )


def pull(state: str = "open", merged: bool = False) -> PullRequest:
    return PullRequest(7, "agent/task_1/1", SHA, state, merged, "")


class VerifierTests(unittest.TestCase):
    config = VerifierConfig(
        required_contexts=(CI_CONTEXT,),
        reviewers={"rupan": "rupan-forge"},
        ci_logins=frozenset({"woodpecker"}),
    )

    def test_nothing_passes_without_a_trusted_ci_login(self) -> None:
        verdict = verify(
            task((f"ci:{CI_CONTEXT}",)),
            pull(),
            FakeForge("success", {CI_CONTEXT: "success"}, set()),  # type: ignore[arg-type]
            VerifierConfig((CI_CONTEXT,), {}),
        )
        self.assertEqual(verdict.state, "pending")

    def test_ci_states(self) -> None:
        for state, contexts, expected in (
            ("pending", {}, "pending"),
            ("failure", {CI_CONTEXT: "failure"}, "failed"),
            ("success", {CI_CONTEXT: "success"}, "passed"),
        ):
            verdict = verify(
                task((f"ci:{CI_CONTEXT}",)),
                pull(),
                FakeForge(state, contexts, set()),
                self.config,
            )  # type: ignore[arg-type]
            self.assertEqual(verdict.state, expected, state)

    def test_free_text_criteria_need_the_principals_approval_of_this_head(self) -> None:
        criteria = (f"ci:{CI_CONTEXT}", "README documents the flag")
        forge = FakeForge("success", {CI_CONTEXT: "success"}, {"someone-else"})
        self.assertEqual(
            verify(task(criteria), pull(), forge, self.config).state, "pending"
        )  # type: ignore[arg-type]
        forge.approvers = {"rupan-forge"}
        verdict = verify(task(criteria), pull(), forge, self.config)  # type: ignore[arg-type]
        self.assertEqual(verdict.state, "passed")
        self.assertEqual(forge.approval_sha, SHA)
        assert verdict.evidence is not None
        self.assertEqual(verdict.evidence.criteria_met, criteria)

    def test_closed_unmerged_pull_request_fails(self) -> None:
        forge = FakeForge("success", {CI_CONTEXT: "success"}, set())
        self.assertEqual(
            verify(task(("c",)), pull("closed"), forge, self.config).state, "failed"
        )  # type: ignore[arg-type]


class NoticeTests(unittest.TestCase):
    def test_questions_carry_the_reply_command(self) -> None:
        notice = render(
            Message(
                "k",
                "question.open",
                "task_1",
                {
                    "question_id": "q_1",
                    "revision": 3,
                    "prompt": "local or oauth?",
                    "choices": ["local", "oauth"],
                    "blocking": True,
                },
                0,
            ),
            "https://forge/issues/1",
        )
        assert notice is not None
        self.assertIn("/answer q_1 3 <answer>", notice.body)
        self.assertEqual(notice.priority, "high")

    def test_dispatch_is_not_a_notification(self) -> None:
        message = Message("k", "task.status", "task_1", {"to": "claimed"}, 0)
        self.assertIsNone(render(message, None))


SPEC = JobSpec(
    attempt_id="att_ABC",
    task_id="task_1",
    api_url="http://controller:8090",
    token="secret-token",
    brief="do it",
    repository_url="https://forge/x.git",
    base_branch="main",
    branch="agent/task_1/1",
    backend={"kind": "pi"},
    commit_message="m",
)


class KubernetesTests(unittest.TestCase):
    config = KubernetesJobConfig(
        namespace="persistent-agent", image="registry/worker@sha256:" + "0" * 64
    )

    def test_job_is_isolated_and_the_token_stays_in_the_secret(self) -> None:
        secret, job = render_job(SPEC, self.config)
        pod = job["spec"]["template"]["spec"]
        container = pod["containers"][0]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertFalse(container["securityContext"]["allowPrivilegeEscalation"])
        self.assertEqual(container["securityContext"]["capabilities"]["drop"], ["ALL"])
        self.assertTrue(container["securityContext"]["runAsNonRoot"])
        self.assertNotIn("hostPath", json.dumps(pod))
        self.assertNotIn("secret-token", json.dumps(job))
        self.assertIn("secret-token", secret["stringData"]["job.json"])
        self.assertEqual(job["spec"]["backoffLimit"], 0)
        self.assertEqual(job_name("att_ABC"), "orc-att-abc")

    def test_manager_creates_reads_and_deletes_through_the_api(self) -> None:
        calls: list[tuple[str, str]] = []
        jobs: dict[str, Any] = {}
        secret_status = [201]

        class Api(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def reply(self, status: int, body: Any = None) -> None:
                data = json.dumps(body).encode() if body is not None else b""
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self) -> None:
                calls.append(("POST", self.path))
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                assert self.headers["Authorization"] == "Bearer sa-token"
                if body["kind"] == "Job":
                    body["metadata"]["uid"] = "uid-1"
                    jobs[body["metadata"]["name"]] = {
                        "metadata": body["metadata"],
                        "status": {"active": 1},
                    }
                else:
                    owners = body["metadata"]["ownerReferences"]
                    assert owners[0]["uid"] == "uid-1", owners
                    return self.reply(secret_status[0], body)
                self.reply(201, body)

            def do_GET(self) -> None:
                name = self.path.rsplit("/", 1)[-1]
                self.reply(200, jobs[name]) if name in jobs else self.reply(404)

            def do_DELETE(self) -> None:
                calls.append(("DELETE", self.path.split("?")[0]))
                jobs.pop(self.path.split("?")[0].rsplit("/", 1)[-1], None)
                self.reply(200, {})

        server = ThreadingHTTPServer(("127.0.0.1", 0), Api)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as tmp:
            token = Path(tmp) / "token"
            token.write_text("sa-token")
            manager = KubernetesJobManager(
                self.config,
                api=f"http://127.0.0.1:{server.server_port}",
                token_file=token,
                ca_file=Path(tmp) / "missing-ca",
            )
            self.assertEqual(manager.state("att_ABC"), "missing")
            manager.start(SPEC)
            self.assertEqual(manager.state("att_ABC"), "running")
            manager.stop("att_ABC")
            self.assertEqual(manager.state("att_ABC"), "missing")
            self.assertEqual(
                [c[0] for c in calls], ["POST", "POST", "DELETE", "DELETE"]
            )
            self.assertTrue(calls[0][1].endswith("/jobs"))
            self.assertTrue(calls[1][1].endswith("/secrets"))
            secret_status[0] = 500
            with self.assertRaises(RuntimeError):
                manager.start(SPEC)
            self.assertEqual(manager.state("att_ABC"), "missing")
            self.assertTrue(calls[-1][1].endswith("/jobs/orc-att-abc"))


class FlakySink:
    name = "flaky"
    topics = ("task.status",)

    def __init__(self) -> None:
        self.fail = True
        self.delivered: list[str] = []

    def deliver(
        self, message: Message, task: Task | None, issue_url: str | None
    ) -> None:
        if self.fail:
            raise OSError("ntfy down")
        self.delivered.append(message.key)


class NullJobs:
    def start(self, spec: JobSpec) -> None:
        pass

    def state(self, attempt_id: str) -> str:
        return "running"

    def stop(self, attempt_id: str) -> None:
        pass


class DeliveryTests(unittest.TestCase):
    def test_failed_delivery_is_retried_and_then_sent_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            forge = FakeForgejo(bare_repository(root))
            self.addCleanup(forge.close)
            store = Store(root / "db.sqlite", "v", controller=True)
            self.addCleanup(store.close)
            from scripts.orchestrator.forge import ForgejoForge

            sink = FlakySink()
            controller = Controller(
                store,
                NullJobs(),  # type: ignore[arg-type]
                ForgejoForge(forge.url, "owner", "sandbox", "t"),
                [sink],
                ControllerConfig(
                    owner="c",
                    project="sandbox",
                    api_url="http://x",
                    repository_url="x",
                    base_branch="main",
                    backend={"kind": "pi"},
                    verifier=VerifierConfig((), {}),
                ),
            )
            task, _ = store.create_task(
                "rupan", "k", TaskSpec("sandbox", "t", "g", ("c",)), time.time()
            )
            controller.tick()
            self.assertEqual(sink.delivered, [])
            sink.fail = False
            controller.tick()
            controller.tick()
            self.assertEqual(len(sink.delivered), 1)


class DaemonTests(unittest.TestCase):
    def test_daemon_builds_from_config_and_ticks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            forge = FakeForgejo(bare_repository(root))
            self.addCleanup(forge.close)
            (root / "forge-token").write_text("t\n")
            (root / "client-token").write_text("c\n")
            config = {
                "db": str(root / "db.sqlite"),
                "policy_version": "v1",
                "project": "sandbox",
                "max_running": 1,
                "worker_api_url": "http://127.0.0.1:1",
                "repository_url": str(root / "origin.git"),
                "forge": {
                    "url": forge.url,
                    "owner": "owner",
                    "repo": "sandbox",
                    "token_file": str(root / "forge-token"),
                },
                "backend": {
                    "kind": "pi",
                    "pi": "pi",
                    "provider": "openai",
                    "model": "m",
                },
                "jobs": {"kind": "local", "root": str(root / "jobs")},
                "verifier": {"ci_logins": ["woodpecker"]},
                "clients": [
                    {
                        "name": "letta",
                        "principal": "rupan",
                        "token_file": str(root / "client-token"),
                    }
                ],
                "listen": {"worker": "127.0.0.1:0", "client": "127.0.0.1:0"},
                "tick_seconds": 0.1,
            }
            daemon = build(config)
            stop = threading.Event()
            daemon.store.create_task(
                "rupan", "k", TaskSpec("sandbox", "t", "g", ("c",)), time.time()
            )
            thread = threading.Thread(target=daemon.run, args=(stop,))
            thread.start()
            deadline = time.time() + 10
            while not forge.issues and time.time() < deadline:
                time.sleep(0.05)
            stop.set()
            thread.join(10)
            self.assertEqual(len(forge.issues), 1)
            for attempt_dir in (root / "jobs").glob("att_*"):
                pid = int((attempt_dir / "pid").read_text())
                import os
                import signal

                try:
                    os.killpg(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass


class BrokenJobs:
    """A Kubernetes API that errors on reads and deletes."""

    def __init__(self) -> None:
        self.stops = 0

    def start(self, spec: JobSpec) -> None:
        pass

    def state(self, attempt_id: str) -> str:
        raise RuntimeError("reading Job failed with 500")

    def stop(self, attempt_id: str) -> None:
        self.stops += 1
        raise RuntimeError("deleting Job failed with 500")


class FailureHandlingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.forge = FakeForgejo(bare_repository(root))
        self.addCleanup(self.forge.close)
        self.store = Store(root / "db.sqlite", "v", controller=True)
        self.addCleanup(self.store.close)
        from scripts.orchestrator.forge import ForgejoForge

        self.jobs = BrokenJobs()
        self.controller = Controller(
            self.store,
            self.jobs,  # type: ignore[arg-type]
            ForgejoForge(self.forge.url, "owner", "sandbox", "t"),
            [],
            ControllerConfig(
                owner="c",
                project="sandbox",
                api_url="http://x",
                repository_url="x",
                base_branch="main",
                backend={"kind": "pi"},
                verifier=VerifierConfig((), {}),
            ),
        )

    def test_unknown_job_state_does_not_finish_the_attempt(self) -> None:
        self.store.create_task(
            "rupan", "k", TaskSpec("sandbox", "t", "g", ("c",)), time.time()
        )
        self.controller.tick()
        (attempt,) = self.store.live_attempts()
        self.controller.tick()
        self.assertIsNone(self.store.attempt(attempt.id).finished)
        self.assertEqual(len(self.store.live_attempts()), 1)

    def test_failed_fence_is_retried_not_acknowledged(self) -> None:
        task, _ = self.store.create_task(
            "rupan", "k", TaskSpec("sandbox", "t", "g", ("c",)), time.time()
        )
        self.controller.tick()
        self.store.cancel(task.id, "rupan", "stop", time.time())
        self.controller.tick()
        self.controller.tick()
        self.assertEqual(self.jobs.stops, 2)
        self.assertEqual(len(self.store.pending_messages("worker.cancel")), 1)


class RestartTests(unittest.TestCase):
    def test_a_new_controller_gives_live_workers_a_lease_before_reaping(self) -> None:
        from scripts.orchestrator.forge import ForgejoForge

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = FakeForgejo(bare_repository(root))
            self.addCleanup(fake.close)
            store = Store(root / "db.sqlite", "v", controller=True)
            self.addCleanup(store.close)
            clock = [1000.0]

            def controller() -> Controller:
                return Controller(
                    store,
                    NullJobs(),  # type: ignore[arg-type]
                    ForgejoForge(fake.url, "owner", "sandbox", "t"),
                    [],
                    ControllerConfig(
                        owner="c",
                        project="sandbox",
                        api_url="http://x",
                        repository_url="x",
                        base_branch="main",
                        backend={"kind": "pi"},
                        verifier=VerifierConfig((), {}),
                        lease_seconds=120,
                    ),
                    clock=lambda: clock[0],
                )

            store.create_task("rupan", "k", TaskSpec("sandbox", "t", "g", ("c",)), 1000)
            controller().tick()
            (attempt,) = store.live_attempts()
            clock[0] += 600
            restarted = controller()
            restarted.tick()
            self.assertIsNone(store.attempt(attempt.id).finished)
            clock[0] += 121
            restarted.tick()
            self.assertIsNotNone(store.attempt(attempt.id).finished)


class TerminatingJobs(NullJobs):
    """A Job whose Pods outlive the delete call until `gone` is set."""

    def __init__(self) -> None:
        self.gone = False

    def state(self, attempt_id: str) -> str:
        return "missing" if self.gone else "running"


class FenceAndStartupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.fake = FakeForgejo(bare_repository(root))
        self.addCleanup(self.fake.close)
        self.store = Store(root / "db.sqlite", "v", controller=True)
        self.addCleanup(self.store.close)
        from scripts.orchestrator.forge import ForgejoForge

        self.jobs = TerminatingJobs()
        self.controller = Controller(
            self.store,
            self.jobs,  # type: ignore[arg-type]
            ForgejoForge(self.fake.url, "owner", "sandbox", "t"),
            [],
            ControllerConfig(
                owner="c",
                project="sandbox",
                api_url="http://x",
                repository_url="x",
                base_branch="main",
                backend={"kind": "pi"},
                verifier=VerifierConfig((), {}),
                startup_seconds=600,
            ),
            clock=lambda: 1000.0,
        )

    def test_first_lease_covers_startup(self) -> None:
        self.store.create_task(
            "rupan", "k", TaskSpec("sandbox", "t", "g", ("c",)), 1000.0
        )
        self.controller.tick()
        (attempt,) = self.store.live_attempts()
        self.assertEqual(attempt.lease_expires, 1600.0)

    def test_cancel_is_acknowledged_only_after_the_pods_are_gone(self) -> None:
        task, _ = self.store.create_task(
            "rupan", "k", TaskSpec("sandbox", "t", "g", ("c",)), 1000.0
        )
        self.controller.tick()
        self.store.cancel(task.id, "rupan", "stop", 1000.0)
        self.controller.tick()
        self.assertEqual(len(self.store.pending_messages("worker.cancel")), 1)
        self.jobs.gone = True
        self.controller.tick()
        self.assertEqual(self.store.pending_messages("worker.cancel"), [])


class MarkerTests(unittest.TestCase):
    def test_a_marker_quoted_inside_a_controller_comment_does_not_count(
        self,
    ) -> None:
        from scripts.orchestrator.controller import defang
        from scripts.orchestrator.forge import MESSAGE_MARKER, ForgejoForge

        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeForgejo(bare_repository(Path(tmp)))
            self.addCleanup(fake.close)
            forge = ForgejoForge(fake.url, "owner", "sandbox", "t")
            future = MESSAGE_MARKER.format("question:q2")
            fake.add_comment(
                1,
                "agent-controller",
                f"Worker asks: {future} hi\n\n{MESSAGE_MARKER.format('question:q1')}",
            )
            forge.ensure_comment(1, "question:q2", "the real notice")
            self.assertEqual(len(fake.comments[1]), 2)
            self.assertNotIn("<!--", defang(f"asks {future}"))

    def test_markers_count_only_from_the_controllers_own_login(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeForgejo(bare_repository(Path(tmp)))
            self.addCleanup(fake.close)
            from scripts.orchestrator.forge import MESSAGE_MARKER, ForgejoForge

            forge = ForgejoForge(fake.url, "owner", "sandbox", "t")
            fake.issues.append(
                {
                    "number": 1,
                    "title": "t",
                    "body": "b",
                    "user": {"login": "agent-worker"},
                }
            )
            fake.add_comment(
                1, "agent-worker", f"spoof {MESSAGE_MARKER.format('question:q1')}"
            )
            forge.ensure_comment(1, "question:q1", "the real notice")
            forge.ensure_comment(1, "question:q1", "the real notice")
            own = [
                c for c in fake.comments[1] if c["user"]["login"] == "agent-controller"
            ]
            self.assertEqual(len(own), 1)
            self.assertIsNone(forge.find_issue("task_1"))


class ClientScopeTests(unittest.TestCase):
    def test_other_projects_and_duplicate_names_are_refused(self) -> None:
        from scripts.orchestrator.client_api import Client, ClientApi
        from scripts.orchestrator.core import OrchestratorError

        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "db.sqlite", "v")
            self.addCleanup(store.close)
            with self.assertRaises(ValueError):
                ClientApi(
                    store,
                    "sandbox",
                    {"a": Client("letta", "rupan"), "b": Client("letta", "other")},
                )
            with self.assertRaises(ValueError):
                ClientApi(store, "sandbox", {"a": Client("ntfy", "rupan")})
            api = ClientApi(store, "sandbox", {"a": Client("letta", "rupan")})
            other, _ = store.create_task(
                "rupan", "k", TaskSpec("elsewhere", "t", "g", ("c",)), time.time()
            )
            client = Client("letta", "rupan")
            with self.assertRaises(OrchestratorError):
                api.handle(client, "get_task", {"task_id": other.id})
            self.assertEqual(api.handle(client, "list_tasks", {}), [])


class CommitStatusTests(unittest.TestCase):
    def test_statuses_from_other_accounts_are_ignored(self) -> None:
        from scripts.orchestrator.forge import ForgejoForge

        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeForgejo(bare_repository(Path(tmp)))
            self.addCleanup(fake.close)
            forge = ForgejoForge(fake.url, "owner", "sandbox", "t")
            trusted = frozenset({"woodpecker"})
            fake.post_status(SHA, "agent-worker", "success")
            self.assertEqual(forge.status(SHA, trusted), CommitStatus("pending", {}))
            fake.post_status(SHA, "woodpecker", "pending")
            fake.post_status(SHA, "woodpecker", "failure")
            fake.post_status(SHA, "agent-worker", "success")
            status = forge.status(SHA, trusted)
            self.assertEqual(status.state, "failure")
            self.assertEqual(status.contexts, {CI_CONTEXT: "failure"})

    def test_newest_trusted_status_wins_across_pages(self) -> None:
        from scripts.orchestrator.forge import PAGE, ForgejoForge

        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeForgejo(bare_repository(Path(tmp)))
            self.addCleanup(fake.close)
            forge = ForgejoForge(fake.url, "owner", "sandbox", "t")
            for _ in range(PAGE):
                fake.post_status(SHA, "woodpecker", "failure")
            fake.post_status(SHA, "woodpecker", "success")
            status = forge.status(SHA, frozenset({"woodpecker"}))
            self.assertEqual(status, CommitStatus("success", {CI_CONTEXT: "success"}))


class ReviewTests(unittest.TestCase):
    def test_a_later_review_on_another_page_replaces_an_approval(self) -> None:
        from scripts.orchestrator.forge import PAGE, ForgejoForge

        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeForgejo(bare_repository(Path(tmp)))
            self.addCleanup(fake.close)
            forge = ForgejoForge(fake.url, "owner", "sandbox", "t")

            def review(number: int, login: str, state: str) -> dict[str, Any]:
                return {
                    "id": number,
                    "user": {"login": login},
                    "state": state,
                    "commit_id": SHA,
                }

            fake.reviews[7] = [review(1, "rupan-forge", "APPROVED")] + [
                review(n, "someone", "COMMENT") for n in range(2, PAGE + 1)
            ]
            self.assertEqual(forge.approvals(7, SHA), {"rupan-forge"})
            fake.reviews[7].append(review(PAGE + 1, "rupan-forge", "REQUEST_CHANGES"))
            self.assertEqual(forge.approvals(7, SHA), set())


if __name__ == "__main__":
    unittest.main()
