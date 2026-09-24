"""Tests for Jev general-conversation fallback validation (B10).

Pure decision logic is tested directly. The agent delegation branch in
``__init__`` is exercised behaviorally with stubbed Home Assistant modules:
self/alias/unknown fallbacks are rejected, one in-flight delegation per
conversation is enforced, and delegation carries a total deadline. Local
intents, allowlisted actions, confidence gates, and disabled-by-default
general conversation are preserved by the router tests.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "home-assistant" / "custom_components" / "jarvis_jev"


def _load_pure(name: str):
    spec = importlib.util.spec_from_file_location(
        f"jarvis_jev_pure_{name}", PACKAGE_DIR / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fallback = _load_pure("fallback")
const = _load_pure("const")


class FallbackRejectionTest(unittest.TestCase):
    def test_empty_fallback_stays_disabled(self) -> None:
        for value in ("", "   "):
            self.assertEqual(
                fallback.fallback_rejection(
                    fallback_agent=value,
                    entry_id="entry-1",
                    unique_id="unique-1",
                    own_entity_ids=frozenset({"conversation.jarvis_jev"}),
                    known_agent_ids={"conversation.other"},
                ),
                "disabled",
            )

    def test_entry_and_unique_ids_are_self(self) -> None:
        for value in ("entry-1", "unique-1"):
            self.assertEqual(
                fallback.fallback_rejection(
                    fallback_agent=value,
                    entry_id="entry-1",
                    unique_id="unique-1",
                    own_entity_ids=frozenset(),
                    known_agent_ids=None,
                ),
                "self",
            )

    def test_own_conversation_entity_is_self_alias(self) -> None:
        self.assertEqual(
            fallback.fallback_rejection(
                fallback_agent="conversation.jarvis_jev",
                entry_id="entry-1",
                unique_id="unique-1",
                own_entity_ids=frozenset({"conversation.jarvis_jev"}),
                known_agent_ids={"conversation.jarvis_jev", "conversation.other"},
            ),
            "self_alias",
        )

    def test_unregistered_agent_is_rejected(self) -> None:
        self.assertEqual(
            fallback.fallback_rejection(
                fallback_agent="conversation.ghost",
                entry_id="entry-1",
                unique_id="unique-1",
                own_entity_ids=frozenset(),
                known_agent_ids={"conversation.other", "entry-9"},
            ),
            "unknown_agent",
        )

    def test_registered_other_agent_is_allowed(self) -> None:
        for candidate, known in (
            ("conversation.other", {"conversation.other"}),
            ("entry-9", {"entry-9"}),
        ):
            self.assertIsNone(
                fallback.fallback_rejection(
                    fallback_agent=candidate,
                    entry_id="entry-1",
                    unique_id="unique-1",
                    own_entity_ids=frozenset({"conversation.jarvis_jev"}),
                    known_agent_ids=known,
                )
            )

    def test_unknown_check_skipped_when_registry_unreadable(self) -> None:
        self.assertIsNone(
            fallback.fallback_rejection(
                fallback_agent="conversation.maybe",
                entry_id="entry-1",
                unique_id="unique-1",
                own_entity_ids=frozenset(),
                known_agent_ids=None,
            )
        )

    def test_fallback_deadline_is_configured(self) -> None:
        self.assertGreaterEqual(
            const.FALLBACK_TIMEOUT_SECONDS, const.REQUEST_TIMEOUT_SECONDS
        )
        self.assertLessEqual(const.FALLBACK_TIMEOUT_SECONDS, 30.0)


class DelegationGuardTest(unittest.TestCase):
    def test_second_in_flight_delegation_is_refused(self) -> None:
        guard = fallback.DelegationGuard()
        self.assertTrue(guard.acquire("conv-1"))
        self.assertFalse(guard.acquire("conv-1"))
        guard.release("conv-1")
        self.assertTrue(guard.acquire("conv-1"))
        guard.release("conv-1")

    def test_conversations_are_independent(self) -> None:
        guard = fallback.DelegationGuard()
        self.assertTrue(guard.acquire("conv-1"))
        self.assertTrue(guard.acquire("conv-2"))
        guard.release("conv-1")
        guard.release("conv-2")

    def test_release_without_acquire_is_safe(self) -> None:
        guard = fallback.DelegationGuard()
        guard.release("conv-9")
        self.assertTrue(guard.acquire("conv-9"))


def _install_ha_stubs() -> dict:
    """Install stub HA/aiohttp/prometheus modules; return the fake converse state."""
    state: dict = {"converse_calls": [], "converse_behavior": "ok"}

    aiohttp = types.ModuleType("aiohttp")
    web = types.ModuleType("aiohttp.web")

    class _Request:  # pragma: no cover
        pass

    class _Response:
        def __init__(self, body=None, headers=None) -> None:
            self.body = body
            self.headers = headers

    web.Request = _Request
    web.Response = _Response
    aiohttp.web = web

    ha = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    conversation = types.ModuleType("homeassistant.components.conversation")

    class AbstractConversationAgent:
        pass

    class ConversationInput:
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    class ConversationResult:
        def __init__(self, response=None, conversation_id=None) -> None:
            self.response = response
            self.conversation_id = conversation_id

    async def async_converse(hass, text, agent_id=None, **kwargs):
        state["converse_calls"].append(agent_id)
        behavior = state["converse_behavior"]
        if behavior == "ok":
            return ConversationResult(response=f"via {agent_id}")
        if behavior == "recurse":
            inner = ConversationInput(
                text=text,
                conversation_id=kwargs.get("conversation_id"),
                context=None,
                language="en",
                device_id=None,
                satellite_id=None,
                extra_system_prompt=None,
            )
            return await state["agent"]._async_delegate_fallback(inner)
        if behavior == "hang":
            await asyncio.sleep(30.0)
        raise AssertionError(f"unknown converse behavior {behavior!r}")

    conversation.AbstractConversationAgent = AbstractConversationAgent
    conversation.ConversationInput = ConversationInput
    conversation.ConversationResult = ConversationResult
    conversation.async_converse = async_converse
    conversation.async_set_agent = lambda *a: None
    conversation.async_unset_agent = lambda *a: None

    http = types.ModuleType("homeassistant.components.http")

    class HomeAssistantView:  # pragma: no cover
        pass

    http.HomeAssistantView = HomeAssistantView

    config_entries = types.ModuleType("homeassistant.config_entries")

    class ConfigEntry:
        def __init__(self, entry_id="entry-1", data=None, options=None) -> None:
            self.entry_id = entry_id
            self.unique_id = "unique-1"
            self.data = data or {}
            self.options = options or {}

        def async_on_unload(self, _func) -> None:  # pragma: no cover
            pass

    config_entries.ConfigEntry = ConfigEntry
    config_entries.ConfigFlow = object
    config_entries.ConfigFlowResult = dict
    config_entries.OptionsFlow = object

    const_mod = types.ModuleType("homeassistant.const")
    const_mod.MATCH_ALL = "*"

    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object

    helpers = types.ModuleType("homeassistant.helpers")
    intent_mod = types.ModuleType("homeassistant.helpers.intent")

    class IntentResponse:
        def __init__(self, language=None) -> None:
            self.language = language
            self.speech_text = ""

        def async_set_speech(self, text) -> None:
            self.speech_text = text

    intent_mod.IntentResponse = IntentResponse

    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")

    class _FakePostResponse:
        status = 200

        def __init__(self, payload) -> None:
            self._payload = payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def json(self):
            return self._payload

    class _FakeSession:
        def __init__(self, payload) -> None:
            self._payload = payload

        def post(self, *args, **kwargs):
            return _FakePostResponse(self._payload)

    def _general_payload() -> dict:
        return {
            "model": "jev-1.13.0",
            "answers": {
                "request_kind": {
                    "type": "choice",
                    "choice": "general_or_conversation",
                    "confidence": 0.99,
                },
                "target": {
                    "type": "choice",
                    "choice": "none_or_unknown",
                    "confidence": 0.99,
                },
                "action": {
                    "type": "choice",
                    "choice": "none_or_unsupported",
                    "confidence": 0.99,
                },
                "reference": {
                    "type": "choice",
                    "choice": "explicit_target",
                    "confidence": 0.99,
                },
                "color": {
                    "type": "choice",
                    "choice": "none_or_unknown",
                    "confidence": 0.99,
                },
            },
        }

    aiohttp_client.async_get_clientsession = lambda hass: _FakeSession(
        _general_payload()
    )

    prometheus = types.ModuleType("prometheus_client")
    prometheus.CONTENT_TYPE_LATEST = "text/plain"

    class _Metric:
        def __init__(self, *args, **kwargs) -> None:
            self.calls: list = []

        def labels(self, *args):
            parent = self

            class _Bound:
                def inc(self, amount=1.0) -> None:
                    parent.calls.append(args)

                def time(self):
                    import contextlib

                    return contextlib.nullcontext()

            return _Bound()

        def time(self):
            import contextlib

            return contextlib.nullcontext()

    prometheus.Counter = _Metric
    prometheus.Histogram = _Metric
    prometheus.generate_latest = lambda: b""

    for name, module in {
        "aiohttp": aiohttp,
        "aiohttp.web": web,
        "homeassistant": ha,
        "homeassistant.components": components,
        "homeassistant.components.conversation": conversation,
        "homeassistant.components.http": http,
        "homeassistant.config_entries": config_entries,
        "homeassistant.const": const_mod,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.intent": intent_mod,
        "homeassistant.helpers.aiohttp_client": aiohttp_client,
        "prometheus_client": prometheus,
    }.items():
        sys.modules[name] = module
    return state


def _load_agent_init():
    package = types.ModuleType("jarvis_jev_agent")
    package.__path__ = [str(PACKAGE_DIR)]
    sys.modules["jarvis_jev_agent"] = package
    for name in ("const", "router", "l0", "shadow", "fallback"):
        spec = importlib.util.spec_from_file_location(
            f"jarvis_jev_agent.{name}", PACKAGE_DIR / f"{name}.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"jarvis_jev_agent.{name}"] = module
        spec.loader.exec_module(module)
    spec = importlib.util.spec_from_file_location(
        "jarvis_jev_agent.agent_init", PACKAGE_DIR / "__init__.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "jarvis_jev_agent"
    sys.modules["jarvis_jev_agent.agent_init"] = module
    spec.loader.exec_module(module)
    return module


class AgentDelegationTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.state = _install_ha_stubs()
        cls.agent_init = _load_agent_init()
        cls._saved_key = os.environ.get("TYPESAFE_API_KEY")
        os.environ["TYPESAFE_API_KEY"] = "test-key"

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._saved_key is None:
            os.environ.pop("TYPESAFE_API_KEY", None)
        else:
            os.environ["TYPESAFE_API_KEY"] = cls._saved_key

    def setUp(self) -> None:
        self.state["converse_calls"] = []
        self.state["converse_behavior"] = "ok"
        self.state["agent"] = None
        entry = sys.modules["homeassistant.config_entries"].ConfigEntry(
            entry_id="entry-1", data={"fallback_agent": ""}
        )
        self.entry = entry
        self.agent = self.agent_init.JarvisJevAgent(
            hass=types.SimpleNamespace(data={}), entry=entry
        )
        self.state["agent"] = self.agent
        self.agent_init.own_conversation_entity_ids = lambda hass, entry: set()
        self.agent_init.known_conversation_agent_ids = lambda hass: None

    def _input(self, text: str, conversation_id: str = "conv-1"):
        conversation = sys.modules["homeassistant.components.conversation"]
        return conversation.ConversationInput(
            text=text,
            conversation_id=conversation_id,
            context=None,
            language="en",
            device_id=None,
            satellite_id=None,
            extra_system_prompt=None,
        )

    def _speech(self, result) -> str:
        return result.response.speech_text

    async def test_disabled_fallback_never_delegates(self) -> None:
        result = await self.agent.async_process(
            self._input("how many ounces are in a cup")
        )
        self.assertEqual(self._speech(result), "General conversation is unavailable.")
        self.assertEqual(self.state["converse_calls"], [])

    async def test_self_entry_id_is_rejected(self) -> None:
        self.entry.data["fallback_agent"] = "entry-1"
        result = await self.agent.async_process(
            self._input("how many ounces are in a cup")
        )
        self.assertEqual(self._speech(result), "General conversation is unavailable.")
        self.assertEqual(self.state["converse_calls"], [])

    async def test_self_alias_entity_is_rejected(self) -> None:
        self.entry.data["fallback_agent"] = "conversation.jarvis_jev"
        self.agent_init.own_conversation_entity_ids = lambda hass, entry: {
            "conversation.jarvis_jev"
        }
        result = await self.agent.async_process(
            self._input("how many ounces are in a cup")
        )
        self.assertEqual(self._speech(result), "General conversation is unavailable.")
        self.assertEqual(self.state["converse_calls"], [])

    async def test_unknown_agent_is_rejected(self) -> None:
        self.entry.data["fallback_agent"] = "conversation.ghost"
        self.agent_init.known_conversation_agent_ids = lambda hass: {
            "conversation.other"
        }
        result = await self.agent.async_process(
            self._input("how many ounces are in a cup")
        )
        self.assertEqual(self._speech(result), "General conversation is unavailable.")
        self.assertEqual(self.state["converse_calls"], [])

    async def test_registered_agent_is_delegated(self) -> None:
        self.entry.data["fallback_agent"] = "conversation.other"
        self.agent_init.known_conversation_agent_ids = lambda hass: {
            "conversation.other"
        }
        result = await self.agent.async_process(
            self._input("how many ounces are in a cup")
        )
        self.assertEqual(self.state["converse_calls"], ["conversation.other"])
        self.assertEqual(result.response, "via conversation.other")

    async def test_reentrant_delegation_is_bounded(self) -> None:
        self.entry.data["fallback_agent"] = "conversation.other"
        self.state["converse_behavior"] = "recurse"
        result = await self.agent.async_process(
            self._input("how many ounces are in a cup")
        )
        self.assertEqual(self.state["converse_calls"], ["conversation.other"])
        self.assertEqual(self._speech(result), "General conversation is unavailable.")

    async def test_delegation_enforces_total_deadline(self) -> None:
        self.entry.data["fallback_agent"] = "conversation.other"
        self.state["converse_behavior"] = "hang"
        self.agent_init.FALLBACK_TIMEOUT_SECONDS = 0.05
        try:
            result = await self.agent.async_process(
                self._input("how many ounces are in a cup")
            )
        finally:
            self.agent_init.FALLBACK_TIMEOUT_SECONDS = 10.0
        self.assertEqual(self._speech(result), "General conversation is unavailable.")
        self.assertEqual(self.state["converse_calls"], ["conversation.other"])

    async def test_options_override_data_fallback(self) -> None:
        self.entry.data["fallback_agent"] = "entry-1"
        self.entry.options["fallback_agent"] = "conversation.other"
        self.agent_init.known_conversation_agent_ids = lambda hass: {
            "conversation.other"
        }
        result = await self.agent.async_process(
            self._input("how many ounces are in a cup")
        )
        self.assertEqual(self.state["converse_calls"], ["conversation.other"])
        self.assertEqual(result.response, "via conversation.other")


if __name__ == "__main__":
    unittest.main()
