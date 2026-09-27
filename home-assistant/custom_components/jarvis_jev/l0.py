"""L0 deterministic fast path for unambiguous canonical commands.

Parses a small grammar of smart-home commands directly into a Command
without invoking any model. Anything that does not match the full grammar
returns None so L1 (the decision model) handles it.

Safety comes from three properties: full-string match only, a closed
target vocabulary, and structural validation against TARGETS (the action
must be supported by the resolved target, values must be in range).
"""

from __future__ import annotations

import re

from .const import RESTRICTED_ACTIONS
from .router import (
    _NUMBER,
    COLORS,
    TARGETS,
    Command,
    extract_music_request,
    normalized_text,
)

_POLITE = re.compile(r"^(?:(?:please|hey jarvis)[,\s]+|(?:could|can|would) you\s+)+")
_WS = re.compile(r"\s+")
_END_PUNCT = re.compile(r"[.!?]+$")
_COMPOUND = re.compile(r"[,;]|\band\b|\bthen\b|\bwhile\b")

_AREA = r"(?:kitchen|living room|bedroom|downstairs|govee)"
_ROOM = r"(?:kitchen|living room|bedroom|downstairs)"
_LIGHT = (
    rf"(?:the\s+)?(?:all\s+the\s+)?(?:{_AREA})\s+lights?"
    r"|(?:the\s+)?(?:living|bed|lounge)\s+lights?"
    r"|(?:the\s+)?(?:all\s+the\s+)?govee\s+lamp"
    r"|(?:the\s+)?downstairs\s+lamp"
    r"|(?:the\s+)?bedrooms\s+lights?"
    r"|(?:the\s+)?livingroom\s+lights?"
)
_WIRED = r"(?:the\s+)?stairs\s+lights?"
_SIGN = r"(?:the\s+)?good\s+vibes\s+sign"
_LAMP = r"(?:the\s+)?kitchen\s+mushroom\s+lamp"
_PLUG = r"(?:the\s+)?bedroom\s+(?:mushroom\s+plug|window\s+plug)"
_THERMO = r"(?:the\s+)?(?:living\s+room\s+)?thermostat"
_TEMP_WORD = r"(?:the\s+)?living\s+room\s+temperature"
_MEDIA = (
    r"(?:the\s+)?(?:satellite\s+(?:media\s+player|speaker)"
    r"|(?:downstairs\s+)?music\s+on\s+the\s+satellite\s+speaker"
    r"|music)"
)
_MOVIE = r"(?:movie|film)\s+mode"

_ANY_TARGET = (
    rf"(?:{_LIGHT}|{_WIRED}|{_SIGN}|{_LAMP}|{_PLUG}|{_THERMO}"
    rf"|{_TEMP_WORD}|{_MEDIA}|{_MOVIE})"
)
_LIGHT_TARGET = rf"(?:{_LIGHT}|{_WIRED}|{_SIGN}|{_LAMP}|{_PLUG})"

