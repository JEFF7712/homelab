import ipaddress
import json
import tempfile
import unittest
import urllib.error
from email.message import Message
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from opnsense_reconciler.dataplane import (
    main,
    probe_target,
    probe_targets,
    verify_lb_data_plane,
)


def targets() -> list[dict[str, object]]:
    return [
        {"url": "http://10.0.40.10/", "expect_status": 200},
        {"url": "http://10.0.40.13:8123/manifest.json", "expect_status": 200},
    ]


class DataPlaneVerificationTests(unittest.TestCase):
    def test_ready_when_all_targets_answer_expected_status(self) -> None:
        proof = verify_lb_data_plane(
            targets(),
            {
                "http://10.0.40.10/": (True, 200),
                "http://10.0.40.13:8123/manifest.json": (True, 200),
            },
        )

        self.assertTrue(proof.ready)
        self.assertEqual(proof.unreachable, set())

    def test_reports_unreachable_and_wrong_status_targets(self) -> None:
        proof = verify_lb_data_plane(
            targets(),
            {
                "http://10.0.40.10/": (False, None),
                "http://10.0.40.13:8123/manifest.json": (True, 500),
            },
        )

        self.assertFalse(proof.ready)
        self.assertEqual(
            proof.unreachable,
            {"http://10.0.40.10/", "http://10.0.40.13:8123/manifest.json"},
        )

    def test_reports_missing_result_as_unreachable(self) -> None:
        proof = verify_lb_data_plane(targets(), {"http://10.0.40.10/": (True, 200)})

        self.assertFalse(proof.ready)
        self.assertEqual(proof.unreachable, {"http://10.0.40.13:8123/manifest.json"})

    def test_rejects_invalid_targets(self) -> None:
        for bad in (
            [],
            "http://10.0.40.10/",
            [{"url": "https://10.0.40.10/", "expect_status": 200}],
            [{"url": "http://10.0.40.10/"}],
            [{"url": "http://10.0.40.10/", "expect_status": "200"}],
        ):
            with self.subTest(targets=bad), self.assertRaises(ValueError):
                verify_lb_data_plane(bad, {})

    def test_probe_target_maps_transport_errors_to_unreachable(self) -> None:
        def failing_opener(url: str, timeout: float) -> object:
            del url, timeout
            raise urllib.error.URLError("refused")

        self.assertEqual(
            probe_target("http://10.0.40.10/", 1.0, failing_opener), (False, None)
        )

    def test_probe_target_reports_http_errors_as_answered(self) -> None:
        def not_found(url: str, timeout: float) -> object:
            del timeout
            raise urllib.error.HTTPError(url, 404, "Not Found", Message(), None)

        with patch("sys.stderr", new_callable=StringIO) as stderr:
            result = probe_target("http://10.0.40.10/", 1.0, not_found)

        self.assertEqual(result, (True, 404))
        self.assertIn("HTTP 404", stderr.getvalue())
        proof = verify_lb_data_plane(
            targets(),
            {
                "http://10.0.40.10/": result,
                "http://10.0.40.13:8123/manifest.json": (True, 200),
            },
        )
        self.assertEqual(proof.unreachable, {"http://10.0.40.10/"})

    def test_probe_target_names_the_transport_error(self) -> None:
        def timing_out(url: str, timeout: float) -> object:
            del url, timeout
            raise urllib.error.URLError(TimeoutError("timed out"))

        with patch("sys.stderr", new_callable=StringIO) as stderr:
            self.assertEqual(
                probe_target("http://10.0.40.10/", 1.0, timing_out), (False, None)
            )
        self.assertIn("http://10.0.40.10/: TimeoutError: timed out", stderr.getvalue())

    def test_probe_target_returns_status_and_closes(self) -> None:
        closed: list[bool] = []

        class FakeResponse:
            status = 200

            def close(self) -> None:
                closed.append(True)

        self.assertEqual(
            probe_target(
                "http://10.0.40.10/", 1.0, lambda url, timeout: FakeResponse()
            ),
            (True, 200),
        )
        self.assertEqual(closed, [True])

    def test_probe_targets_uses_injected_probe(self) -> None:
        seen: list[tuple[str, float]] = []

        def stub(url: str, timeout: float) -> tuple[bool, int | None]:
            seen.append((url, timeout))
            return True, 200

        results = probe_targets(targets(), 5.0, stub)

        self.assertEqual(
            results,
            {
                "http://10.0.40.10/": (True, 200),
                "http://10.0.40.13:8123/manifest.json": (True, 200),
            },
        )
        self.assertEqual(
            seen,
            [
                ("http://10.0.40.10/", 5.0),
                ("http://10.0.40.13:8123/manifest.json", 5.0),
            ],
        )

    def test_main_reports_ready_and_exits_zero(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "opnsense_reconciler/lb-data-plane-targets.json"
        )

        def stub(url: str, timeout: float) -> tuple[bool, int | None]:
            del url, timeout
            return True, 200

        with patch("sys.stdout", new_callable=StringIO) as stdout:
            main(["--targets", str(path)], probe=stub)

        report = json.loads(stdout.getvalue())
        self.assertTrue(report["ready"])
        self.assertEqual(report["unreachable"], [])

    def test_main_exits_nonzero_when_target_unreachable(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "opnsense_reconciler/lb-data-plane-targets.json"
        )

        def stub(url: str, timeout: float) -> tuple[bool, int | None]:
            del url, timeout
            return False, None

        with patch("sys.stdout", new_callable=StringIO):
            with self.assertRaises(SystemExit) as raised:
                main(["--targets", str(path)], probe=stub)

        self.assertEqual(raised.exception.code, 1)

    def test_main_writes_the_report_for_the_alert(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "opnsense_reconciler/lb-data-plane-targets.json"
        )

        def stub(url: str, timeout: float) -> tuple[bool, int | None]:
            del timeout
            return (False, None) if "10.0.40.13" in url else (True, 200)

        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "nested/dataplane.json"
            with (
                patch("sys.stdout", new_callable=StringIO),
                patch("sys.stderr", new_callable=StringIO),
            ):
                with self.assertRaises(SystemExit):
                    main(
                        ["--targets", str(path), "--report", str(report_path)],
                        probe=stub,
                    )
            report = json.loads(report_path.read_text())
        self.assertFalse(report["ready"])
        self.assertEqual(
            report["unreachable"], ["http://10.0.40.13:8123/manifest.json"]
        )


