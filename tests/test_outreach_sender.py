import datetime as dt
import importlib.machinery
import importlib.util
import json
import multiprocessing
import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).resolve().parents[1] / "gitops/outreach/app/outreach.py"
sys.path[:0] = [str(path) for path in (SCRIPT.parent.parent / "vendor").glob("*.whl")]
loader = importlib.machinery.SourceFileLoader("outreach", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None
outreach = importlib.util.module_from_spec(spec)
loader.exec_module(outreach)


class SenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.queue = Path(self.temp.name) / "queue.json"
        self.fixed_now = dt.datetime(2026, 11, 17, 10, 30, tzinfo=outreach.TZ)
        self.item = {
            "id": "<draft@example.org>",
            "company": "Test Company",
            "key": "example.org",
            "batch": "test",
            "to": "person@example.org",
            "resume": None,
            "state": "queued",
            "send_at": self.fixed_now.isoformat(),
            "master": "synced",
        }
        self.queue.write_text(
            json.dumps(
                {
                    "items": [self.item],
                    "last_bounce_scan": self.fixed_now.isoformat(),
                }
            )
        )
        msg = EmailMessage()
        msg["To"] = self.item["to"]
        msg["From"] = "sender@example.net"
        msg["Message-ID"] = self.item["id"]
        msg.set_content("Test message")
        self.mailbox = Mock()
        self.mailbox.find_draft.return_value = b"123"
        self.mailbox.fetch.return_value = msg.as_bytes()
        self.mailbox.sent_time.return_value = None
        for name, value in (
            ("QUEUE", self.queue),
            ("now", Mock(return_value=self.fixed_now)),
            ("Mailbox", Mock(return_value=self.mailbox)),
            ("master_update", Mock()),
            ("notify", Mock()),
            ("smtp_send", Mock()),
        ):
            p = patch.object(outreach, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.args = SimpleNamespace(dry_run=False, force=False, scan_bounces=False)

    def stored(self):
        return outreach.read_queue()["items"][0]

    def run_sender(self):
        outreach.cmd_send_due(self.args)

    def test_success_is_durable_before_smtp_and_cleanup(self):
        def submit(msg):
            item = self.stored()
            self.assertEqual(item["state"], "sending")
            self.assertEqual(item["sent_message_id"], msg["Message-ID"])
            self.assertEqual(item["attempt_to"], self.item["to"])

        def cleanup(uid):
            item = self.stored()
            self.assertEqual(item["state"], "sent")
            self.assertEqual(
                item["master"]["pending_ops"][-1]["fields"]["Status"], "Waiting"
            )

        outreach.smtp_send.side_effect = submit
        self.mailbox.delete_draft.side_effect = cleanup
        self.run_sender()
        self.assertEqual(self.stored()["state"], "sent")
        self.run_sender()
        outreach.smtp_send.assert_called_once()

    def test_crash_after_acceptance_recovers_without_resending(self):
        def accepted_then_crashed(msg):
            self.mailbox.sent_time.return_value = self.fixed_now
            raise SystemExit("process died after SMTP acceptance")

        outreach.smtp_send.side_effect = accepted_then_crashed
        with self.assertRaises(SystemExit):
            self.run_sender()
        self.assertEqual(self.stored()["state"], "sending")
        stable_id = self.stored()["sent_message_id"]
        self.run_sender()
        self.assertEqual(self.stored()["state"], "sent")
        self.mailbox.sent_time.assert_called_with(stable_id)
        outreach.smtp_send.assert_called_once()
        self.mailbox.delete_draft.assert_not_called()

    def test_crash_before_submission_is_held_without_resending(self):
        outreach.smtp_send.side_effect = SystemExit("crash before submission")
        with self.assertRaises(SystemExit):
            self.run_sender()
        self.run_sender()
        self.assertEqual(self.stored()["state"], "uncertain")
        outreach.smtp_send.assert_called_once()

    def test_timeout_is_held_and_later_reconciled(self):
        outreach.smtp_send.side_effect = TimeoutError("lost SMTP response")
        self.run_sender()
        self.assertEqual(self.stored()["state"], "uncertain")
        self.run_sender()
        self.assertEqual(self.stored()["state"], "uncertain")
        self.mailbox.sent_time.return_value = self.fixed_now
        self.run_sender()
        self.assertEqual(self.stored()["state"], "sent")
        outreach.smtp_send.assert_called_once()

    def test_cleanup_failure_never_requeues(self):
        self.mailbox.delete_draft.side_effect = OSError("IMAP disconnected")
        self.run_sender()
        self.assertEqual(self.stored()["state"], "sent")
        self.assertIn("cleanup failed", self.stored()["note"])
        self.run_sender()
        outreach.smtp_send.assert_called_once()

    def test_crash_after_success_record_never_resends(self):
        self.mailbox.delete_draft.side_effect = SystemExit(
            "process died during cleanup"
        )
        with self.assertRaises(SystemExit):
            self.run_sender()
        self.assertEqual(self.stored()["state"], "sent")
        self.run_sender()
        self.assertEqual(self.stored()["master"], "synced")
        outreach.smtp_send.assert_called_once()

    def test_failed_durable_write_prevents_submission(self):
        with patch.object(
            outreach.os, "fsync", side_effect=OSError("disk unavailable")
        ):
            with self.assertRaises(OSError):
                self.run_sender()
        self.assertEqual(self.stored()["state"], "queued")
        outreach.smtp_send.assert_not_called()
        self.mailbox.delete_draft.assert_not_called()

    def test_master_failure_preserves_pending_update(self):
        outreach.master_update.side_effect = OSError("workbook unavailable")
        with self.assertRaises(OSError):
            self.run_sender()
        self.assertEqual(self.stored()["state"], "sent")
        self.assertIsInstance(self.stored()["master"], dict)
        outreach.master_update.side_effect = None
        self.run_sender()
        self.assertEqual(self.stored()["master"], "synced")
        outreach.smtp_send.assert_called_once()

    def test_uncertain_cannot_be_rescheduled_or_cancelled(self):
        outreach.smtp_send.side_effect = TimeoutError()
        self.run_sender()
        args = SimpleNamespace(target="test", at="2026-11-18T10:30", spread_minutes=0)
        for command in (outreach.cmd_reschedule, outreach.cmd_cancel):
            with self.assertRaises(SystemExit):
                command(args)
        self.assertEqual(self.stored()["state"], "uncertain")

    def test_reconciliation_search_failure_never_resends(self):
        outreach.smtp_send.side_effect = TimeoutError()
        self.run_sender()
        self.mailbox.sent_time.side_effect = OSError("Sent unavailable")
        with self.assertRaises(OSError):
            self.run_sender()
        self.assertEqual(self.stored()["state"], "uncertain")
        outreach.smtp_send.assert_called_once()

    def test_dry_run_neither_sends_nor_changes_queue(self):
        self.args.dry_run = True
        before = self.queue.read_bytes()
        self.run_sender()
        self.assertEqual(self.queue.read_bytes(), before)
        outreach.smtp_send.assert_not_called()
        self.mailbox.delete_draft.assert_not_called()

    def test_overlapping_senders_submit_once(self):
        ctx = multiprocessing.get_context("fork")
        submitting, release, second_started = ctx.Event(), ctx.Event(), ctx.Event()
        receipts = Path(self.temp.name) / "submissions"

        def submit(msg):
            with receipts.open("a") as output:
                output.write(str(msg["Message-ID"]) + "\n")
            submitting.set()
            if not release.wait(5):
                raise RuntimeError("test timed out")

        def second_run():
            second_started.set()
            self.run_sender()

        outreach.smtp_send.side_effect = submit
        first = ctx.Process(target=self.run_sender)
        second = ctx.Process(target=second_run)
        try:
            first.start()
            self.assertTrue(submitting.wait(5))
            second.start()
            self.assertTrue(second_started.wait(5))
            release.set()
            first.join(5)
            second.join(5)
            self.assertEqual(first.exitcode, 0)
            self.assertEqual(second.exitcode, 0)
            self.assertEqual(len(receipts.read_text().splitlines()), 1)
        finally:
            release.set()
            for process in (first, second):
                if process.is_alive():
                    process.terminate()
                    process.join()


class SentLookupTests(unittest.TestCase):
    def test_exact_id_and_sent_folder_required(self):
        mailbox = outreach.Mailbox.__new__(outreach.Mailbox)
        mailbox.special = {"Sent": "Sent Mail"}
        mailbox.imap = Mock()
        mailbox.imap.select.return_value = ("OK", [])
        mailbox.imap.uid.return_value = ("OK", [b"123"])
        msg = EmailMessage()
        msg["Message-ID"] = "<delivery@example.net>"
        msg["Date"] = "Tue, 17 Nov 2026 10:30:00 -0600"
        with patch.object(mailbox, "fetch", return_value=msg.as_bytes()):
            self.assertIsNotNone(mailbox.sent_time("<delivery@example.net>"))
            mailbox.imap.select.assert_called_with('"Sent Mail"')
            with self.assertRaises(RuntimeError):
                mailbox.sent_time("<other@example.net>")
        mailbox.imap.uid.return_value = ("NO", [b"unavailable"])
        with self.assertRaises(RuntimeError):
            mailbox.sent_time("<delivery@example.net>")


if __name__ == "__main__":
    unittest.main()
