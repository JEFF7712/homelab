"""Woodpecker pipeline retention on homelab-04 (woodpecker_retention.py)."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "flake" / "modules" / "woodpecker_retention.py"


def load_module():
    spec = importlib.util.spec_from_file_location("woodpecker_retention", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


retention = load_module()


def log(level: str, message: str, **fields: Any) -> str:
    return json.dumps({"level": level, "message": message, **fields})


class FakeRun:
    """Stands in for woodpecker-cli; returns canned stderr per repository id."""

    def __init__(self, results: dict[str, tuple[int, list[str]]]) -> None:
        self.results = results
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, command, env, timeout, **_kwargs):
        self.calls.append((list(command), dict(env)))
        code, lines = self.results[command[-1]]
        return subprocess.CompletedProcess(command, code, "", "\n".join(lines) + "\n")


class WrapperTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.dir = Path(temporary.name)
        self.token = self.dir / "token"
        self.token.write_text("secret-token\n")
        self.output = self.dir / "out"

    def args(self, *extra: str) -> list[str]:
        return [
            "--server",
            "http://127.0.0.1:8000/",
            "--token-file",
            str(self.token),
            "--cli",
            "/bin/woodpecker-cli",
            "--older-than",
            "720h",
            "--keep-min",
            "50",
            "--output",
            str(self.output),
            "--work-dir",
            str(self.dir),
            "--deadline",
            "3600",
            *extra,
        ]

    def metrics(self) -> str:
        return (self.output / retention.OUTPUT_NAME).read_text()

    def test_purges_every_listed_repository_and_counts_deletions(self) -> None:
        pages: list[str] = []

        def fetch(url: str, token: str) -> Any:
            self.assertEqual(token, "secret-token")
            pages.append(url)
            if "page=1&" in url:
                return [{"id": n, "full_name": f"o/r{n}"} for n in range(1, 51)]
            return [{"id": 51, "full_name": 'o/"odd"'}]

        purge_lines = [
            log("debug", f"purge {i}/3 pipelines from repo '1' (pipeline {i})")
            for i in (1, 2, 3)
        ]
        refused = log(
            "error",
            "failed to delete pipeline 3",
            error="client error 422: Cannot delete pipeline with status running",
        )
        results = {str(n): (0, []) for n in range(2, 52)}
        results["1"] = (0, [*purge_lines, refused])
        run = FakeRun(results)

        stdout = self.dir / "stdout"
        with open(stdout, "w") as handle:
            original = sys.stdout
            sys.stdout = handle
            try:
                code = retention.main(self.args(), fetch=fetch, run=run)
            finally:
                sys.stdout = original

        self.assertEqual(code, 0)
        self.assertEqual(
            pages,
            [
                "http://127.0.0.1:8000/api/repos?page=1&perPage=50",
                "http://127.0.0.1:8000/api/repos?page=2&perPage=50",
            ],
        )
        self.assertEqual(len(run.calls), 51)
        command, env = run.calls[0]
        self.assertEqual(
            command,
            [
                "/bin/woodpecker-cli",
                "pipeline",
                "purge",
                "--older-than",
                "720h",
                "--keep-min",
                "50",
                "1",
            ],
        )
        # The token travels in the environment, never in argv.
        self.assertEqual(env["WOODPECKER_TOKEN"], "secret-token")
        self.assertNotIn("secret-token", " ".join(command))
        self.assertEqual(env["WOODPECKER_SERVER"], "http://127.0.0.1:8000")
        self.assertEqual(env["WOODPECKER_LOG_LEVEL"], "debug")
        self.assertEqual(env["WOODPECKER_DISABLE_UPDATE_CHECK"], "true")

        body = self.metrics()
        self.assertIn("homelab_woodpecker_retention_success 1\n", body)
        self.assertIn("homelab_woodpecker_retention_repositories 51\n", body)
        self.assertIn(
            'homelab_woodpecker_retention_deleted_pipelines{repo="o/r1"} 2\n', body
        )
        self.assertIn(
            'homelab_woodpecker_retention_refused_pipelines{repo="o/r1"} 1\n', body
        )
        self.assertIn('{repo="o/\\"odd\\""} 0\n', body)
        summary = stdout.read_text()
        self.assertIn("o/r1 (id 1): deleted 2 of 3 candidates, 1 refused", summary)
        self.assertNotIn("secret-token", summary)

    def test_cli_failure_is_reported_not_raised(self) -> None:
        fetch = lambda url, token: [
            {"id": 1, "full_name": "o/a"},
            {"id": 2, "full_name": "o/b"},
        ]
        run = FakeRun(
            {
                "1": (
                    1,
                    [
                        log("debug", "purge 1/2 pipelines from repo '1' (pipeline 7)"),
                        "Error: client error 500: database is locked",
                    ],
                ),
                "2": (0, []),
            }
        )
        self.assertEqual(retention.main(self.args(), fetch=fetch, run=run), 0)
        body = self.metrics()
        self.assertIn("homelab_woodpecker_retention_success 0\n", body)
        self.assertIn("homelab_woodpecker_retention_failed_repositories 1\n", body)
        # The deletion the CLI stopped at did not complete.
        self.assertIn('deleted_pipelines{repo="o/a"} 0\n', body)
        # The next repository is still purged.
        self.assertEqual([call[0][-1] for call in run.calls], ["1", "2"])

    def test_listing_failure_is_reported_not_raised(self) -> None:
        def fetch(url: str, token: str) -> Any:
            raise OSError("connection refused")

        run = FakeRun({})
        self.assertEqual(retention.main(self.args(), fetch=fetch, run=run), 0)
        self.assertIn("homelab_woodpecker_retention_success 0\n", self.metrics())
        self.assertEqual(run.calls, [])

    def test_empty_token_is_reported_not_raised(self) -> None:
        self.token.write_text("\n")
        self.assertEqual(
            retention.main(self.args(), fetch=lambda u, t: [], run=FakeRun({})), 0
        )
        self.assertIn("homelab_woodpecker_retention_success 0\n", self.metrics())

    def test_deadline_stops_before_remaining_repositories(self) -> None:
        ticks = iter([0.0, 10.0, 4000.0])
        fetch = lambda url, token: [
            {"id": 1, "full_name": "o/a"},
            {"id": 2, "full_name": "o/b"},
        ]
        run = FakeRun({"1": (0, []), "2": (0, [])})
        retention.main(self.args(), fetch=fetch, run=run, clock=lambda: next(ticks))
        self.assertEqual([call[0][-1] for call in run.calls], ["1"])
        self.assertIn(
            "homelab_woodpecker_retention_failed_repositories 1\n", self.metrics()
        )

    def test_keep_min_must_be_positive(self) -> None:
        args = self.args()
        args[args.index("50")] = "0"
        with self.assertRaises(SystemExit):
            retention.parse_args(args)


class FakeWoodpecker(BaseHTTPRequestHandler):
    """Enough of the Woodpecker API for repository listing and purge."""

    pipelines: ClassVar[dict[int, dict[str, Any]]] = {}
    deleted: ClassVar[list[int]] = []

    def log_message(self, *args: Any) -> None:
        pass

    def reply(self, code: int, body: Any = None) -> None:
        data = b"" if body is None else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if self.headers.get("Authorization") != "Bearer secret-token":
            return self.reply(401, "unauthorized")
        page = int(query.get("page", ["1"])[0])
        if url.path == "/api/repos":
            return self.reply(200, [{"id": 1, "full_name": "o/a"}] if page == 1 else [])
        if url.path == "/api/repos/1":
            return self.reply(200, {"id": 1, "full_name": "o/a"})
        if url.path == "/api/repos/1/pipelines":
            rows = sorted(self.pipelines.values(), key=lambda p: -p["number"])
            if "before" in query:
                before = datetime.fromisoformat(query["before"][0]).timestamp()
                rows = [p for p in rows if p["created"] < before]
            per_page = 50
            return self.reply(200, rows[(page - 1) * per_page : page * per_page])
        return self.reply(404, "not found")

    def do_DELETE(self) -> None:
        number = int(self.path.rsplit("/", 1)[1])
        pipeline = self.pipelines[number]
        if pipeline["status"] in {"running", "pending", "blocked"}:
            return self.reply(
                422, f"Cannot delete pipeline with status {pipeline['status']}"
            )
        del self.pipelines[number]
        self.deleted.append(number)
        return self.reply(204)


@unittest.skipUnless(
    os.environ.get("WOODPECKER_CLI") or shutil.which("woodpecker-cli"),
    "woodpecker-cli required",
)
class PinnedCliTest(unittest.TestCase):
    """Run the real CLI against a fake server: keep-min, age and refusals."""

    def test_purge_keeps_newest_and_refuses_running(self) -> None:
        now = datetime.now(UTC)
        pipelines = {}
        for number in range(1, 81):
            # 1..70 are 40 days old, 71..80 are a day old.
            age = timedelta(days=40 if number <= 70 else 1)
            status = "running" if number == 5 else "success"
            pipelines[number] = {
                "id": number,
                "number": number,
                "status": status,
                "created": int((now - age).timestamp()),
            }
        FakeWoodpecker.pipelines = pipelines
        FakeWoodpecker.deleted = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeWoodpecker)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as temporary:
            token = Path(temporary, "token")
            token.write_text("secret-token")
            cli = os.environ.get("WOODPECKER_CLI") or shutil.which("woodpecker-cli")
            retention.main(
                [
                    "--server",
                    f"http://127.0.0.1:{server.server_port}",
                    "--token-file",
                    str(token),
                    "--cli",
                    str(cli),
                    "--older-than",
                    "720h",
                    "--keep-min",
                    "50",
                    "--output",
                    temporary,
                    "--work-dir",
                    temporary,
                    "--deadline",
                    "120",
                ]
            )
            body = Path(temporary, retention.OUTPUT_NAME).read_text()
        # The newest 50 (31..80) stay; of the old 1..30, running 5 is refused.
        self.assertEqual(
            sorted(FakeWoodpecker.deleted), [n for n in range(1, 31) if n != 5]
        )
        self.assertEqual(sorted(pipelines), [5, *range(31, 81)])
        self.assertIn('deleted_pipelines{repo="o/a"} 29\n', body)
        self.assertIn('refused_pipelines{repo="o/a"} 1\n', body)
        self.assertIn("homelab_woodpecker_retention_success 1\n", body)


@unittest.skipIf(os.getenv("SKIP_NIX_EVAL") == "1", "Nix evaluation disabled")
@unittest.skipUnless(shutil.which("nix"), "Nix required")
class EvaluatedServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        expression = """
          let
            flake = builtins.getFlake __FLAKE__;
            config = flake.nixosConfigurations.homelab-04.config;
            service = config.systemd.services.woodpecker-retention;
          in {
            serviceConfig = service.serviceConfig;
            restartIfChanged = service.restartIfChanged;
            wantedBy = service.wantedBy;
            timer = config.systemd.timers.woodpecker-retention.timerConfig;
            timerWantedBy = config.systemd.timers.woodpecker-retention.wantedBy;
            secret = config.sops.secrets.woodpecker-retention-token.path;
            secretFile = toString config.sops.secrets.woodpecker-retention-token.sopsFile;
            exporterFlags = config.services.prometheus.exporters.node.extraFlags;
          }
        """.replace("__FLAKE__", json.dumps(f"git+file://{ROOT}?dir=flake"))
        result = subprocess.run(
            ["nix", "eval", "--impure", "--json", "--expr", expression],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr)
        cls.config = json.loads(result.stdout)
        cls.service = cls.config["serviceConfig"]

    def test_daily_timer_avoids_backups_and_catches_up(self) -> None:
        timer = self.config["timer"]
        self.assertEqual(timer["OnCalendar"], "*-*-* 04:40:00")
        self.assertEqual(timer["OnActiveSec"], "20min")
        self.assertTrue(timer["Persistent"])
        self.assertEqual(self.config["timerWantedBy"], ["timers.target"])
        # Only the timer starts it; a deploy never restarts a running purge.
        self.assertEqual(self.config["wantedBy"], [])
        self.assertFalse(self.config["restartIfChanged"])

    def test_purges_thirty_days_keeping_fifty(self) -> None:
        start = self.service["ExecStart"]
        self.assertIn("woodpecker_retention.py", start)
        self.assertIn("--older-than 720h --keep-min 50 ", start)
        self.assertIn("--server http://127.0.0.1:8000 ", start)
        self.assertIn("woodpecker-cli-", start)
        self.assertNotIn("--dry-run", start)

    def test_never_fails_the_unit(self) -> None:
        self.assertEqual(self.service["Type"], "oneshot")
        self.assertTrue(self.service["ExecStart"].startswith("-/nix/store/"))
        self.assertTrue(self.service["ExecStartPost"].startswith("-+/nix/store/"))
        # The start timeout exceeds the wrapper's own deadline.
        start = self.service["ExecStart"]
        deadline = int(start.split("--deadline ")[1].split()[0])
        self.assertGreater(int(self.service["TimeoutStartSec"].rstrip("s")), deadline)

    def test_token_is_a_loaded_credential(self) -> None:
        self.assertEqual(
            self.service["LoadCredential"],
            f"woodpecker-token:{self.config['secret']}",
        )
        self.assertIn("--token-file %d/woodpecker-token ", self.service["ExecStart"])
        self.assertTrue(
            self.config["secretFile"].endswith("secrets/hosts/homelab-04.sops.yaml")
        )

    def test_sandboxed_and_low_priority(self) -> None:
        service = self.service
        self.assertTrue(service["DynamicUser"])
        self.assertEqual(service["Nice"], 19)
        self.assertEqual(service["IOSchedulingClass"], "idle")
        self.assertEqual(service["ProtectSystem"], "strict")
        self.assertEqual(service["IPAddressDeny"], "any")
        self.assertEqual(service["IPAddressAllow"], "localhost")
        self.assertEqual(service["CapabilityBoundingSet"], "")
        for key in (
            "ProtectHome",
            "PrivateTmp",
            "PrivateDevices",
            "NoNewPrivileges",
            "RestrictSUIDSGID",
            "ProtectKernelTunables",
        ):
            self.assertTrue(service[key], key)

    def test_metrics_reach_the_textfile_collector(self) -> None:
        self.assertIn(
            "--collector.textfile.directory=/var/lib/persist-usage-metrics",
            self.config["exporterFlags"],
        )


if __name__ == "__main__":
    unittest.main()
