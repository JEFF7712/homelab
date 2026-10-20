"""The OpenTofu stack must send the tunnel exactly the rules the ConfigMap mirror holds.

`tofu/cloudflare/main.tf` decodes `gitops/cloudflare/ingress-config.yaml` with
`yamldecode` instead of restating the rules, which is what keeps the mirror and
the edge from drifting. That only holds if the decode is still wired to that file
and still produces the same ordered list, so this compares the evaluated
`local.ingress` against the mirror.

`local.ingress` depends only on that file, so the stack is evaluated in a
scratch copy without its state backend: the check needs no state service or
credentials and runs wherever `tofu` and the provider are available.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STACK = ROOT / "tofu" / "cloudflare"
MIRROR = ROOT / "gitops" / "cloudflare" / "ingress-config.yaml"


def evaluated_ingress() -> list[dict]:
    with tempfile.TemporaryDirectory() as directory:
        # main.tf reads the mirror relative to path.module, so the copy keeps
        # the repository layout. backend.tf is left out.
        stack = Path(directory) / STACK.relative_to(ROOT)
        stack.mkdir(parents=True)
        for source in [*STACK.glob("*.tf"), STACK / ".terraform.lock.hcl"]:
            if source.name != "backend.tf":
                shutil.copy(source, stack / source.name)
        mirror = Path(directory) / MIRROR.relative_to(ROOT)
        mirror.parent.mkdir(parents=True)
        shutil.copy(MIRROR, mirror)
        subprocess.run(
            ["tofu", f"-chdir={stack}", "init", "-backend=false", "-lockfile=readonly"],
            capture_output=True,
            text=True,
            check=True,
        )
        result = subprocess.run(
            [
                "tofu",
                f"-chdir={stack}",
                "console",
                "-var=cloudflare_account_id=placeholder",
            ],
            input="jsonencode(local.ingress)\n",
            capture_output=True,
            text=True,
            check=True,
        )
    # `tofu console` renders the jsonencode result as a quoted HCL string, so
    # the value arrives double-encoded.
    try:
        return json.loads(json.loads(result.stdout.strip().splitlines()[-1]))
    except (IndexError, ValueError) as error:
        raise AssertionError(
            f"tofu console printed no JSON value\nstdout:\n{result.stdout}"
            f"\nstderr:\n{result.stderr}"
        ) from error


def mirrored_ingress() -> list[dict]:
    config = yaml.safe_load(MIRROR.read_text(encoding="utf-8"))
    tunnel = yaml.safe_load(config["data"]["config.yaml"])
    return [
        {
            "hostname": rule.get("hostname"),
            "service": rule["service"],
            "path": rule.get("path"),
        }
        for rule in tunnel["ingress"]
    ]


class TestTunnelIngressIsMirrored(unittest.TestCase):
    @unittest.skipUnless(shutil.which("tofu"), "tofu is not installed")
    def test_evaluated_ingress_matches_mirror(self) -> None:
        self.assertEqual(evaluated_ingress(), mirrored_ingress())

    def test_mirror_ends_in_the_catch_all(self) -> None:
        rules = mirrored_ingress()
        self.assertEqual(
            rules[-1], {"hostname": None, "service": "http_status:404", "path": None}
        )


if __name__ == "__main__":
    unittest.main()
