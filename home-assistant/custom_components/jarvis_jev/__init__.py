from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, Literal

from aiohttp import web
from homeassistant.components import conversation
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from .const import (
    API_URL,
    CONF_FALLBACK_AGENT,
    DOMAIN,
    FALLBACK_TIMEOUT_SECONDS,
    OLLAMA_FALLBACK_AGENT,
    REQUEST_TIMEOUT_SECONDS,
    SHADOW_TIMEOUT_SECONDS,
    SHADOW_URL,
)
from .fallback import (
    DelegationGuard,
    fallback_rejection,
    known_conversation_agent_ids,
    own_conversation_entity_ids,
)
from .l0 import parse_canonical
from .router import TARGETS, Command, build_request, decide
from .shadow import ShadowResult, compare_answers, request_shadow

JEV_REQUESTS = Counter(
    "jarvis_jev_requests_total", "Jev routing outcomes.", ("outcome",)
)
JEV_LATENCY = Histogram(
    "jarvis_jev_request_latency_seconds", "Hosted Jev request latency."
)
SERVICE_LATENCY = Histogram(
    "jarvis_ha_service_call_latency_seconds",
    "Service-call latency for commands executed by Jarvis Jev.",
    ("domain", "service"),
)


class JarvisMetricsView(HomeAssistantView):
    url = "/api/jarvis_jev/metrics"
    name = "api:jarvis_jev:metrics"
    requires_auth = True

    async def get(self, _request: web.Request) -> web.Response:
        headers = {"Content-Type": CONTENT_TYPE_LATEST}
        body = await asyncio.to_thread(generate_latest)
        return web.Response(body=body, headers=headers)


