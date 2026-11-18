from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import yaml


class SecretTag:
    """Represents a Home Assistant !secret reference."""

    def __init__(self, value: str) -> None:
        self.value = str(value)

    def __repr__(self) -> str:
        return f"SecretTag({self.value!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SecretTag):
            return self.value == other.value
        return False

    def __hash__(self) -> int:
        return hash(("!secret", self.value))


class IncludeTag:
    """Represents a Home Assistant !include reference."""

    def __init__(self, value: str) -> None:
        self.value = str(value)

    def __repr__(self) -> str:
        return f"IncludeTag({self.value!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, IncludeTag):
            return self.value == other.value
        return False

    def __hash__(self) -> int:
        return hash(("!include", self.value))


class IncludeDirTag:
    """Represents a Home Assistant !include_dir_merge_named reference."""

    def __init__(self, value: str) -> None:
        self.value = str(value)

    def __repr__(self) -> str:
        return f"IncludeDirTag({self.value!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, IncludeDirTag):
            return self.value == other.value
        return False

    def __hash__(self) -> int:
        return hash(("!include_dir_merge_named", self.value))


class DuplicateKeySafeLoader(yaml.SafeLoader):
    """YAML safe loader that strictly rejects duplicate keys in mappings."""

    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[Any, Any]:
        if not isinstance(node, yaml.MappingNode):
            raise ValueError(f"Expected a mapping node, but found {node.id}")
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                hash(key)
            except TypeError as exc:
                raise ValueError(
                    f"Unhashable key {key} at {key_node.start_mark}"
                ) from exc
            if key in mapping:
                raise ValueError(
                    f"Duplicate YAML key: '{key}' at line {key_node.start_mark.line + 1}"
                )
            value = self.construct_object(value_node, deep=deep)
            mapping[key] = value
        return mapping


def _secret_constructor(loader: yaml.BaseLoader, node: yaml.Node) -> SecretTag:
    scalar = loader.construct_scalar(node)  # type: ignore[arg-type]
    return SecretTag(str(scalar))


def _include_constructor(loader: yaml.BaseLoader, node: yaml.Node) -> IncludeTag:
    scalar = loader.construct_scalar(node)  # type: ignore[arg-type]
    return IncludeTag(str(scalar))


def _include_dir_constructor(loader: yaml.BaseLoader, node: yaml.Node) -> IncludeDirTag:
    scalar = loader.construct_scalar(node)  # type: ignore[arg-type]
    return IncludeDirTag(str(scalar))


DuplicateKeySafeLoader.add_constructor("!secret", _secret_constructor)
DuplicateKeySafeLoader.add_constructor("!include", _include_constructor)
DuplicateKeySafeLoader.add_constructor(
    "!include_dir_merge_named", _include_dir_constructor
)


class CanonicalSafeDumper(yaml.SafeDumper):
    """YAML dumper with clean multiline formatting and custom tag support."""

    def choose_scalar_style(self) -> str:
        tag = getattr(self.event, "tag", None)
        if tag in ("!secret", "!include", "!include_dir_merge_named"):
            return ""
        return super().choose_scalar_style()

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        super().increase_indent(flow=flow, indentless=False)


def _secret_representer(dumper: yaml.SafeDumper, data: SecretTag) -> yaml.ScalarNode:
    return dumper.represent_scalar("!secret", data.value)


def _include_representer(dumper: yaml.SafeDumper, data: IncludeTag) -> yaml.ScalarNode:
    return dumper.represent_scalar("!include", data.value)


def _include_dir_representer(
    dumper: yaml.SafeDumper, data: IncludeDirTag
) -> yaml.ScalarNode:
    return dumper.represent_scalar("!include_dir_merge_named", data.value)


def _str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


CanonicalSafeDumper.add_representer(SecretTag, _secret_representer)
CanonicalSafeDumper.add_representer(IncludeTag, _include_representer)
CanonicalSafeDumper.add_representer(IncludeDirTag, _include_dir_representer)
CanonicalSafeDumper.add_representer(str, _str_representer)


def parse_yaml(content: str) -> Any:
    """Parse YAML with duplicate key rejection and custom tag preservation."""
    return yaml.load(content, Loader=DuplicateKeySafeLoader)


def dump_yaml(data: Any) -> str:
    """Dump data to clean, deterministic YAML."""
    ordered = _order_mapping_keys(data)
    return yaml.dump(
        ordered,
        Dumper=CanonicalSafeDumper,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )


PREFERRED_KEY_ORDER: tuple[str, ...] = (
    "id",
    "alias",
    "name",
    "description",
    "mode",
    "url_path",
    "title",
    "icon",
    "require_admin",
    "show_in_sidebar",
    "views",
    "triggers",
    "trigger",
    "conditions",
    "condition",
    "actions",
    "action",
    "entities",
)


def _order_mapping_keys(obj: Any, *, keep_order: bool = False) -> Any:
    """Recursively order mapping keys. Never reorder lists/arrays.

    Variables mappings keep their order: HA renders them in sequence.
    """
    if isinstance(obj, dict):
        keys = list(obj.keys())
        if keep_order:
            ordered_keys = keys
        else:
            # Sort keys with preferred order first, then alphabetical
            preferred = [k for k in PREFERRED_KEY_ORDER if k in obj]
            rest = sorted([k for k in keys if k not in PREFERRED_KEY_ORDER])
            ordered_keys = preferred + rest
        return {
            k: _order_mapping_keys(obj[k], keep_order=k == "variables")
            for k in ordered_keys
        }
    if isinstance(obj, list):
        return [_order_mapping_keys(item) for item in obj]
    return obj


VOLATILE_FIELDS = frozenset(
    {
        "last_triggered",
        "modified_at",
        "created_at",
        "last_updated",
        "last_changed",
        "time_fired",
    }
)


