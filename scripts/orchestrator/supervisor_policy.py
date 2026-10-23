"""Preflight for the Letta supervisor's model access.

Letta Code 0.34.x offers two ChatGPT logins (docs/architecture/persistent-agent.md):
- "ChatGPT Plus/Pro" (`chatgpt-plus-pro`, types `chatgpt_oauth` and
  `openai-codex`) logs in under the Codex CLI's OAuth client and calls
  ChatGPT's backend API. It is never allowed.
- "OpenAI (ChatGPT subscription)" (`lc-openai`, type `openai`) is OpenAI's
  documented Sign in with ChatGPT flow: a dynamically issued client, the
  `chatgpt.tokens.use.direct` scope, and the public Responses API. It is
  allowed only when the operator passes `chatgpt_sign_in=True`.
Claude subscription credentials are not permitted outside Anthropic's own
applications, so any other OAuth login is refused. Other providers need an
explicit approval and must use an API key.

Letta stores local provider credentials in `<storage>/providers/auth.json`
as `{"version": 1, "providers": {name: record}}`; `record.auth.type` is
"oauth" or "api". Provider API keys can also arrive through the environment,
which is checked too. Credential values are never echoed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUBSCRIPTION_PROVIDERS = frozenset(
    {"chatgpt-plus-pro", "chatgpt_oauth", "openai-codex"}
)
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
REGISTRATION_CLIENT_ID = "dynamic_agent_client"
SIGN_IN_SCOPE = "chatgpt.tokens.use.direct"
OPENAI_API = "https://api.openai.com/v1"
SIGN_IN_PROVIDER = "lc-openai"
# Any of these would let Letta send the sign-in token somewhere else.
ENDPOINT_ENV = re.compile(r"(_BASE_URL|_API_BASE|_ENDPOINT|_API_URL)$")
CREDENTIAL_ENV = re.compile(
    r"(_API_KEY|_AUTH_TOKEN|_OAUTH_TOKEN|_ACCESS_KEY_ID|_SECRET_ACCESS_KEY)$"
    r"|^(LETTA_API_KEY|GOOGLE_APPLICATION_CREDENTIALS|AWS_PROFILE)$"
)


@dataclass(frozen=True)
class SupervisorAudit:
    status: str
    providers: tuple[str, ...]
    violations: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.violations


def sign_in_problems(name: str, record: dict[str, Any]) -> list[str]:
    """Why a record is not OpenAI's documented Sign in with ChatGPT, if it isn't."""
    auth = record.get("auth") or {}
    problems = []
    if name != SIGN_IN_PROVIDER:
        problems.append(f"provider {name} is not Letta's {SIGN_IN_PROVIDER} sign-in")
    if record.get("provider_type") != "openai":
        problems.append(f"provider {name} is not the OpenAI provider")
    client = auth.get("clientId")
    if not isinstance(client, str) or not client.strip():
        problems.append(f"provider {name} has no issued client ID")
    elif client in {CODEX_CLIENT_ID, REGISTRATION_CLIENT_ID}:
        problems.append(f"provider {name} uses a client ID that is not its own")
    scopes = auth.get("scopes")
    if not isinstance(scopes, list) or SIGN_IN_SCOPE not in scopes:
        problems.append(f"provider {name} lacks the {SIGN_IN_SCOPE} grant")
    base = record.get("base_url")
    if base not in (None, "", OPENAI_API):
        problems.append(f"provider {name} overrides the required {OPENAI_API} endpoint")
    return problems


def audit_letta(
    storage: Path,
    env: Mapping[str, str],
    approved: Collection[str] = (),
    chatgpt_sign_in: bool = False,
) -> SupervisorAudit:
    """Classify Letta's configured inference as unconfigured, approved or not."""
    violations: list[str] = []
    providers: list[str] = []
    sign_ins = 0
    auth_path = storage / "providers" / "auth.json"
    if auth_path.exists():
        try:
            document = json.loads(auth_path.read_text())
            records = document.get("providers") or {}
            if not isinstance(records, dict):
                raise ValueError("providers is not an object")
        except (OSError, ValueError, AttributeError):
            violations.append(f"{auth_path} is unreadable or malformed")
            records = {}
        for name, record in sorted(records.items()):
            providers.append(name)
            if not isinstance(record, dict):
                violations.append(f"provider {name} is malformed")
                continue
            auth = record.get("auth") or {}
            if not isinstance(auth, dict):
                violations.append(f"provider {name} has malformed authentication")
                continue
            kind = record.get("provider_type")
            if name in SUBSCRIPTION_PROVIDERS or kind in SUBSCRIPTION_PROVIDERS:
                violations.append(f"provider {name} is the Codex-client ChatGPT login")
            elif auth.get("type") == "oauth":
                problems = sign_in_problems(name, record)
                if not chatgpt_sign_in:
                    violations.append(
                        f"provider {name} uses an OAuth login and ChatGPT sign-in "
                        "is not enabled"
                    )
                elif problems:
                    violations.extend(problems)
                else:
                    sign_ins += 1
            elif auth.get("type") != "api":
                violations.append(f"provider {name} does not use an API key")
            elif name not in approved:
                violations.append(f"provider {name} is not approved")
    if sign_ins > 1:
        violations.append("more than one ChatGPT sign-in is configured")
    for variable in sorted(env):
        if CREDENTIAL_ENV.search(variable) and variable not in approved:
            violations.append(
                f"environment variable {variable} carries a model credential"
            )
        elif ENDPOINT_ENV.search(variable):
            violations.append(f"environment variable {variable} redirects a provider")
    if violations:
        status = "violations"
    elif sign_ins:
        status = "chatgpt-sign-in"
    elif providers:
        status = "approved"
    else:
        status = "unconfigured"
    return SupervisorAudit(status, tuple(providers), tuple(violations))