_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.version == 1:
        data = {**entry.data, CONF_FALLBACK_AGENT: OLLAMA_FALLBACK_AGENT}
        hass.config_entries.async_update_entry(entry, data=data, version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not hass.data.get(f"{DOMAIN}_metrics_registered"):
        hass.http.register_view(JarvisMetricsView)
        hass.data[f"{DOMAIN}_metrics_registered"] = True
    agent = JarvisJevAgent(hass, entry)
    conversation.async_set_agent(hass, entry, agent)
    entry.async_on_unload(lambda: conversation.async_unset_agent(hass, entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return True


class JarvisJevAgent(conversation.AbstractConversationAgent):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._shadow_active = False
        self._delegation_guard = DelegationGuard()

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return MATCH_ALL

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        command = parse_canonical(user_input.text)
        if command is not None:
            try:
                await _execute(self.hass, user_input, command)
            except Exception:  # noqa: BLE001
                JEV_REQUESTS.labels("l0_service_error").inc()
                return _speech(user_input, "The home action failed.")
            JEV_REQUESTS.labels("l0_execute").inc()
            return _speech(user_input, "Done.")

        api_key = os.environ.get("TYPESAFE_API_KEY")
        if not api_key:
            JEV_REQUESTS.labels("missing_api_key").inc()
            return _speech(user_input, "Jev is unavailable.")

        request = build_request(user_input.text)
        request_id = uuid.uuid4().hex
        shadow_task = self._start_shadow(request)

        try:
            with JEV_LATENCY.time():
                async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                    async with async_get_clientsession(self.hass).post(
                        API_URL,
                        headers={"Authorization": f"Bearer {api_key}"},
                        json=request,
                    ) as response:
                        if response.status != 200:
                            JEV_REQUESTS.labels(f"http_{response.status}").inc()
                            self._schedule_shadow_observation(
                                request_id, shadow_task, None
                            )
                            return _speech(user_input, "Jev is unavailable.")
                        payload = await response.json()
        except Exception:  # noqa: BLE001
            JEV_REQUESTS.labels("request_error").inc()
            self._schedule_shadow_observation(request_id, shadow_task, None)
            return _speech(user_input, "Jev is unavailable.")

        self._schedule_shadow_observation(request_id, shadow_task, payload)

        decision = decide(user_input.text, payload)
        if decision.route == "fallback":
            return await self._async_delegate_fallback(user_input)
        if decision.route != "execute" or decision.command is None:
            JEV_REQUESTS.labels(decision.route).inc()
            return _speech(
                user_input, decision.speech or "I could not safely route that."
            )

        try:
            await _execute(self.hass, user_input, decision.command)
        except Exception:  # noqa: BLE001
            JEV_REQUESTS.labels("service_error").inc()
            return _speech(user_input, "The home action failed.")
        JEV_REQUESTS.labels("execute").inc()
        return _speech(user_input, "Done.")

    async def _async_delegate_fallback(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        fallback_agent = self.entry.options.get(
            CONF_FALLBACK_AGENT, self.entry.data.get(CONF_FALLBACK_AGENT, "")
        )
        rejection = fallback_rejection(
            fallback_agent=fallback_agent,
            entry_id=self.entry.entry_id,
            unique_id=self.entry.unique_id,
            own_entity_ids=own_conversation_entity_ids(self.hass, self.entry),
            known_agent_ids=known_conversation_agent_ids(self.hass),
        )
        if rejection is not None:
            JEV_REQUESTS.labels(
                "general_disabled" if rejection == "disabled" else "general_rejected"
            ).inc()
            if rejection != "disabled":
                _LOGGER.warning(
                    "jarvis_jev fallback rejected reason=%s", rejection
                )
            return _speech(user_input, "General conversation is unavailable.")
        if not self._delegation_guard.acquire(user_input.conversation_id):
            JEV_REQUESTS.labels("general_rejected").inc()
            _LOGGER.warning("jarvis_jev fallback delegation depth exceeded")
            return _speech(user_input, "General conversation is unavailable.")
        try:
            JEV_REQUESTS.labels("general_delegate").inc()
            async with asyncio.timeout(FALLBACK_TIMEOUT_SECONDS):
                return await conversation.async_converse(
                    self.hass,
                    text=user_input.text,
                    conversation_id=user_input.conversation_id,
                    context=user_input.context,
                    language=user_input.language,
                    agent_id=fallback_agent,
                    device_id=user_input.device_id,
                    satellite_id=user_input.satellite_id,
                    extra_system_prompt=user_input.extra_system_prompt,
                )
        except Exception:  # noqa: BLE001
            JEV_REQUESTS.labels("general_error").inc()
            return _speech(
                user_input,
                "General conversation is unavailable.",
            )
        finally:
            self._delegation_guard.release(user_input.conversation_id)

    def _start_shadow(
        self, request: dict[str, Any]
    ) -> asyncio.Task[ShadowResult] | None:
        if os.environ.get("JARVIS_SHADOW_ENABLED", "0") != "1":
            return None
        if self._shadow_active:
            _LOGGER.info("jarvis_shadow skipped reason=busy")
            return None
        self._shadow_active = True

        async def run() -> ShadowResult:
            try:
                return await request_shadow(
                    async_get_clientsession(self.hass),
                    SHADOW_URL,
                    request,
                    SHADOW_TIMEOUT_SECONDS,
                )
            finally:
                self._shadow_active = False

        return self.hass.async_create_task(run(), "local decision shadow")

    def _schedule_shadow_observation(
        self,
        request_id: str,
        task: asyncio.Task[ShadowResult] | None,
        authoritative: Any,
    ) -> None:
        if task is None:
            return
        self.hass.async_create_task(
            self._observe_shadow(request_id, task, authoritative),
            "observe local decision shadow",
        )

    async def _observe_shadow(
        self,
        request_id: str,
        task: asyncio.Task[ShadowResult],
        authoritative: Any,
    ) -> None:
        result = await task
        if result.error is not None:
            _LOGGER.info(
                "jarvis_shadow id=%s result=error error=%s latency_seconds=%.6f",
                request_id,
                result.error,
                result.latency_seconds,
            )
            return
        agreed, compared, all_agree = compare_answers(authoritative, result.payload)
        _LOGGER.info(
            "jarvis_shadow id=%s result=ok agreed=%d compared=%d all_agree=%s latency_seconds=%.6f",
            request_id,
            agreed,
            compared,
            str(all_agree).lower(),
            result.latency_seconds,
        )


async def _execute(
    hass: HomeAssistant,
    user_input: conversation.ConversationInput,
    command: Command,
) -> None:
    target = TARGETS[command.target]
    domain = target.entity_id.split(".", 1)[0]
    service = command.action
    data: dict[str, Any] = {}

    if command.action == "toggle":
        domain, service = "homeassistant", "toggle"
    elif command.action in {"turn_on", "turn_off"}:
        service = command.action
    elif command.action == "set_brightness":
        domain, service = "light", "turn_on"
        data["brightness_pct"] = command.value
    elif command.action in {"adjust_brightness_up", "adjust_brightness_down"}:
        domain, service = "light", "turn_on"
        data["brightness_step_pct"] = 30 if command.action.endswith("up") else -30
    elif command.action == "set_color":
        domain, service = "light", "turn_on"
        if command.color == "neon_pink":
            data["rgb_color"] = [255, 16, 240]
        elif command.color == "warm_white":
            data["color_temp_kelvin"] = 3000
        else:
            data["color_name"] = command.color.replace("_", " ")
    elif command.action == "activate_scene":
        domain, service = "scene", "turn_on"
    elif command.action == "pause_media":
        domain, service = "media_player", "media_pause"
    elif command.action == "set_temperature":
        domain, service = "climate", "set_temperature"
        _validate_temperature(hass, target.entity_id, command.value)
        data["temperature"] = command.value
    elif command.action in {"adjust_temperature_up", "adjust_temperature_down"}:
        domain, service = "climate", "set_temperature"
        state = hass.states.get(target.entity_id)
        if state is None or not isinstance(
            state.attributes.get("temperature"), (int, float)
        ):
            raise ValueError("thermostat temperature is unavailable")
        value = float(state.attributes["temperature"]) + (
            1 if command.action.endswith("up") else -1
        )
        _validate_temperature(hass, target.entity_id, value)
        data["temperature"] = value
    else:
        raise ValueError("unsupported action")

    with SERVICE_LATENCY.labels(domain, service).time():
        await hass.services.async_call(
            domain,
            service,
            service_data=data,
            target={"entity_id": target.entity_id},
            blocking=True,
            context=user_input.context,
        )


def _validate_temperature(
    hass: HomeAssistant, entity_id: str, value: float | None
) -> None:
    state = hass.states.get(entity_id)
    if state is None or value is None:
        raise ValueError("thermostat state is unavailable")
    minimum = state.attributes.get("min_temp")
    maximum = state.attributes.get("max_temp")
    if not isinstance(minimum, (int, float)) or not isinstance(maximum, (int, float)):
        raise TypeError("thermostat range is unavailable")
    if not float(minimum) <= value <= float(maximum):
        raise ValueError("temperature is outside the thermostat range")


def _speech(
    user_input: conversation.ConversationInput, text: str
) -> conversation.ConversationResult:
    response = intent.IntentResponse(language=user_input.language)
    response.async_set_speech(text)
    return conversation.ConversationResult(
        response=response,
        conversation_id=user_input.conversation_id,
    )
