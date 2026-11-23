"""Weekly Bookshelf maintenance: rescan, clear imported downloads, search.

Runs the shipped script (rendered bookshelf-missing-search ConfigMap) against
a stub of the Bookshelf API.
"""

import unittest

from tests.gitops_render import config_map, rendered

SOURCE = config_map("media", "bookshelf-missing-search")["data"]["weekly.py"]


def load():
    ns: dict = {"__name__": "weekly_under_test"}
    exec(compile(SOURCE, "weekly.py", "exec"), ns)
    return ns


class StubBookshelf:
    def __init__(self, queue, files, statuses=("completed",)):
        self.queue = queue
        self.files = files
        self.statuses = list(statuses)
        self.calls = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "POST" and path == "/command":
            return {"id": 7}
        if path == "/command/7":
            return {"status": self.statuses.pop(0)}
        if path.startswith("/queue?"):
            return {"records": self.queue}
        if path.startswith("/book/"):
            book_id = int(path.rsplit("/", 1)[1])
            return {"statistics": {"bookFileCount": self.files.get(book_id, 0)}}
        if method == "DELETE":
            return None
        raise AssertionError(f"unexpected call {method} {path}")


class WeeklyRun(unittest.TestCase):
    def run_main(self, stub):
        mod = load()
        mod["time"].sleep = lambda _s: None
        return mod["main"](stub)

    def test_rescans_clears_imported_then_searches(self):
        stub = StubBookshelf(
            queue=[
                {"id": 1, "bookId": 10, "status": "completed", "title": "Matter"},
                {"id": 2, "bookId": 11, "status": "completed", "title": "Nemesis"},
                {"id": 3, "bookId": 12, "status": "downloading", "title": "Foundation"},
            ],
            files={10: 1},
        )
        self.assertEqual(self.run_main(stub), 0)
        commands = [b["name"] for m, p, b in stub.calls if p == "/command"]
        self.assertEqual(commands, ["RescanFolders", "MissingBookSearch"])
        deletes = [p for m, p, _ in stub.calls if m == "DELETE"]
        self.assertEqual(deletes, ["/queue/1?removeFromClient=false&blocklist=false"])
        rescan_done = stub.calls.index(("GET", "/command/7", None))
        first_queue = next(
            i for i, c in enumerate(stub.calls) if c[1].startswith("/queue?")
        )
        self.assertLess(rescan_done, first_queue)

    def test_waits_for_rescan(self):
        stub = StubBookshelf(
            queue=[], files={}, statuses=("queued", "started", "completed")
        )
        self.assertEqual(self.run_main(stub), 0)
        polls = [c for c in stub.calls if c[1] == "/command/7"]
        self.assertEqual(len(polls), 3)

    def test_failed_rescan_stops_before_search(self):
        stub = StubBookshelf(queue=[], files={}, statuses=("failed",))
        self.assertEqual(self.run_main(stub), 1)
        commands = [b["name"] for m, p, b in stub.calls if p == "/command"]
        self.assertEqual(commands, ["RescanFolders"])

    def test_missing_key_fails(self):
        mod = load()
        mod["os"].environ.pop("BOOKSHELF_API_KEY", None)
        self.assertEqual(mod["main"](), 1)


class Wiring(unittest.TestCase):
    def cronjob(self) -> dict:
        return next(
            d
            for d in rendered("media")
            if d["kind"] == "CronJob"
            and d["metadata"]["name"] == "bookshelf-missing-search"
        )

    def test_schedule_and_script(self):
        cj = self.cronjob()
        self.assertEqual(cj["spec"]["schedule"], "47 3 * * 0")
        self.assertEqual(cj["spec"]["concurrencyPolicy"], "Forbid")
        container = cj["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][
            0
        ]
        self.assertEqual(container["command"], ["python3", "/app/weekly.py"])

    def test_reads_only_the_bookshelf_key(self):
        container = self.cronjob()["spec"]["jobTemplate"]["spec"]["template"]["spec"][
            "containers"
        ][0]
        self.assertNotIn("envFrom", container)
        self.assertEqual(
            [e["valueFrom"]["secretKeyRef"] for e in container["env"]],
            [{"name": "media-app", "key": "BOOKSHELF_API_KEY"}],
        )


if __name__ == "__main__":
    unittest.main()
