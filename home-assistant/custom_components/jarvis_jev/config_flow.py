from __future__ import annotations

import os
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .const import CONF_FALLBACK_AGENT, DOMAIN, OLLAMA_FALLBACK_AGENT
from .fallback import (
    fallback_rejection,
    known_conversation_agent_ids,
    own_conversation_entity_ids,
)


class JarvisJevConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not os.environ.get("TYPESAFE_API_KEY"):
            return self.async_abort(reason="missing_api_key")
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title="Jarvis Jev Router",
            data={CONF_FALLBACK_AGENT: OLLAMA_FALLBACK_AGENT},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return JarvisJevOptionsFlow(config_entry)


class JarvisJevOptionsFlow(OptionsFlow):
    """Configure the general-conversation fallback with recursion validation.

    An empty value keeps general conversation disabled. Any other value must
    resolve to a registered agent that is not this entry under any identity.
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        current = self._config_entry.options.get(
            CONF_FALLBACK_AGENT,
            self._config_entry.data.get(CONF_FALLBACK_AGENT, ""),
        )
        if user_input is not None:
            candidate = str(user_input.get(CONF_FALLBACK_AGENT, "") or "").strip()
            rejection = fallback_rejection(
                fallback_agent=candidate,
                entry_id=self._config_entry.entry_id,
                unique_id=self._config_entry.unique_id,
                own_entity_ids=own_conversation_entity_ids(
                    self.hass, self._config_entry
                ),
                known_agent_ids=known_conversation_agent_ids(self.hass),
            )
            if rejection in (None, "disabled"):
                return self.async_create_entry(
                    title="", data={CONF_FALLBACK_AGENT: candidate}
                )
            errors[CONF_FALLBACK_AGENT] = rejection
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Required(CONF_FALLBACK_AGENT, default=current): str}
            ),
            errors=errors,
        )
