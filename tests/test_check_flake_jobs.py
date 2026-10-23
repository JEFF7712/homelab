from __future__ import annotations

import json
import unittest

from scripts.checks.flake_jobs import run


def line(**job: str) -> str:
    return json.dumps(job)


class FlakeJobsTest(unittest.TestCase):
    def test_builds_checks_and_only_evaluates_hosts(self) -> None:
        built: list[str] = []
        count, failures = run(
            [
                line(attr="checks.topology", drvPath="/nix/store/b-topology.drv"),
                line(attr="hosts.nas-01", drvPath="/nix/store/a-nas.drv"),
                "",
                line(attr="checks.opnsense-types", drvPath="/nix/store/c-types.drv"),
            ],
            lambda target: built.append(target) is None,
        )
        self.assertEqual(failures, [])
        self.assertEqual(count, 2)
        self.assertEqual(
            built,
            ["/nix/store/b-topology.drv^*", "/nix/store/c-types.drv^*"],
        )

    def test_evaluation_and_build_failures_are_reported(self) -> None:
        _, failures = run(
            [
                line(attr="hosts.homelab-04", error="infinite recursion"),
                line(attr="checks.topology", drvPath="/nix/store/b-topology.drv"),
            ],
            lambda target: False,
        )
        self.assertEqual(
            failures,
            [
                "hosts.homelab-04: infinite recursion",
                "/nix/store/b-topology.drv^*: build failed",
            ],
        )

    def test_no_checks_is_a_failure(self) -> None:
        _, failures = run(
            [line(attr="hosts.nas-01", drvPath="/nix/store/a-nas.drv")],
            lambda target: True,
        )
        self.assertEqual(failures, ["no checks evaluated"])


if __name__ == "__main__":
    unittest.main()
