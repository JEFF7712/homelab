import os
import re
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from scripts.orchestrator import credentials


def fake_sops(directory: Path) -> str:
    """A sops stand-in that records its stdin and returns an encrypted marker."""
    script = directory / "sops"
    script.write_text(
        "#!/bin/sh\n"
        f'cat > "{directory}/stdin-$$"\n'
        "echo 'stringData:'\n"
        "echo '  value: ENC[AES256_GCM,data:x,type:str]'\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


class MachineSecretTests(unittest.TestCase):
    def test_intake_tokens_are_shared_with_their_senders(self) -> None:
        files = {f.name: f for f in credentials.machine_secrets()}
        controller = files["orchestrator-secrets"].data
        self.assertEqual(
            controller["alertmanager-intake-token"],
            files["orchestrator-intake-alertmanager"].data["token"],
        )
        self.assertEqual(
            controller["sentinel-intake-token"],
            files["orchestrator-intake-sentinel"].data["token"],
        )
        self.assertNotEqual(
            controller["alertmanager-intake-token"], controller["sentinel-intake-token"]
        )
        self.assertRegex(controller["ntfy-token"], r"^tk_[a-z0-9]{29}$")
        self.assertEqual(
            {f.namespace for f in files.values()},
            {"persistent-agent", "observability", "automation"},
        )

    def test_values_are_fresh_each_time(self) -> None:
        first = credentials.machine_secrets()[0].data
        second = credentials.machine_secrets()[0].data
        for key in first:
            self.assertNotEqual(first[key], second[key])

    def test_plaintext_goes_only_to_sops_on_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            out = directory / "gitops/secrets"
            out.mkdir(parents=True)
            with (
                mock.patch.object(credentials, "SECRETS", out),
                mock.patch.object(credentials, "ROOT", directory),
            ):
                written = credentials.generate(False, fake_sops(directory))
                self.assertEqual(len(written), 3)
                values = [
                    value
                    for f in (directory / name for name in os.listdir(directory))
                    if f.name.startswith("stdin-")
                    for value in yaml.safe_load(f.read_text())["stringData"].values()
                ]
                self.assertEqual(len(values), 6)
                for path in written:
                    text = path.read_text()
                    self.assertIn("ENC[AES256_GCM", text)
                    for value in values:
                        self.assertNotIn(value, text)
                # Existing credentials are never replaced without --force.
                with self.assertRaises(SystemExit):
                    credentials.generate(False, fake_sops(directory))

    def test_sops_output_without_encryption_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "sops"
            script.write_text("#!/bin/sh\ncat\n")
            script.chmod(0o755)
            with self.assertRaisesRegex(RuntimeError, "unencrypted"):
                credentials.encrypt(credentials.machine_secrets()[1], str(script))

    def test_the_target_files_are_not_applied_by_flux_yet(self) -> None:
        kustomization = (credentials.SECRETS / "kustomization.yaml").read_text()
        for secret in credentials.machine_secrets():
            self.assertIsNone(re.search(re.escape(secret.path), kustomization))


if __name__ == "__main__":
    unittest.main()
