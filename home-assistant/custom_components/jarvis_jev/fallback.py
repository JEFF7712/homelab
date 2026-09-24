"""General-conversation fallback delegation guard for the Jev agent.

General conversation is a separate trust domain and stays disabled until a
fallback passes its own exposure, prompt, history, privacy, and recursion
evaluation. When a fallback is configured, delegation must never recurse
into this same agent under another identity, must stay depth-bounded, and
must carry a total deadline (applied by the caller with
``FALLBACK_TIMEOUT_SECONDS``).

This module has no Home Assistant imports so the decision logic is directly
unit-testable; the thin hass-dependent resolvers below use lazy imports.
"""

from __future__ import annotations

from typing import Any

MAX_DELEGATION_DEPTH = 1

# Rejection reasons returned by fallback_rejection. All values are safe to
# expose in logs; only aggregates become metric labels.
DISABLED = "disabled"
SELF = "self"
SELF_ALIAS = "self_alias"
UNKNOWN_AGENT = "unknown_agent"


def fallback_rejection(
    *,
    fallback_agent: str,
    entry_id: str,
    unique_id: str | None,
    own_entity_ids: set[str] | frozenset[str],
    known_agent_ids: set[str] | frozenset[str] | None,
) -> str | None:
    """Return None when delegation may proceed, else a rejection reason.

    ``own_entity_ids`` holds this entry's conversation entity ids (aliases
    that would resolve back to this same agent). ``known_agent_ids`` holds
    every registered agent identity (conversation entity ids plus config
    entry ids); None skips the registration check when the registry is
    unavailable, in which case an unresolvable target still fails safely at
    delegation time.
    """
    candidate = (fallback_agent or "").strip()
    if not candidate:
        return DISABLED
    if candidate == entry_id:
        return SELF
    if unique_id and candidate == unique_id:
        return SELF
    if candidate in own_entity_ids:
        return SELF_ALIAS
    if known_agent_ids is not None and candidate not in known_agent_ids:
        return UNKNOWN_AGENT
    return None


class DelegationGuard:
    """Bound re-entrant fallback delegation per conversation.

    A delegated turn that routes back into this agent (same conversation id)
    is refused once the in-flight depth reaches the bound, which caps hosted
    request loops from a later fallback configuration.
    """

    def __init__(self, max_depth: int = MAX_DELEGATION_DEPTH) -> None:
        self._max_depth = max_depth
        self._active: dict[str, int] = {}

    def acquire(self, conversation_id: str | None) -> bool:
        key = conversation_id or ""
        depth = self._active.get(key, 0)
        if depth >= self._max_depth:
            return False
        self._active[key] = depth + 1
        return True

    def release(self, conversation_id: str | None) -> None:
        key = conversation_id or ""
        depth = self._active.get(key, 0)
        if depth <= 1:
            self._active.pop(key, None)
        else:
            self._active[key] = depth - 1


def own_conversation_entity_ids(hass: Any, entry: Any) -> set[str]:
    """Conversation entity ids owned by this config entry (lazy HA import)."""
    try:
        from homeassistant.helpers import entity_registry as entity_registry_module

        registry = entity_registry_module.async_get(hass)
        return {
            entity.entity_id
            for entity in registry.entities.values()
            if entity.config_entry_id == entry.entry_id
            and entity.entity_id.split(".", 1)[0] == "conversation"
        }
    except Exception:
        return set()


def known_conversation_agent_ids(hass: Any) -> set[str] | None:
    """Registered agent identities, or None when the registries are unreadable."""
    try:
        from homeassistant.helpers import entity_registry as entity_registry_module

        registry = entity_registry_module.async_get(hass)
        entity_ids = {
            entity.entity_id
            for entity in registry.entities.values()
            if entity.entity_id.split(".", 1)[0] == "conversation"
        }
        entry_ids = {entry.entry_id for entry in hass.config_entries.async_entries()}
        return entity_ids | entry_ids
    except Exception:
        return None
