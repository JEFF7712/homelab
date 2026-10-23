from __future__ import annotations

import json
import unittest

from scripts.checks.flake_jobs import check_targets


def line(**job: str) -> str:
    return json.dumps(job)


class FlakeJobsTest(unittest.TestCase):
    def test_builds_checks_and_only_evaluates_hosts(self) -> None:
        targets, errors = check_targets(
            [
                line(attr="hosts.nas-01", drvPath="/nix/store/a-nas.drv"),
                line(attr="checks.topology", drvPath="/nix/store/b-topology.drv"),
                line(attr="checks.opnsense-types", drvPath="/nix/store/c-types.drv"),
                "",
            ]
        )
        self.assertEqual(errors, [])
        self.assertEqual(
            targets,
            ["/nix/store/b-topology.drv^*", "/nix/store/c-types.drv^*"],
        )

    def test_any_evaluation_error_is_reported(self) -> None:
        targets, errors = check_targets(
            [
                line(attr="hosts.homelab-04", error="infinite recursion"),
                line(attr="checks.topology", drvPath="/nix/store/b-topology.drv"),
            ]
        )
        self.assertEqual(errors, ["hosts.homelab-04: infinite recursion"])
        self.assertEqual(targets, ["/nix/store/b-topology.drv^*"])


if __name__ == "__main__":
    unittest.main()
