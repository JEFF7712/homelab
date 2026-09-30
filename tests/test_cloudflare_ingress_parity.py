"""The OpenTofu stack must send the tunnel exactly the rules the ConfigMap mirror holds.

`tofu/cloudflare/main.tf` decodes `gitops/cloudflare/ingress-config.yaml` with
`yamldecode` instead of restating the rules, which is what keeps the mirror and
the edge from drifting. That only holds if the decode is still wired to that file
and still produces the same ordered list, so this compares the evaluated
`local.ingress` against the mirror.

`tofu console` needs an initialised backend, which only CI has (the plan job
initialises it from the GitLab-managed state). Everywhere else the test skips
rather than reporting a false pass, and `tofu validate` plus the two `check`
blocks in main.tf cover the rest.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STACK = ROOT / "tofu" / "cloudflare"
MIRROR = ROOT / "gitops" / "cloudflare" / "ingress-config.yaml"
BACKEND_STATE = STACK / ".terraform" / "terraform.tfstate"


def evaluated_ingress() -> list[dict]:
    result = subprocess.run(
        [
            "tofu",
            f"-chdir={STACK}",
            "console",
            "-var=cloudflare_account_id=placeholder",
        ],
        input="jsonencode(local.ingress)\n",
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=True,
    )
    # `tofu console` renders the jsonencode result as a quoted HCL string, so
    # the value arrives double-encoded.
    return json.loads(json.loads(result.stdout.strip().splitlines()[-1]))


def mirrored_ingress() -> list[dict]:
    config = yaml.safe_load(MIRROR.read_text(encoding="utf-8"))
    tunnel = yaml.safe_load(config["data"]["config.yaml"])
    return [
        {"hostname": rule.get("hostname"), "service": rule["service"]}
        for rule in tunnel["ingress"]
    ]


class TestTunnelIngressIsMirrored(unittest.TestCase):
    @unittest.skipUnless(
        BACKEND_STATE.exists(), "tofu/cloudflare backend not initialised (CI only)"
    )
    def test_evaluated_ingress_matches_mirror(self) -> None:
        self.assertEqual(evaluated_ingress(), mirrored_ingress())

    def test_mirror_ends_in_the_catch_all(self) -> None:
        rules = mirrored_ingress()
        self.assertEqual(rules[-1], {"hostname": None, "service": "http_status:404"})


if __name__ == "__main__":
    unittest.main()