class DataPlaneAlertTests(unittest.TestCase):
    report = {
        "checked": 2,
        "ready": False,
        "unreachable": ["http://10.0.40.10/", "http://10.0.40.13:8123/manifest.json"],
    }

    def test_summary_names_the_vips_and_the_fix(self) -> None:
        from scripts.ci.notify import summarise_dataplane

        title, message = summarise_dataplane(self.report)
        self.assertEqual(title, "VIP data plane down: 2 unreachable")
        self.assertIn("2 of 2 LoadBalancer VIPs", message)
        self.assertIn("http://10.0.40.13:8123/manifest.json", message)
        self.assertIn("restart FRR", message)
        self.assertIn("opnsense-bgp-proof.md", message)

    def test_notify_posts_the_dataplane_summary(self) -> None:
        from scripts.ci import notify

        sent: list[object] = []

        class Response:
            status = 200

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *args: object) -> None:
                return None

        def fake_urlopen(request: object, timeout: float) -> Response:
            del timeout
            sent.append(request)
            return Response()

        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "dataplane.json"
            report_path.write_text(json.dumps(self.report))
            environment = {"NTFY_TOPIC": "homelab-alerts", "NTFY_TOKEN": "t0ken"}
            with (
                patch.dict("os.environ", environment, clear=True),
                patch.object(notify, "urlopen", fake_urlopen),
                patch("sys.stderr", new_callable=StringIO),
            ):
                self.assertEqual(notify.main(["vip-dataplane", str(report_path)]), 0)

        self.assertEqual(len(sent), 1)
        request = sent[0]
        self.assertEqual(
            request.full_url,
            "https://ntfy.rupan.dev/homelab-alerts",  # type: ignore[attr-defined]
        )
        self.assertEqual(
            request.get_header("Title"),  # type: ignore[attr-defined]
            "VIP data plane down: 2 unreachable",
        )
        self.assertEqual(
            request.get_header("Authorization"),  # type: ignore[attr-defined]
            "Bearer t0ken",
        )

    def test_notify_rejects_an_unknown_label(self) -> None:
        from scripts.ci import notify

        with patch("sys.stderr", new_callable=StringIO):
            with self.assertRaises(SystemExit):
                notify.main(["no-such-report", "unused.json"])

    def test_data_plane_targets_stay_inside_lb_pool(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "opnsense_reconciler/lb-data-plane-targets.json"
        )
        desired = json.loads(path.read_text())
        pool = ipaddress.ip_network("10.0.40.0/24")

        self.assertIsInstance(desired, list)
        self.assertTrue(desired)
        for target in desired:
            with self.subTest(target=target):
                host = target["url"].split("://", 1)[1].split("/", 1)[0].split(":")[0]
                address = ipaddress.ip_address(host)
                self.assertTrue(address in pool)
                self.assertIn(
                    int(address) - int(pool.network_address),
                    range(10, 20),
                )
                self.assertEqual(target["expect_status"], 200)


if __name__ == "__main__":
    unittest.main()
