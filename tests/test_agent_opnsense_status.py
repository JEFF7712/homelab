import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.agent.opnsense_status import (
    ENDPOINTS,
    Unavailable,
    credentials,
    diagnostic_health,
    main,
    probe,
)

ROOT = Path(__file__).resolve().parents[1]


class OpnsenseStatusTest(unittest.TestCase):
    def test_health_requires_expected_asn_established_peers_and_all_routes(
        self,
    ) -> None:
        summary = {
            "response": {
                "ipv4Unicast": {
                    "peers": {"10.0.30.11": {"state": "Established", "remoteAs": 64512}}
                }
            }
        }
        routes = {"total": 1, "rows": [{"prefix": "10.0.40.10"}]}
        expected_peers = {"10.0.30.11": 64512}
        expected_routes = {"10.0.40.10/32"}
        self.assertEqual(
            diagnostic_health(summary, routes, expected_peers, expected_routes)[
                "status"
            ],
            "pass",
        )
        summary["response"]["ipv4Unicast"]["peers"]["10.0.30.11"]["remoteAs"] = 64513
        result = diagnostic_health(
            summary, routes, expected_peers, expected_routes | {"10.0.40.13/32"}
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["missing_peers"], ["10.0.30.11"])
        self.assertEqual(result["missing_routes"], ["10.0.40.13/32"])

    def test_partial_or_malformed_responses_cannot_pass(self) -> None:
        summary = {"response": {"peers": {}}}
        expectations = ({"10.0.30.11": 64512}, {"10.0.40.10/32"})
        for routes in (
            {},
            {"total": 2, "rows": [{"prefix": "10.0.40.10"}]},
            {"total": 1, "rows": [{}]},
        ):
            with self.subTest(routes=routes), self.assertRaises(ValueError):
                diagnostic_health(summary, routes, *expectations)
        with self.assertRaises(ValueError):
            diagnostic_health({}, {"total": 0, "rows": []}, *expectations)

    def test_empty_expectations_cannot_pass(self) -> None:
        with self.assertRaises(ValueError):
            diagnostic_health(
                {"response": {"peers": {}}}, {"total": 0, "rows": []}, {}, set()
            )

    @patch.dict(os.environ, {"OPNSENSE_READONLY_API_KEY": "readonly-key"}, clear=True)
    def test_partial_explicit_credentials_fail_without_falling_back(self) -> None:
        with patch("scripts.agent.opnsense_status.subprocess.run") as run:
            with self.assertRaises(Unavailable):
                credentials(ROOT)
            run.assert_not_called()

    @patch.dict(
        os.environ,
        {
            "OPNSENSE_API_KEY": "admin-key",
            "OPNSENSE_API_SECRET": "admin-secret",
            "SOPS_AGE_KEY_FILE": "/private/age.key",
        },
        clear=True,
    )
    def test_decryption_extracts_only_readonly_properties_and_never_uses_admin_pair(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "gitops/secrets/homelab-values.sops.yaml"
            source.parent.mkdir(parents=True)
            source.write_text("encrypted")
            results = [
                subprocess.CompletedProcess([], 0, "readonly-key\n", ""),
                subprocess.CompletedProcess([], 0, "readonly-secret\n", ""),
            ]
            with patch(
                "scripts.agent.opnsense_status.subprocess.run", side_effect=results
            ) as run:
                self.assertEqual(credentials(root), ("readonly-key", "readonly-secret"))
            commands = [call.args[0] for call in run.call_args_list]
            self.assertIn('["stringData"]["OPNSENSE_READONLY_API_KEY"]', commands[0])
            self.assertIn('["stringData"]["OPNSENSE_READONLY_API_SECRET"]', commands[1])
            self.assertNotIn("admin-key", str(commands))
            self.assertNotIn("readonly-secret", str(commands))

    def test_probe_uses_verified_tls_and_only_the_two_get_endpoints(self) -> None:
        response = Mock(status=200)
        response.read.return_value = b"{}"
        connection = Mock()
        connection.getresponse.return_value = response
        with tempfile.TemporaryDirectory() as directory:
            ca_file = Path(directory) / "ca.pem"
            ca_file.touch()
            with (
                patch.dict(os.environ, {"OPNSENSE_CA_FILE": str(ca_file)}),
                patch(
                    "scripts.agent.opnsense_status.credentials",
                    return_value=("key", "secret"),
                ),
                patch(
                    "scripts.agent.opnsense_status.ssl.create_default_context"
                ) as context,
                patch(
                    "scripts.agent.opnsense_status.http.client.HTTPSConnection",
                    return_value=connection,
                ) as connect,
                patch(
                    "scripts.agent.opnsense_status.diagnostic_health",
                    return_value={"status": "pass"},
                ),
            ):
                self.assertEqual(
                    probe(ROOT, "https://OPNsense.internal")["status"], "pass"
                )
            context.assert_called_once_with(cafile=str(ca_file))
            self.assertEqual(connect.call_count, 2)
            self.assertIs(connect.call_args.kwargs["context"], context.return_value)
            self.assertEqual(
                [
                    (call.args[0], call.args[1])
                    for call in connection.request.call_args_list
                ],
                [("GET", endpoint) for endpoint in ENDPOINTS],
            )

    @patch.dict(os.environ, {}, clear=True)
    def test_vm_missing_scoped_secret_does_not_use_operator_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identity = root / ".config/sops/age/agent-rupan.key"
            identity.parent.mkdir(parents=True)
            identity.touch()
            operator_source = root / "gitops/secrets/homelab-values.sops.yaml"
            operator_source.parent.mkdir(parents=True)
            operator_source.touch()
            with (
                patch("pathlib.Path.home", return_value=root),
                patch("scripts.agent.opnsense_status.subprocess.run") as run,
            ):
                with self.assertRaises(Unavailable):
                    credentials(root)
            run.assert_not_called()

    @patch.dict(os.environ, {}, clear=True)
    def test_vm_identity_selects_scoped_encrypted_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identity = root / ".config/sops/age/agent-rupan.key"
            identity.parent.mkdir(parents=True)
            identity.touch()
            source = root / "secrets/agent-rupan-opnsense-readonly.sops.json"
            source.parent.mkdir()
            source.touch()
            results = [
                subprocess.CompletedProcess([], 0, "readonly-key\n", ""),
                subprocess.CompletedProcess([], 0, "readonly-secret\n", ""),
            ]
            with (
                patch("pathlib.Path.home", return_value=root),
                patch(
                    "scripts.agent.opnsense_status.subprocess.run", side_effect=results
                ) as run,
            ):
                self.assertEqual(credentials(root), ("readonly-key", "readonly-secret"))
            self.assertEqual(
                run.call_args_list[0].kwargs["env"]["SOPS_AGE_KEY_FILE"], str(identity)
            )
            self.assertIn(
                '["OPNSENSE_READONLY_API_KEY"]', run.call_args_list[0].args[0]
            )
            self.assertEqual(run.call_args_list[0].args[0][-1], str(source))

    def test_failure_never_echoes_exception_or_credentials(self) -> None:
        output = io.StringIO()
        with (
            patch(
                "sys.argv",
                ["probe", "--root", str(ROOT), "--url", "https://OPNsense.internal"],
            ),
            patch(
                "scripts.agent.opnsense_status.probe",
                side_effect=ValueError("sensitive-value"),
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(main(), 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "failed")
        self.assertNotIn("sensitive-value", output.getvalue())

    def test_http_denial_reports_only_status_without_reading_response_body(
        self,
    ) -> None:
        response = Mock(status=403)
        response.read.return_value = b"sensitive-error-body"
        connection = Mock()
        connection.getresponse.return_value = response
        with tempfile.TemporaryDirectory() as directory:
            ca_file = Path(directory) / "ca.pem"
            ca_file.touch()
            with (
                patch.dict(os.environ, {"OPNSENSE_CA_FILE": str(ca_file)}),
                patch(
                    "scripts.agent.opnsense_status.credentials",
                    return_value=("key", "secret"),
                ),
                patch("scripts.agent.opnsense_status.ssl.create_default_context"),
                patch(
                    "scripts.agent.opnsense_status.http.client.HTTPSConnection",
                    return_value=connection,
                ),
            ):
                result = probe(ROOT, "https://OPNsense.internal")
        self.assertEqual(
            result, {"status": "failed", "detail": "OPNsense returned HTTP 403"}
        )
        response.read.assert_not_called()
        connection.close.assert_called_once()

    def test_plain_http_or_embedded_credentials_are_rejected_before_loading_keys(
        self,
    ) -> None:
        for url in ("http://OPNsense.internal", "https://key:secret@OPNsense.internal"):
            with (
                self.subTest(url=url),
                patch("scripts.agent.opnsense_status.credentials") as load,
            ):
                with self.assertRaises(ValueError):
                    probe(ROOT, url)
                load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