_ON_OFF = r"(?:turn|switch)"
_COLOR_WORDS = sorted(COLORS, key=len, reverse=True)
_COLOR = "(?:" + "|".join(c.replace("_", r"\s+") for c in _COLOR_WORDS) + ")"
_NUM = r"(-?\d+(?:\.\d+)?)"

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"{_ON_OFF}\s+(on|off)\s+({_ANY_TARGET})"), "onoff_lead"),
    (re.compile(rf"{_ON_OFF}\s+({_ANY_TARGET})\s+(on|off)"), "onoff_trail"),
    (
        re.compile(rf"{_ON_OFF}\s+the\s+lights\s+(on|off)\s+in\s+the\s+({_ROOM})"),
        "onoff_area",
    ),
    (
        re.compile(
            rf"{_ON_OFF}\s+(on|off)\s+the\s+lights?\s+(?:in\s+the\s+)?({_ROOM})"
        ),
        "onoff_area",
    ),
    (re.compile(rf"toggle\s+({_LIGHT_TARGET})"), "toggle"),
    (re.compile(rf"flip\s+({_LIGHT_TARGET})"), "toggle"),
    (re.compile(rf"({_LIGHT_TARGET})\s+(on|off)"), "short_onoff"),
    (
        re.compile(
            rf"(?:kill|cut|black\s+out|douse|snuff\s+out|shut\s+off)\s+({_LIGHT_TARGET})"
        ),
        "off",
    ),
    (re.compile(rf"start\s+({_LIGHT_TARGET})"), "on"),
    (
        re.compile(rf"switch\s+the\s+lights\s+in\s+the\s+({_ROOM})\s+(on|off)"),
        "onoff_area_trail",
    ),
    (
        re.compile(
            rf"(?:set|put)\s+({_LIGHT_TARGET})\s+(?:to|at)\s+{_NUM}\s*(?:percent|%)"
        ),
        "brightness",
    ),
    (
        re.compile(rf"dim\s+({_LIGHT_TARGET})\s+to\s+{_NUM}\s*(?:percent|%)?"),
        "brightness",
    ),
    (re.compile(rf"dim\s+({_LIGHT_TARGET})\s+to\s+(ten)"), "brightness_word"),
    (
        re.compile(
            rf"(?:take|turn)\s+({_LIGHT_TARGET})\s+down\s+to\s+{_NUM}\s*(?:percent|%)"
        ),
        "brightness",
    ),
    (
        re.compile(rf"bring\s+({_LIGHT_TARGET})\s+up\s+to\s+{_NUM}\s*(?:percent|%)"),
        "brightness",
    ),
    (re.compile(rf"(?:brighten|crank)\s+({_LIGHT_TARGET})(?:\s+up)?"), "bright_up"),
    (re.compile(rf"dim\s+({_LIGHT_TARGET})\s+a\s+little"), "bright_down"),
    (
        re.compile(
            rf"(?:set|turn|change|make|paint|color)\s+({_LIGHT_TARGET})\s+(?:to\s+)?({_COLOR})"
        ),
        "color",
    ),
    (
        re.compile(
            rf"(?:set|turn|crank|drop)\s+(?:{_THERMO}|{_TEMP_WORD})\s+to\s+{_NUM}\s*(?:degrees?)?"
        ),
        "temperature",
    ),
    (
        re.compile(rf"(?:{_THERMO}|{_TEMP_WORD})\s+to\s+{_NUM}\s*(?:degrees?)?"),
        "temperature",
    ),
    (
        re.compile(
            r"(?:make\s+the\s+living\s+room\s+warmer|bump\s+the\s+thermostat\s+up\s+a\s+degree|turn\s+up\s+the\s+heat\s+in\s+the\s+living\s+room)"
        ),
        "temp_up",
    ),
    (
        re.compile(
            r"(?:make\s+the\s+living\s+room\s+(?:cooler|colder)|bump\s+the\s+thermostat\s+down\s+a\s+degree|turn\s+down\s+(?:the\s+)?(?:living\s+room\s+thermostat|the\s+heat\s+in\s+the\s+living\s+room))"
        ),
        "temp_down",
    ),
    (re.compile(rf"(?:pause|stop|halt|silence|turn\s+off)\s+({_MEDIA})"), "media"),
    (
        re.compile(
            rf"(?:activate|put\s+on|set\s+the\s+scene\s+to)\s+({_MOVIE})"
            r"(?:\s+in\s+the\s+living\s+room)?"
        ),
        "scene",
    ),
    (re.compile(rf"({_MOVIE})\s+on"), "scene"),
    (
        re.compile(r"(?:play|put\s+on|listen\s+to|i\s+want\s+to\s+hear|hear)\s+(.+)"),
        "play",
    ),
]

_TARGET_KEY: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(?:living|lounge)\s+lights?$"), "living_room_lights"),
    (re.compile(r"^bed\s+lights?$"), "bedroom_lights"),
    (re.compile(r"^(?:all\s+the\s+)?govee\s+lamp$"), "all_govee_lights"),
    (re.compile(r"^downstairs\s+lamp$"), "downstairs_lights"),
    (re.compile(r"^bedrooms\s+lights?$"), "bedroom_lights"),
    (re.compile(r"^livingroom\s+lights?$"), "living_room_lights"),
    (re.compile(rf"^(?:the\s+)?(?:all\s+the\s+)?{_AREA}\s+lights?$"), "area"),
    (re.compile(r"^stairs\s+lights?$"), "stairs_light"),
    (re.compile(r"^good\s+vibes\s+sign$"), "good_vibes_sign"),
    (re.compile(r"^kitchen\s+mushroom\s+lamp$"), "kitchen_mushroom_lamp"),
    (re.compile(r"^bedroom\s+mushroom\s+plug$"), "bedroom_mushroom_plug"),
    (re.compile(r"^bedroom\s+window\s+plug$"), "bedroom_window_plug"),
    (
        re.compile(r"^(?:living\s+room\s+)?(?:thermostat|temperature)$"),
        "living_room_thermostat",
    ),
    (
        re.compile(
            r"^(?:satellite\s+(?:media\s+player|speaker)|(?:downstairs\s+)?music\s+on\s+the\s+satellite\s+speaker|(?:the\s+)?music)$"
        ),
        "satellite_media_player",
    ),
    (re.compile(r"^(?:movie|film)\s+mode$"), "movie_mode"),
]

_AREA_KEY = {
    "kitchen": "kitchen_lights",
    "living room": "living_room_lights",
    "bedroom": "bedroom_lights",
    "downstairs": "downstairs_lights",
    "govee": "all_govee_lights",
}

_COLOR_KEY = {c.replace("_", " "): c for c in COLORS}

_BRIGHTNESS_RANGE = (0.0, 100.0)
_TEMPERATURE_RANGE = (50.0, 90.0)

