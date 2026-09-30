from __future__ import annotations

import re

REDACTED = "[REDACTED]"

_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_AUTHORIZATION = re.compile(r"(?i)\b(authorization\s*:\s*)[^\r\n]*")
_API_KEY_HEADER = re.compile(r"(?i)\b((?:x-)?api[-_ ]key\s*:\s*)[^\s\r\n]+")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z][A-Z0-9_]*(?:TOKEN|PASSWORD|PASSWD|SECRET|API_KEY|ACCESS_KEY|CREDENTIAL)[A-Z0-9_]*|"
    r"token|password|secret|api_key)\s*=\s*([^\s'\"]+|\"[^\"]*\"|'[^']*')"
)
_SECRET_ARGUMENT = re.compile(
    r"(?i)(--(?:token|password|passwd|secret|api[-_]key|access[-_]key|credential)"
    r"(?:=|\s+))(\"[^\"]*\"|'[^']*'|[^\s]+)"
)
_MACHINE_CREDENTIAL_PATH = re.compile(
    r"(?<![A-Za-z0-9_./-])"
    r"(?:~|\$HOME|\$\{HOME\}|/home/[^/\s]+|/Users/[^/\s]+|/root)"
    r"/\.(?:ssh(?:/[^\s'\"]*)?|config/sops/age(?:/[^\s'\"]*)?|kube(?:/config)?|"
    r"aws/credentials|docker/config\.json|gnupg(?:/[^\s'\"]*)?)"
    r"(?=$|[\s'\",;])"
)


def redact(value: str) -> str:
    """Return deterministic text with common credentials and local credential paths removed."""
    value = _PRIVATE_KEY.sub(REDACTED, value)
    value = _AUTHORIZATION.sub(r"\1" + REDACTED, value)
    value = _API_KEY_HEADER.sub(r"\1" + REDACTED, value)
    value = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}={REDACTED}", value)
    value = _SECRET_ARGUMENT.sub(r"\1" + REDACTED, value)
    return _MACHINE_CREDENTIAL_PATH.sub(REDACTED, value)
