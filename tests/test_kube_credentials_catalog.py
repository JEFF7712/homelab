from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.ci import kube_credentials as credentials


class HaCredentialCatalog(unittest.TestCase):
    def operations(self) -> list[dict]:
        catalog = json.loads(
            (credentials.ROOT / "config/ci/operations.json").read_text()
        )
        return [
            op
            for name, op in catalog.items()
            if name.startswith("deploy-home-assistant")
        ]

    def test_ha_operations_use_only_restricted_issuer_and_pinned_hosts(self) -> None:
        operations = self.operations()
        self.assertEqual(len(operations), 4)
        for operation in operations:
            environment = operation["environment"]
            self.assertNotIn("HA_DEPLOYER_KUBECONFIG", environment)
            self.assertNotIn("SSH_DEPLOY_KEY", environment)
            self.assertEqual(
                environment["HA_ISSUER_SSH_KEY"], {"from_secret": credentials.NAME}
            )
            self.assertEqual(environment["HOSTS_KNOWN"], {"from_secret": "hosts_known"})
            self.assertIn("HA_ISSUER_SSH_KEY", operation["file_secrets"])
            self.assertIn("HOSTS_KNOWN", operation["file_secrets"])

    def test_temporary_kubeconfig_is_removed_even_when_job_fails(self) -> None:
        for operation in self.operations():
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                binary = root / "python"
                binary.write_text("""#!/bin/sh
set -eu
test "$1" = -m
test "$2" = scripts.ci.kube_credentials
test "$3" = fetch
shift 3
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output) output=$2 ;;
  esac
  shift 2
done
printf 'fixture scoped config' > "$output"
chmod 0600 "$output"
printf '%s' "$output" > "$KUBE_TEST_PATH"
""")
                binary.chmod(0o755)
                record = root / "created-path"
                commands = operation["commands"]
                setup = commands[1:4]
                result = subprocess.run(
                    ["bash", "-euc", "\n".join([*setup, "false"])],
                    env={
                        **os.environ,
                        "PATH": directory + ":" + os.environ["PATH"],
                        "TMPDIR": directory,
                        "HA_ISSUER_SSH_KEY": "/private/key",
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


class CiTokenManifestTests(unittest.TestCase):
    def test_fixed_service_accounts_have_no_long_lived_token_secrets(self) -> None:
        for namespace, account, filename in (
            ("home-assistant", "ha-deployer", "deployer-access.yaml"),
            ("automation", "registry-advance", "registry-advance-access.yaml"),
        ):
            with self.subTest(account=account):
                path = credentials.ROOT / "gitops" / namespace / filename
                documents = list(yaml.safe_load_all(path.read_text()))
                accounts = [
                    item["metadata"]["name"]
                    for item in documents
                    if item["kind"] == "ServiceAccount"
                ]
                self.assertIn(account, accounts)
                static_tokens = [
                    item["metadata"]["name"]
                    for item in documents
                    if item["kind"] == "Secret"
                    and item.get("type") == "kubernetes.io/service-account-token"
                    and item["metadata"]
                    .get("annotations", {})
                    .get("kubernetes.io/service-account.name")
                    == account
                ]
                self.assertEqual(static_tokens, [])
