import asyncio
import json
import socket
import threading
import time
import unittest
from unittest.mock import patch

from scripts.ci import operation, oplock


class LockServerTest(unittest.TestCase):
    """A real lock server on loopback, driven by the operation client."""

    def setUp(self) -> None:
        self.loop = asyncio.new_event_loop()
        started = threading.Event()

        async def start() -> None:
            self.server = await asyncio.start_server(
                oplock.LockServer().handle, "127.0.0.1", 0
            )
            self.address = self.server.sockets[0].getsockname()[:2]
            started.set()

        self.thread = threading.Thread(
            target=lambda: (
                self.loop.run_until_complete(start()),
                self.loop.run_forever(),
            ),
            daemon=True,
        )
        self.thread.start()
        started.wait(5)

    def tearDown(self) -> None:
        async def stop() -> None:
            self.server.close()
            tasks = [
                task
                for task in asyncio.all_tasks()
                if task is not asyncio.current_task()
            ]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        asyncio.run_coroutine_threadsafe(stop(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()

    def hold_in_thread(
        self, group: str, holder: str, events: list[str], release: threading.Event
    ):
        def run() -> None:
            with oplock.held(group, holder, address=self.address, log=lambda _: None):
                events.append(f"{holder} in")
                release.wait(5)
                events.append(f"{holder} out")

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    def wait_for(self, predicate, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("condition not reached")
            time.sleep(0.01)

    def status(self) -> dict:
        with socket.create_connection(self.address, timeout=5) as sock:
            sock.sendall(b'{"status": true}\n')
            return json.loads(sock.makefile().readline())

    def test_one_holder_per_group_in_arrival_order(self) -> None:
        # Regression (2026-11-10): three deploy-fleet runs of one commit
        # switched hosts at the same time.
        events: list[str] = []
        gates = [threading.Event() for _ in range(3)]
        threads = []
        for index, gate in enumerate(gates):
            threads.append(
                self.hold_in_thread("homelab-fleet-deploy", f"run{index}", events, gate)
            )
            self.wait_for(lambda index=index: len(self.status()["holders"]) == 1)
            time.sleep(0.05)
        for gate in gates:
            gate.set()
        for thread in threads:
            thread.join(5)
        self.assertEqual(
            events,
            ["run0 in", "run0 out", "run1 in", "run1 out", "run2 in", "run2 out"],
        )

    def test_other_groups_run_in_parallel(self) -> None:
        events: list[str] = []
        release = threading.Event()
        first = self.hold_in_thread("homelab-fleet-deploy", "fleet", events, release)
        second = self.hold_in_thread("home-assistant", "ha", events, release)
        self.wait_for(lambda: len(events) == 2)
        self.assertEqual(sorted(events), ["fleet in", "ha in"])
        release.set()
        first.join(5)
        second.join(5)

    def test_a_dead_holder_releases_at_once(self) -> None:
        # A job killed mid-run closes its connection; nobody waits for a
        # stale lock to expire.
        sock = socket.create_connection(self.address, timeout=5)
        sock.sendall(b'{"group": "homelab-fleet-deploy", "holder": "killed job"}\n')
        self.assertEqual(json.loads(sock.makefile().readline()), {"granted": True})
        events: list[str] = []
        release = threading.Event()
        waiter = self.hold_in_thread("homelab-fleet-deploy", "next", events, release)
        time.sleep(0.2)
        self.assertEqual(events, [])
        sock.close()
        self.wait_for(lambda: events == ["next in"])
        release.set()
        waiter.join(5)

    def test_a_waiter_that_leaves_loses_its_place(self) -> None:
        holder = socket.create_connection(self.address, timeout=5)
        holder.sendall(b'{"group": "g", "holder": "first"}\n')
        holder.makefile().readline()
        quitter = socket.create_connection(self.address, timeout=5)
        quitter.sendall(b'{"group": "g", "holder": "quitter"}\n')
        self.assertIn("waiting", json.loads(quitter.makefile().readline()))
        quitter.close()
        events: list[str] = []
        release = threading.Event()
        last = self.hold_in_thread("g", "last", events, release)
        time.sleep(0.1)
        holder.close()
        self.wait_for(lambda: events == ["last in"])
        release.set()
        last.join(5)
        self.wait_for(lambda: self.status() == {"holders": {}})

    def test_waiting_gives_up_after_the_limit(self) -> None:
        holder = socket.create_connection(self.address, timeout=5)
        holder.sendall(b'{"group": "g", "holder": "first"}\n')
        holder.makefile().readline()
        with self.assertRaisesRegex(oplock.LockError, "waited"):
            with oplock.held(
                "g", "second", address=self.address, wait_seconds=1, log=lambda _: None
            ):
                self.fail("entered while the lock was held")
        holder.close()

    def test_invalid_group_is_refused(self) -> None:
        with self.assertRaisesRegex(oplock.LockError, "invalid group"):
            with oplock.held("../etc", "x", address=self.address, log=lambda _: None):
                self.fail("entered with an invalid group")


class ClientTest(unittest.TestCase):
    def test_unreachable_service_runs_unserialized_with_a_warning(self) -> None:
        # A broken lock service must not block deploy-ci-host, which deploys
        # its fix.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            address = probe.getsockname()[:2]
        messages: list[str] = []
        ran = False
        with oplock.held("g", "x", address=address, log=messages.append):
            ran = True
        self.assertTrue(ran)
        self.assertIn("unreachable", messages[0])

    def test_only_mutations_take_their_group_lock(self) -> None:
        with patch.object(oplock, "held") as held:
            operation.group_lock(
                "opnsense-plan", {"mutation": False, "group": "opnsense"}
            )
            held.assert_not_called()
            operation.group_lock(
                "opnsense-apply", {"mutation": True, "group": "opnsense"}
            )
            held.assert_called_once()
            self.assertEqual(held.call_args.args[0], "opnsense")


if __name__ == "__main__":
    unittest.main()
