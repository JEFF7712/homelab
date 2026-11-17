from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

APP = Path(__file__).resolve().parents[1] / "gitops/outreach/app"
sys.path.insert(0, str(APP))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


with patch.dict(os.environ, {"OUTREACH_DEPS": str(APP.parent / "vendor")}):
    runner = load("outreach_runner", APP / "runner.py")
client = load("outreach_client", APP / "client.py")


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "server/outreach"
        self.master = self.state / "outputs/2026-11-13-master/outreach_master.xlsx"
        self.queue = self.state / "queue/queue.json"
        for target, name, value in (
            (runner, "STATE", self.state),
            (runner.engine, "MASTER", self.master),
            (runner.engine, "QUEUE", self.queue),
            (runner.engine, "OUTREACH", self.state),
            (client, "ROOT", self.root / "laptop"),
            (client, "MASTER", self.root / "laptop/master.xlsx"),
            (client, "RECEIPT", self.root / "laptop/queue/remote-state.json"),
        ):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        book = runner.engine.openpyxl.Workbook()
        book.active.append(["Company", "Status"])
        output = io.BytesIO()
        book.save(output)
        self.book = output.getvalue()
        self.request = {
            "version": 1,
            "args": ["bootstrap"],
            "bootstrap": {
                "master": runner.encode(self.book),
                "queue": runner.encode(b'{"items":[]}'),
            },
        }
        self.invoke = patch.object(runner, "invoke", Mock(return_value=0))
        self.invoke.start()
        self.addCleanup(self.invoke.stop)

    def test_bootstrap_is_one_time_and_snapshot_is_restorable(self):
        response = runner.rpc(self.request)
        self.assertEqual(base64.b64decode(response["master"]), self.book)
        with tarfile.open(self.state.parent / "snapshots/latest.tar") as archive:
            content = archive.extractfile("queue/queue.json")
            assert content is not None
            self.assertEqual(json.load(content), {"items": []})
            self.assertNotIn("gmail.env", archive.getnames())
        with self.assertRaisesRegex(ValueError, "already initialized"):
            runner.rpc(self.request)

    def test_stale_tracker_update_is_rejected(self):
        runner.rpc(self.request)
        original = self.master.read_bytes()
        with self.assertRaisesRegex(ValueError, "tracker conflict"):
            runner.rpc(
                {
                    "version": 1,
                    "args": ["status"],
                    "master_update": {
                        "base_sha256": "stale",
                        "data": runner.encode(self.book),
                    },
                }
            )
        self.assertEqual(self.master.read_bytes(), original)

    def test_local_tracker_edits_use_compare_and_swap(self):
        runner.rpc(self.request)
        book = runner.engine.openpyxl.load_workbook(io.BytesIO(self.book))
        book.active.append(["Edited locally", "Waiting"])
        output = io.BytesIO()
        book.save(output)
        edited = output.getvalue()
        result = runner.rpc(
            {
                "version": 1,
                "args": ["sync"],
                "master_update": {
                    "base_sha256": hashlib.sha256(self.book).hexdigest(),
                    "data": runner.encode(edited),
                },
            }
        )
        self.assertEqual(result["master_sha256"], hashlib.sha256(edited).hexdigest())
        self.assertEqual(self.master.read_bytes(), edited)

    def test_rpc_does_not_allow_live_sends(self):
        with self.assertRaisesRegex(ValueError, "only in the homelab"):
            runner.rpc({"version": 1, "args": ["send-due", "--force"]})
        runner.invoke.assert_not_called()

    def test_batch_spec_is_materialized_on_server(self):
        runner.rpc(self.request)
        spec = {"batch": "test", "emails": []}

        def inspect(arguments):
            self.assertEqual(arguments[0], "lint")
            self.assertEqual(json.loads(Path(arguments[1]).read_text()), spec)
            return 1

        runner.invoke.side_effect = inspect
        result = runner.rpc(
            {"version": 1, "args": ["lint", "/laptop/batch.json"], "spec": spec}
        )
        self.assertEqual(result["code"], 1)
        self.assertIn("master", result)

    def test_client_pull_preserves_local_workbook(self):
        response = runner.rpc(self.request)
        client.atomic_write(client.MASTER, b"local edits to preserve")
        with patch.object(client, "transport", return_value=response):
            args = client.parser().parse_args(["sync", "--pull"])
            self.assertEqual(client.run(args, ["sync", "--pull"]), 0)
        backups = list((client.ROOT / "queue/local-tracker-backups").glob("*.xlsx"))
        self.assertEqual(backups[0].read_bytes(), b"local edits to preserve")
        self.assertEqual(client.MASTER.read_bytes(), self.book)

    def test_client_forwards_spec_and_resumes_without_laptop_paths(self):
        response = runner.rpc(self.request)
        client.atomic_write(
            client.RECEIPT,
            json.dumps({"master_sha256": response["master_sha256"]}).encode(),
        )
        client.atomic_write(client.MASTER, self.book)
        spec = self.root / "batch.json"
        spec.write_text(
            json.dumps({"batch": "test", "emails": [{"resume": "scientificML"}]})
        )
        with patch.object(
            client, "resume_payload", return_value={"scientificML": "PDF bytes"}
        ) as pdfs:
            with patch.object(client, "transport", return_value=response) as transport:
                args = client.parser().parse_args(["queue", str(spec)])
                self.assertEqual(client.run(args, ["queue", str(spec)]), 0)
                request = transport.call_args.args[0]
                self.assertEqual(request["spec"]["batch"], "test")
                self.assertEqual(request["resumes"], {"scientificML": "PDF bytes"})
                pdfs.assert_called_once_with({"scientificML"})

    def test_client_never_falls_back_when_homelab_unavailable(self):
        with patch.object(client, "CONFIG", self.root / "missing-config"):
            with self.assertRaisesRegex(ValueError, "not configured"):
                client.transport({"version": 1, "args": ["queue"]})

    def test_render_reports_only_the_created_laptop_path(self):
        response = runner.rpc(self.request)
        response.update(
            stdout="/tmp/server-preview/example.eml\n",
            artifacts={
                "example.eml": runner.encode(b"Subject: Preview\n\nExisting draft")
            },
        )
        client.atomic_write(client.MASTER, self.book)
        client.atomic_write(
            client.RECEIPT,
            json.dumps({"master_sha256": response["master_sha256"]}).encode(),
        )
        spec = self.root / "batch.json"
        spec.write_text(json.dumps({"batch": "test", "emails": []}))
        output = self.root / "laptop-previews"
        args = client.parser().parse_args(["render", str(spec), "--out", str(output)])
        with patch.object(client, "transport", return_value=response):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                self.assertEqual(
                    client.run(args, ["render", str(spec), "--out", str(output)]), 0
                )
                reported = Path(stdout.getvalue().strip())
        self.assertEqual(reported, output / "example.eml")
        self.assertEqual(reported.read_bytes(), b"Subject: Preview\n\nExisting draft")


if __name__ == "__main__":
    unittest.main()