def strip_volatile(obj: Any, path: tuple[str, ...] = ()) -> Any:
    """Remove volatile runtime timestamps and identifiers from configuration.

    Path-aware: only removes runtime volatile fields at top level (path == ()),
    preserving arbitrary user configuration such as nested variables, context,
    action payloads, and custom card fields.
    """
    if isinstance(obj, dict):
        result: dict[str, Any] = {}
        is_entity_state = len(path) == 0 and "entity_id" in obj and "state" in obj
        for k, v in obj.items():
            if len(path) == 0 and k in VOLATILE_FIELDS:
                continue
            if is_entity_state and k == "context":
                continue
            result[k] = strip_volatile(v, path + (str(k),))
        return result
    if isinstance(obj, list):
        return [
            strip_volatile(item, path + (str(idx),)) for idx, item in enumerate(obj)
        ]
    return obj


def to_json_compatible(obj: Any) -> Any:
    """Convert objects to JSON-serializable types, preserving tags and key order.

    Key order is semantic in HA: a variables step renders its keys in order, so
    a later variable can read an earlier one. Hashing sorts keys separately.
    """
    if isinstance(obj, SecretTag):
        return {"!secret": obj.value}
    if isinstance(obj, IncludeTag):
        return {"!include": obj.value}
    if isinstance(obj, IncludeDirTag):
        return {"!include_dir_merge_named": obj.value}
    if isinstance(obj, dict):
        return {k: to_json_compatible(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_json_compatible(item) for item in obj]
    return obj


def from_json_compatible(obj: Any) -> Any:
    """Restore SecretTag and IncludeTag from JSON-serialized representation."""
    if isinstance(obj, dict):
        if len(obj) == 1 and "!secret" in obj:
            return SecretTag(str(obj["!secret"]))
        if len(obj) == 1 and "!include" in obj:
            return IncludeTag(str(obj["!include"]))
        if len(obj) == 1 and "!include_dir_merge_named" in obj:
            return IncludeDirTag(str(obj["!include_dir_merge_named"]))
        return {k: from_json_compatible(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [from_json_compatible(item) for item in obj]
    return obj


_to_json_compatible = to_json_compatible


def canonical_json(data: Any) -> str:
    """Produce deterministic JSON representation with sorted keys."""
    clean = strip_volatile(data)
    json_ready = to_json_compatible(clean)
    return json.dumps(
        json_ready, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def canonical_hash(data: Any) -> str:
    """Compute SHA-256 hash of canonical representation."""
    payload = canonical_json(data).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def compute_baseline_hash(resources: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 hash of baseline resources."""
    cleaned: dict[str, Any] = {}
    for k in sorted(resources.keys()):
        rdata = resources[k]
        if isinstance(rdata, dict):
            desired = rdata.get("desired", rdata)
        else:
            desired = rdata
        cleaned[k] = strip_volatile(desired)
    payload = canonical_json(cleaned).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


SECRET_PATTERNS = [
    re.compile(r"ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9._-]{10,}"),  # JWT / Bearer token
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # API keys
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),  # Private keys
    re.compile(r"://[^:/@\s]+:[^/@\s]+@"),  # URL with user:password
    re.compile(
        r"(?i)(password|secret|api_key|apikey|client_secret|credential|access_token|private_key)\s*[:=]\s*['\"]([^'\"]{8,})['\"]"
    ),
]

AUTH_HEADER_PATTERN = re.compile(
    r"(Bearer\s+|token\s+|token=)[A-Za-z0-9._-]{10,}", re.IGNORECASE
)

URL_SECRET_PATTERN = re.compile(
    r"([?&](?:api_key|apikey|key|token|access_token|secret|password|client_secret)=)([^&\s]+)",
    re.IGNORECASE,
)

SENSITIVE_KEY_TERMS: tuple[str, ...] = (
    "password",
    "secret",
    "token",
    "auth_key",
    "bearer",
    "authorization",
    "api_key",
    "apikey",
    "client_secret",
    "credential",
    "credentials",
    "access_token",
    "private_key",
    "passphrase",
)


def detect_secrets(obj: Any, path: str = "") -> list[str]:
    """Check configuration data for potential plaintext credentials."""
    findings: list[str] = []

    if isinstance(obj, SecretTag):
        return findings

    if isinstance(obj, str):
        for pattern in SECRET_PATTERNS:
            match = pattern.search(obj)
            if match:
                findings.append(
                    f"Potential credential found at {path or 'root'}: pattern match"
                )
                break
        if URL_SECRET_PATTERN.search(obj):
            findings.append(
                f"Potential credential found at {path or 'root'}: sensitive parameter in URL"
            )
    elif isinstance(obj, dict):
        for k, v in obj.items():
            sub_path = f"{path}.{k}" if path else str(k)
            k_lower = str(k).lower()
            if any(term in k_lower for term in SENSITIVE_KEY_TERMS):
                if isinstance(v, str) and not isinstance(v, SecretTag) and len(v) > 0:
                    findings.append(
                        f"Sensitive key '{sub_path}' contains unreferenced plaintext string"
                    )
            findings.extend(detect_secrets(v, sub_path))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            sub_path = f"{path}[{idx}]"
            findings.extend(detect_secrets(item, sub_path))

    return findings


def redact(text: str) -> str:
    """Sanitize sensitive strings from text."""
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    text = AUTH_HEADER_PATTERN.sub(r"\1[REDACTED]", text)
    text = URL_SECRET_PATTERN.sub(r"\1[REDACTED]", text)
    return text


def sanitize_error(error: Any) -> str:
    """Scrub sensitive tokens, headers, and credentials from exception or error strings."""
    return redact(str(error))
