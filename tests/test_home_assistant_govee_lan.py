from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPONENT_DIR = REPO_ROOT / "home-assistant" / "custom_components" / "govee_lan"


def _stub(name: str, **attrs: object) -> None:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module


def _install_stubs() -> None:
    if "voluptuous" in sys.modules:
        return

    class _Marker:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def __hash__(self) -> int:
            return id(self)

    _stub(
        "voluptuous",
        Schema=_Marker,
        Required=_Marker,
        Optional=_Marker,
        All=_Marker,
        Range=_Marker,
    )

    class PlatformNotReady(Exception):
        pass

    class _PlatformSchema:
        def extend(self, _schema: object) -> str:
            return "platform-schema"

    class LightEntity:
        @property
        def is_on(self) -> bool | None:
            return getattr(self, "_attr_is_on", None)

        def async_write_ha_state(self) -> None:
            self._state_writes = getattr(self, "_state_writes", 0) + 1

    _stub("homeassistant")
    _stub("homeassistant.components")

    class ColorMode:
        RGB = "rgb"
        COLOR_TEMP = "color_temp"

    _stub(
        "homeassistant.components.light",
        ATTR_BRIGHTNESS="brightness",
        ATTR_COLOR_TEMP_KELVIN="color_temp_kelvin",
        ATTR_RGB_COLOR="rgb_color",
        PLATFORM_SCHEMA=_PlatformSchema(),
        ColorMode=ColorMode,
        LightEntity=LightEntity,
    )
    _stub("homeassistant.exceptions", PlatformNotReady=PlatformNotReady)
    _stub("homeassistant.helpers")
    _stub("homeassistant.helpers.config_validation", string=str)


_install_stubs()


pkg = types.ModuleType("govee_lan")
pkg.__path__ = [str(COMPONENT_DIR)]
sys.modules["govee_lan"] = pkg


