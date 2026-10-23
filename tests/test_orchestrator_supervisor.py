import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.orchestrator import __main__ as cli
from scripts.orchestrator.supervisor_policy import audit_letta


def record(
    name: str, auth_type: str, provider_type: str = "openai", **auth_fields: object
) -> dict[str, object]:
    auth: dict[str, object] = {"type": auth_type}
    auth.update(
        {"key": "sk-redacted"} if auth_type == "api" else {"access": "a", "expires": 0}
    )
    auth.update(auth_fields)
    return {
        "id": f"local-provider-{name}",
        "name": name,
        "provider_type": provider_type,
        "provider_category": "byok",
        "auth": auth,
    }


class SupervisorAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, *records: dict[str, object]) -> None:
        path = self.storage / "providers" / "auth.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"version": 1, "providers": {r["name"]: r for r in records}})
        )

    def test_no_providers_is_unconfigured_and_passes(self) -> None:
        audit = audit_letta(self.storage, {"PATH": "/bin"})
        self.assertEqual((audit.status, audit.ok), ("unconfigured", True))
        self.write()
        self.assertEqual(audit_letta(self.storage, {}).status, "unconfigured")

    def test_chatgpt_login_is_refused_even_if_approved(self) -> None:
        self.write(record("chatgpt-plus-pro", "oauth", "chatgpt_oauth"))
        audit = audit_letta(self.storage, {}, approved={"chatgpt-plus-pro"})
        self.assertFalse(audit.ok)
        self.assertIn("Codex-client", audit.violations[0])

    def test_any_oauth_login_is_refused(self) -> None:
        self.write(record("lc-anthropic", "oauth", "anthropic"))
        self.assertFalse(audit_letta(self.storage, {}, approved={"lc-anthropic"}).ok)

    def test_api_providers_need_explicit_approval(self) -> None:
        self.write(record("lc-openai", "api"))
        self.assertEqual(audit_letta(self.storage, {}).status, "violations")
        approved = audit_letta(self.storage, {}, approved={"lc-openai"})
        self.assertEqual((approved.status, approved.ok), ("approved", True))

    def test_chatgpt_provider_type_is_refused_whatever_its_auth(self) -> None:
        self.write(record("renamed", "api", "chatgpt_oauth"))
        self.assertFalse(audit_letta(self.storage, {}, approved={"renamed"}).ok)

    def test_approved_anthropic_api_key_provider_passes(self) -> None:
        self.write(record("lc-anthropic", "api", "anthropic"))
        self.assertTrue(audit_letta(self.storage, {}, approved={"lc-anthropic"}).ok)

    def test_approved_provider_must_use_an_api_key(self) -> None:
        self.write(record("custom", "bearer"))
        self.assertFalse(audit_letta(self.storage, {}, approved={"custom"}).ok)

    def sign_in(self, **overrides: object) -> dict[str, object]:
        fields: dict[str, object] = {
            "clientId": "app_issued_for_letta_homelab",
            "scopes": [
                "openid",
                "offline_access",
                "resource.invoke",
                "chatgpt.tokens.use.direct",
            ],
        }
        fields.update(overrides)
        return record("lc-openai", "oauth", "openai", **fields)

    def test_documented_chatgpt_sign_in_passes_only_when_enabled(self) -> None:
        self.write(self.sign_in())
        self.assertFalse(audit_letta(self.storage, {}).ok)
        audit = audit_letta(self.storage, {}, chatgpt_sign_in=True)
        self.assertEqual((audit.status, audit.ok), ("chatgpt-sign-in", True))

    def test_sign_in_must_carry_its_own_client_scope_and_endpoint(self) -> None:
        for bad in (
            {"clientId": "app_EMoamEEZ73f0CkXaXp7hrann"},
            {"clientId": "dynamic_agent_client"},
            {"clientId": ""},
            {"scopes": ["openid", "offline_access"]},
        ):
            self.write(self.sign_in(**bad))
            self.assertFalse(
                audit_letta(self.storage, {}, chatgpt_sign_in=True).ok, bad
            )
        proxied = self.sign_in()
        proxied["base_url"] = "http://127.0.0.1:8080/v1"
        self.write(proxied)
        self.assertFalse(audit_letta(self.storage, {}, chatgpt_sign_in=True).ok)

    def test_relabelled_oauth_record_is_refused(self) -> None:
        relabelled = self.sign_in()
        relabelled["name"] = "lc-anthropic"
        path = self.storage / "providers" / "auth.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"version": 1, "providers": {"lc-anthropic": relabelled}})
        )
        self.assertFalse(audit_letta(self.storage, {}, chatgpt_sign_in=True).ok)

    def test_endpoint_rejection_does_not_print_url_credentials(self) -> None:
        secret = "private-url-credential"
        provider = self.sign_in()
        provider["base_url"] = f"https://user:{secret}@example.invalid/v1"
        self.write(provider)
        output = io.StringIO()
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            contextlib.redirect_stdout(output),
        ):
            code = cli.main(
                [
                    "supervisor-audit",
                    "--letta-dir",
                    str(self.storage),
                    "--allow-chatgpt-sign-in",
                ]
            )
        self.assertEqual(code, 1)
        self.assertNotIn(secret, output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["status"], "violations")

    def test_malformed_authentication_is_a_structured_violation(self) -> None:
        for value in (["credential"], "credential", 1):
            provider = self.sign_in()
            provider["auth"] = value
            self.write(provider)
            self.assertFalse(audit_letta(self.storage, {}, chatgpt_sign_in=True).ok)

    def test_store_read_errors_are_structured_violations(self) -> None:
        self.write(self.sign_in())
        with mock.patch.object(
            Path, "read_text", side_effect=PermissionError("denied")
        ):
            self.assertFalse(audit_letta(self.storage, {}).ok)

    def test_endpoint_redirects_in_the_environment_are_refused(self) -> None:
        self.write(self.sign_in())
        for variable in ("OPENAI_BASE_URL", "OPENAI_API_BASE", "ANTHROPIC_BASE_URL"):
            audit = audit_letta(
                self.storage,
                {variable: "https://evil.invalid/v1"},
                chatgpt_sign_in=True,
            )
            self.assertFalse(audit.ok, variable)

    def test_endpoint_redirects_cannot_be_approved(self) -> None:
        self.write(self.sign_in())
        audit = audit_letta(
            self.storage,
            {"OPENAI_BASE_URL": "https://evil.invalid/v1"},
            approved={"OPENAI_BASE_URL"},
            chatgpt_sign_in=True,
        )
        self.assertFalse(audit.ok)

    def test_enabling_sign_in_never_admits_the_codex_client_login(self) -> None:
        self.write(
            record(
                "chatgpt-plus-pro",
                "oauth",
                "chatgpt_oauth",
                clientId="x",
                scopes=["chatgpt.tokens.use.direct"],
            )
        )
        self.assertFalse(audit_letta(self.storage, {}, chatgpt_sign_in=True).ok)

    def test_anthropic_oauth_is_refused_even_with_sign_in_enabled(self) -> None:
        self.write(record("lc-anthropic", "oauth", "anthropic"))
        self.assertFalse(audit_letta(self.storage, {}, chatgpt_sign_in=True).ok)

    def test_credentials_in_the_environment_are_refused(self) -> None:
        for variable in (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_OAUTH_TOKEN",
            "LETTA_API_KEY",
            "AWS_SECRET_ACCESS_KEY",
        ):
            audit = audit_letta(self.storage, {variable: "x"})
            self.assertFalse(audit.ok, variable)
            self.assertIn(variable, audit.violations[0])

    def test_unreadable_store_fails_closed(self) -> None:
        path = self.storage / "providers" / "auth.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json")
        self.assertFalse(audit_letta(self.storage, {}).ok)

    def test_cli_exit_status_and_output(self) -> None:
        self.write(record("chatgpt-plus-pro", "oauth", "chatgpt_oauth"))
        output = io.StringIO()
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            contextlib.redirect_stdout(output),
        ):
            code = cli.main(["supervisor-audit", "--letta-dir", str(self.storage)])
        result = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "violations")
        self.assertNotIn("sk-redacted", output.getvalue())


if __name__ == "__main__":
    unittest.main()