# Queries naming other home targets stay on their own paths: L0 must not
# claim "put on the lights" or "put on movie mode" (already matched above)
# as a music search.
_HOME_WORDS = re.compile(
    r"lights?|lamp|thermostat|temperature|movie|film|satellite|speaker"
    r"|soundbar|scene|shade|lock|garage|alarm|plug|switch",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    cleaned = _WS.sub(" ", normalized_text(text).lower()).strip()
    cleaned = _END_PUNCT.sub("", cleaned).strip()
    return _POLITE.sub("", cleaned).strip()


def _resolve_target(phrase: str) -> str | None:
    phrase = _WS.sub(" ", phrase).strip()
    phrase = re.sub(r"^(?:all\s+the\s+|the\s+)", "", phrase)
    for pattern, key in _TARGET_KEY:
        if pattern.match(phrase):
            if key == "area":
                area = re.sub(r"^(?:the\s+)?(?:all\s+the\s+)?|\s+lights?$", "", phrase)
                return _AREA_KEY.get(_WS.sub(" ", area).strip())
            return key
    return None


def _single_number(text: str) -> float | None:
    values = [float(m.group(1)) for m in _NUMBER.finditer(text)]
    return values[0] if len(values) == 1 else None


def _in_range(value: float, bounds: tuple[float, float]) -> bool:
    return bounds[0] <= value <= bounds[1]


def parse_canonical(text: str) -> Command | None:
    """Return a Command for unambiguous canonical utterances, else None."""
    cleaned = _clean(text)
    if not cleaned or _COMPOUND.search(cleaned):
        return None
    for pattern, kind in _PATTERNS:
        match = pattern.fullmatch(cleaned)
        if match is None:
            continue
        command = _build(kind, match, cleaned)
        if command is not None:
            if command.action in RESTRICTED_ACTIONS:
                return None
            return command
    return None


def _build(kind: str, match: re.Match[str], cleaned: str) -> Command | None:
    if kind in {"onoff_lead", "onoff_trail"}:
        if kind == "onoff_lead":
            state, phrase = match.group(1), match.group(2)
        else:
            phrase, state = match.group(1), match.group(2)
        return _command(phrase, f"turn_{state}")
    if kind == "onoff_area":
        state, area = match.group(1), match.group(2)
        target = _AREA_KEY.get(area)
        if target is None or f"turn_{state}" not in TARGETS[target].actions:
            return None
        return Command(target, f"turn_{state}", 1.0)
    if kind == "onoff_area_trail":
        area, state = match.group(1), match.group(2)
        target = _AREA_KEY.get(area)
        if target is None or f"turn_{state}" not in TARGETS[target].actions:
            return None
        return Command(target, f"turn_{state}", 1.0)
    if kind == "short_onoff":
        return _command(match.group(1), f"turn_{match.group(2)}")
    if kind == "off":
        return _command(match.group(1), "turn_off")
    if kind == "on":
        return _command(match.group(1), "turn_on")
    if kind == "toggle":
        return _command(match.group(1), "toggle")
    if kind == "brightness":
        value = _single_number(cleaned)
        if value is None or not _in_range(value, _BRIGHTNESS_RANGE):
            return None
        command = _command(match.group(1), "set_brightness")
        if command is None:
            return None
        return Command(command.target, command.action, 1.0, value=value)
    if kind == "brightness_word":
        command = _command(match.group(1), "set_brightness")
        if command is None:
            return None
        return Command(command.target, command.action, 1.0, value=10.0)
    if kind == "color":
        color = _COLOR_KEY.get(_WS.sub(" ", match.group(2)).strip())
        if color is None:
            return None
        command = _command(match.group(1), "set_color")
        if command is None:
            return None
        return Command(command.target, command.action, 1.0, color=color)
    if kind in {"bright_up", "bright_down"}:
        action = (
            "adjust_brightness_up" if kind == "bright_up" else "adjust_brightness_down"
        )
        return _command(match.group(1), action)
    if kind == "temperature":
        value = _single_number(cleaned)
        if value is None or not _in_range(value, _TEMPERATURE_RANGE):
            return None
        target = _resolve_target("thermostat")
        if target is None or "set_temperature" not in TARGETS[target].actions:
            return None
        return Command(target, "set_temperature", 1.0, value=value)
    if kind in {"temp_up", "temp_down"}:
        action = (
            "adjust_temperature_up" if kind == "temp_up" else "adjust_temperature_down"
        )
        return Command("living_room_thermostat", action, 1.0)
    if kind == "media":
        target = _resolve_target(match.group(1))
        if target != "satellite_media_player":
            return None
        return Command(target, "pause_media", 1.0)
    if kind == "scene":
        return Command("movie_mode", "activate_scene", 1.0)
    if kind == "play":
        music = extract_music_request(cleaned)
        if music is None:
            return None
        if _HOME_WORDS.search(music[1]):
            return None
        return Command("satellite_media_player", "play_music", 1.0)
    return None


def _command(phrase: str, action: str) -> Command | None:
    target = _resolve_target(phrase)
    if target is None or action not in TARGETS[target].actions:
        return None
    return Command(target, action, 1.0)