def _load(name: str):
    fullname = f"govee_lan.{name}"
    spec = importlib.util.spec_from_file_location(
        fullname, COMPONENT_DIR / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[fullname] = module
    spec.loader.exec_module(module)
    return module


init_mod = _load("__init__")
pkg.__dict__.update(
    {k: v for k, v in init_mod.__dict__.items() if not k.startswith("__")}
)
light_mod = _load("light")
PlatformNotReady = sys.modules["homeassistant.exceptions"].PlatformNotReady


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
        self.sent.append((data, addr))

    def close(self) -> None:
        pass


class FakeHub:
    def __init__(self, query_result: dict | None = None) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.query_result = query_result
        self.queries: list[str] = []

    def send(self, host: str, message: dict) -> None:
        self.sent.append((host, message))

    async def query(self, host: str) -> dict | None:
        self.queries.append(host)
        return self.query_result


def payloads(sent: list[tuple[str, dict]]) -> list[dict]:
    return [message for _, message in sent]


class BuildersTests(unittest.TestCase):
    def test_turn(self) -> None:
        self.assertEqual(
            init_mod.turn_message(True), {"msg": {"cmd": "turn", "data": {"value": 1}}}
        )
        self.assertEqual(
            init_mod.turn_message(False), {"msg": {"cmd": "turn", "data": {"value": 0}}}
        )

    def test_brightness_clamps(self) -> None:
        self.assertEqual(
            init_mod.brightness_message(150)["msg"]["data"], {"value": 100}
        )
        self.assertEqual(init_mod.brightness_message(0)["msg"]["data"], {"value": 1})

    def test_color_forces_rgb_mode(self) -> None:
        self.assertEqual(
            init_mod.color_rgb_message(255, 0, 128)["msg"],
            {
                "cmd": "colorwc",
                "data": {"color": {"r": 255, "g": 0, "b": 128}, "colorTemInKelvin": 0},
            },
        )

    def test_color_temp_clamps(self) -> None:
        self.assertEqual(
            init_mod.color_temp_message(99999)["msg"]["data"]["colorTemInKelvin"], 6500
        )
        self.assertEqual(
            init_mod.color_temp_message(1)["msg"]["data"]["colorTemInKelvin"], 2700
        )
        data = init_mod.color_temp_message(4000)["msg"]["data"]
        self.assertEqual(data["color"], {"r": 0, "g": 0, "b": 0})

    def test_brightness_conversions_round_trip(self) -> None:
        self.assertEqual(init_mod.pct_to_ha_brightness(100), 255)
        self.assertEqual(init_mod.ha_brightness_to_pct(255), 100)
        self.assertEqual(init_mod.ha_brightness_to_pct(128), 50)


class ParseTests(unittest.TestCase):
    def test_compact_rgb_reply(self) -> None:
        raw = '{"msg":{"cmd":"devStatus","data":{"onOff":1,"brightness":100,"color":{"r":255,"g":255,"b":255},"colorTemInKelvin":0}}}'
        self.assertEqual(
            init_mod.parse_devstatus_message(json.loads(raw)),
            {"on": True, "brightness": 100, "rgb": (255, 255, 255), "kelvin": 0},
        )

    def test_cct_mode(self) -> None:
        raw = '{"msg":{"cmd":"devStatus","data":{"onOff":0,"brightness":50,"color":{"r":0,"g":0,"b":0},"colorTemInKelvin":2700}}}'
        self.assertEqual(
            init_mod.parse_devstatus_message(json.loads(raw)),
            {"on": False, "brightness": 50, "rgb": (0, 0, 0), "kelvin": 2700},
        )

    def test_malformed_returns_none(self) -> None:
        self.assertIsNone(init_mod.parse_devstatus_message({}))
        self.assertIsNone(init_mod.parse_devstatus_message({"msg": {}}))
        self.assertIsNone(init_mod.parse_devstatus_message(None))


class HubTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_resolves_on_reply(self) -> None:
        hub = init_mod.GoveeLanHub()
        hub._transport = FakeTransport()
        task = asyncio.ensure_future(hub.query("10.0.20.166"))
        await asyncio.sleep(0)
        reply = b'{"msg":{"cmd":"devStatus","data":{"onOff":1,"brightness":80,"color":{"r":1,"g":2,"b":3},"colorTemInKelvin":0}}}'
        hub.handle_reply("10.0.20.166", reply)
        self.assertEqual((await task)["brightness"], 80)
        sent = json.loads(hub._transport.sent[0][0].decode())
        self.assertEqual(sent["msg"]["cmd"], "devStatus")
        self.assertEqual(hub._transport.sent[0][1], ("10.0.20.166", 4003))

    async def test_reply_from_other_host_ignored(self) -> None:
        hub = init_mod.GoveeLanHub()
        hub._transport = FakeTransport()
        task = asyncio.ensure_future(hub.query("10.0.20.166"))
        await asyncio.sleep(0)
        reply = b'{"msg":{"cmd":"devStatus","data":{"onOff":1,"brightness":80,"color":{"r":1,"g":2,"b":3},"colorTemInKelvin":0}}}'
        hub.handle_reply("10.0.20.169", reply)
        self.assertFalse(task.done())
        task.cancel()

    async def test_garbage_reply_resolves_none(self) -> None:
        hub = init_mod.GoveeLanHub()
        hub._transport = FakeTransport()
        task = asyncio.ensure_future(hub.query("10.0.20.166"))
        await asyncio.sleep(0)
        hub.handle_reply("10.0.20.166", b"not json")
        self.assertIsNone(await task)


class LightTests(unittest.IsolatedAsyncioTestCase):
    async def test_turn_on_sends_turn_then_attrs(self) -> None:
        hub = FakeHub()
        light = light_mod.GoveeLanLight("kitchen", "Kitchen", "10.0.20.167", hub)
        await light.async_turn_on(brightness=128, rgb_color=(255, 0, 0))
        cmds = [m["msg"]["cmd"] for m in payloads(hub.sent)]
        self.assertEqual(cmds, ["turn", "brightness", "colorwc"])
        self.assertEqual(hub.sent[0][1]["msg"]["data"], {"value": 1})
        self.assertTrue(light.is_on)
        self.assertEqual(light._attr_brightness, 128)
        self.assertEqual(light._attr_rgb_color, (255, 0, 0))
        self.assertEqual(light._attr_color_mode, "rgb")

    async def test_turn_on_color_temp(self) -> None:
        hub = FakeHub()
        light = light_mod.GoveeLanLight("kitchen", "Kitchen", "10.0.20.167", hub)
        await light.async_turn_on(color_temp_kelvin=4000)
        self.assertEqual(payloads(hub.sent)[1]["msg"]["data"]["colorTemInKelvin"], 4000)
        self.assertEqual(light._attr_color_mode, "color_temp")

    async def test_turn_on_while_on_skips_turn(self) -> None:
        hub = FakeHub()
        light = light_mod.GoveeLanLight("kitchen", "Kitchen", "10.0.20.167", hub)
        light._attr_is_on = True
        await light.async_turn_on(brightness=255)
        cmds = [m["msg"]["cmd"] for m in payloads(hub.sent)]
        self.assertEqual(cmds, ["brightness"])

    async def test_turn_off(self) -> None:
        hub = FakeHub()
        light = light_mod.GoveeLanLight("kitchen", "Kitchen", "10.0.20.167", hub)
        light._attr_is_on = True
        await light.async_turn_off()
        self.assertEqual(payloads(hub.sent)[0]["msg"]["data"], {"value": 0})
        self.assertFalse(light.is_on)

    async def test_update_applies_rgb_state(self) -> None:
        hub = FakeHub({"on": True, "brightness": 80, "rgb": (1, 2, 3), "kelvin": 0})
        light = light_mod.GoveeLanLight("kitchen", "Kitchen", "10.0.20.167", hub)
        await light.async_update()
        self.assertTrue(light.is_on)
        self.assertEqual(light._attr_brightness, 204)
        self.assertEqual(light._attr_rgb_color, (1, 2, 3))
        self.assertEqual(light._attr_color_mode, "rgb")
        self.assertTrue(light._attr_available)

    async def test_update_applies_cct_state(self) -> None:
        hub = FakeHub({"on": True, "brightness": 100, "rgb": (0, 0, 0), "kelvin": 2700})
        light = light_mod.GoveeLanLight("kitchen", "Kitchen", "10.0.20.167", hub)
        await light.async_update()
        self.assertEqual(light._attr_color_mode, "color_temp")
        self.assertEqual(light._attr_color_temp_kelvin, 2700)

    async def test_three_missed_polls_mark_unavailable(self) -> None:
        hub = FakeHub(None)
        light = light_mod.GoveeLanLight("kitchen", "Kitchen", "10.0.20.167", hub)
        await light.async_update()
        await light.async_update()
        self.assertTrue(light._attr_available)
        await light.async_update()
        self.assertFalse(light._attr_available)
        hub.query_result = {
            "on": True,
            "brightness": 100,
            "rgb": (1, 1, 1),
            "kelvin": 0,
        }
        await light.async_update()
        self.assertTrue(light._attr_available)

    async def test_setup_platform_bind_failure_retries(self) -> None:
        added: list = []
        hass = SimpleNamespace(data={})

        class FailingHub(init_mod.GoveeLanHub):
            async def async_bind(self) -> None:
                raise OSError(98, "Address in use")

        original = light_mod.GoveeLanHub
        light_mod.GoveeLanHub = FailingHub
        try:
            with self.assertRaises(PlatformNotReady):
                await light_mod.async_setup_platform(hass, {"lights": {}}, added.append)
        finally:
            light_mod.GoveeLanHub = original

    async def test_setup_platform_adds_entities(self) -> None:
        added: list = []
        hass = SimpleNamespace(data={})

        class BoundHub(init_mod.GoveeLanHub):
            async def async_bind(self) -> None:
                pass

        original = light_mod.GoveeLanHub
        light_mod.GoveeLanHub = BoundHub
        try:
            await light_mod.async_setup_platform(
                hass,
                {"lights": {"kitchen": {"name": "Kitchen", "host": "10.0.20.167"}}},
                lambda entities: added.extend(entities),
            )
        finally:
            light_mod.GoveeLanHub = original
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]._attr_unique_id, "govee_lan_kitchen")


class ManifestTests(unittest.TestCase):
    def test_manifest(self) -> None:
        manifest = json.loads((COMPONENT_DIR / "manifest.json").read_text())
        self.assertEqual(manifest["domain"], "govee_lan")
        self.assertFalse(manifest["config_flow"])
        self.assertEqual(manifest["iot_class"], "local_polling")


if __name__ == "__main__":
    unittest.main()
