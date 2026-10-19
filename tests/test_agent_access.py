from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.agent.access import access_checks

ROOT = Path(__file__).resolve().parents[1]


class AccessChecksTest(unittest.TestCase):
    def home(self, directory: str) -> Path:
        home = Path(directory)
        (home / ".config/tea").mkdir(parents=True)
        (home / ".config/tea/config.yml").write_text(
            "logins:\n- name: a\n  url: http://git.internal:3000\n  token: t\n"
            "  user: homelab-agent\n  default: true\n"
        )
        (home / ".config/woodpecker").mkdir(parents=True)
        (home / ".config/woodpecker/token").write_text("w\n")
        (home / ".config/sops/age").mkdir(parents=True)
        (home / ".config/sops/age/keys.txt").write_text("AGE-SECRET-KEY-fixture\n")
        return home

    def test_reports_each_failure_with_a_remedy_and_never_the_secret(self) -> None:
        recipient = "age1k2vhn663mmw9ancuwm2hfmtfg4xyus9xlvp8rkep4fpvxlqa9gdsms9usl"

        def runner(command, timeout):
            if command[0] == "age-keygen":
                return 0, recipient
            if command[0] == "ssh" and command[-2].endswith(".13"):
                return 255, "Connection timed out"
            return 0, ""

        def fetcher(url, headers):
            return 401 if "ci.internal" in url else 200

        with tempfile.TemporaryDirectory() as directory:
            checks = access_checks(
                ROOT,
                self.home(directory),
                {"homelab-02": "10.0.30.12", "homelab-03": "10.0.30.13"},
                runner,
                fetcher,
                now=json_time(ROOT) + 60,
            )
        by_name = {item["name"]: item for item in checks}
        self.assertEqual(by_name["access:forgejo-api"]["status"], "pass")
        self.assertEqual(by_name["access:woodpecker-api"]["status"], "fail")
        self.assertTrue(by_name["access:woodpecker-api"]["remedy"])
        self.assertEqual(by_name["access:sops-age"]["status"], "pass")
        self.assertEqual(by_name["access:ssh:homelab-03"]["status"], "fail")
        self.assertEqual(by_name["access:ssh:homelab-02"]["status"], "pass")
        self.assertEqual(by_name["access:registry-observed-snapshot"]["status"], "pass")
        self.assertNotIn("AGE-SECRET-KEY", json.dumps(checks))


def json_time(root: Path) -> float:
    from datetime import datetime

    observed = json.loads((root / "registry/observed-images.json").read_text())
    return datetime.fromisoformat(observed["observed_at"]).timestamp()


if __name__ == "__main__":
    unittest.main()
