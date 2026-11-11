from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.ci import kube_credentials as credentials


class RegistryCredentialCatalog(unittest.TestCase):
    def operation(self) -> dict:
        return json.loads((credentials.ROOT / "config/ci/operations.json").read_text())[
            "registry-advance"
        ]

    def test_only_existing_read_only_profile_is_requested(self) -> None:
        operation = self.operation()
        environment = operation["environment"]
        self.assertNotIn("REGISTRY_ADVANCE_KUBECONFIG", environment)
        self.assertNotIn("SSH_DEPLOY_KEY", environment)
        self.assertEqual(
            environment["REGISTRY_ISSUER_SSH_KEY"],
            {"from_secret": "registry_issuer_ssh_key"},
        )
        self.assertEqual(environment["HOSTS_KNOWN"], {"from_secret": "hosts_known"})
        self.assertIn("REGISTRY_ISSUER_SSH_KEY", operation["file_secrets"])
        self.assertIn("HOSTS_KNOWN", operation["file_secrets"])

    def test_actual_catalog_setup_cleans_up_credentials_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "python"
            binary.write_text("""#!/bin/sh
set -eu
test "$1" = -m
test "$2" = scripts.ci.kube_credentials
test "$3" = fetch
test "$4" = --profile
test "$5" = registry-advance
shift 5
while [ "$#" -gt 0 ]; do
  case "$1" in --output) output=$2 ;; esac
  shift 2
done
printf 'fixture read-only config' > "$output"
chmod 0600 "$output"
printf '%s' "$output" > "$KUBE_TEST_PATH"
""")
            binary.chmod(0o755)
            record = root / "created-path"
            setup = self.operation()["commands"][1:4]
            result = subprocess.run(
                ["bash", "-euc", "\n".join([*setup, "false"])],
                env={
                    **os.environ,
                    "PATH": directory + ":" + os.environ["PATH"],
                    "TMPDIR": directory,
                    "REGISTRY_ISSUER_SSH_KEY": "/private/key",
                    "HOSTS_KNOWN": "/private/known-hosts",
                    "KUBE_TEST_PATH": str(record),
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertTrue(record.is_file(), result.stderr)
            self.assertFalse(Path(record.read_text()).exists())
