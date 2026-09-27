from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .const import (
    CLARIFY_THRESHOLD,
    CLIMATE_THRESHOLD,
    LIGHT_THRESHOLD,
    MODEL,
    RESTRICTED_ACTIONS,
)


@dataclass(frozen=True)
class Target:
    entity_id: str
    actions: frozenset[str]


@dataclass(frozen=True)
class Command:
    target: str
    action: str
    confidence: float
    value: float | None = None
    color: str = "none_or_unknown"


@dataclass(frozen=True)
class Decision:
    route: str
    command: Command | None = None
    speech: str | None = None


TARGETS = {
    "downstairs_lights": Target(
        "light.downstairs_lights",
        frozenset(
            {
                "turn_on",
                "turn_off",
                "toggle",
                "set_brightness",
                "adjust_brightness_up",
                "adjust_brightness_down",
                "set_color",
            }
        ),
    ),
    "kitchen_lights": Target(
        "light.kitchen_lights",
        frozenset(
            {
                "turn_on",
                "turn_off",
                "toggle",
                "set_brightness",
                "adjust_brightness_up",
                "adjust_brightness_down",
                "set_color",
            }
        ),
    ),
    "living_room_lights": Target(
        "light.living_room_lights",
        frozenset(
            {
                "turn_on",
                "turn_off",
                "toggle",
                "set_brightness",
                "adjust_brightness_up",
                "adjust_brightness_down",
                "set_color",
            }
        ),
    ),
    "bedroom_lights": Target(
        "light.bedroom_roku_lights",
        frozenset(
            {
                "turn_on",
                "turn_off",
                "toggle",
                "set_brightness",
                "adjust_brightness_up",
                "adjust_brightness_down",
                "set_color",
            }
        ),
    ),
    "all_govee_lights": Target(
        "light.all_govee_lights",
        frozenset(
            {
                "turn_on",
                "turn_off",
                "toggle",
                "set_brightness",
                "adjust_brightness_up",
                "adjust_brightness_down",
                "set_color",
            }
        ),
    ),
    "kitchen_mushroom_lamp": Target(
        "light.kitchen_mushroom_lamp", frozenset({"turn_on", "turn_off", "toggle"})
    ),
    "bedroom_window_plug": Target(
        "light.bedroom_window_plug", frozenset({"turn_on", "turn_off", "toggle"})
    ),
    "bedroom_mushroom_plug": Target(
        "light.bedroom_mushroom_plug", frozenset({"turn_on", "turn_off", "toggle"})
    ),
    "good_vibes_sign": Target(
        "light.living_room_good_vibes_sign",
        frozenset({"turn_on", "turn_off", "toggle"}),
    ),
    "stairs_light": Target(
        "switch.stairs_light", frozenset({"turn_on", "turn_off", "toggle"})
    ),
    "living_room_thermostat": Target(
        "climate.living_room_ac_living_room_ac_thermostat",
        frozenset(
            {"set_temperature", "adjust_temperature_up", "adjust_temperature_down"}
        ),
    ),
    "satellite_media_player": Target(
        "media_player.homelab_05_satellite_media_player_2",
        frozenset({"pause_media", "play_music"}),
    ),
    "movie_mode": Target("scene.movie_low_living_room", frozenset({"activate_scene"})),
}

COLORS = frozenset(
    {
        "white",
        "red",
        "orange",
        "yellow",
        "green",
        "blue",
        "purple",
        "pink",
        "neon_pink",
        "warm_white",
    }
)

_NUMBER = re.compile(
    r"(?<![\w.])(-?\d+(?:\.\d+)?)(?:\s*(?:degrees?|percent)|\s*%|\s*°[fc]?)?(?![\w.])",
    re.IGNORECASE,
)
_SPEAKER = re.compile(r"^speaker\s+[^\s]+\s+", re.IGNORECASE)
_COMPOUND_COMMAND = re.compile(r";|\band\b|\bthen\b|\bwhile\b")

_TARGET_EVIDENCE = {
    "downstairs_lights": re.compile(
        r"\bdownstairs (?:lights?|lamp)\b|\blights?\s+downstairs\b"
    ),
    "kitchen_lights": re.compile(
        r"(?:\bkitchen lights?\b|\blights?\s+(?:in\s+the\s+)?kitchen\b)"
        r"(?!\s+fixture)"
    ),
    "living_room_lights": re.compile(
        r"\b(?:living room lights?|living lights?|lounge lights?)\b"
        r"|\blights?\s+(?:in\s+the\s+)?living\s+room\b"
    ),
    "bedroom_lights": re.compile(
        r"\b(?:bedroom lights?|bed lights?)\b"
        r"|\blights?\s+(?:in\s+the\s+)?bedroom\b"
    ),
    "all_govee_lights": re.compile(r"\b(?:all (?:the )?)?govee (?:lights?|lamp)\b"),
    "kitchen_mushroom_lamp": re.compile(r"\bkitchen mushroom lamp\b"),
    "bedroom_window_plug": re.compile(r"\bbedroom window plug\b"),
    "bedroom_mushroom_plug": re.compile(r"\bbedroom mushroom plug\b"),
    "good_vibes_sign": re.compile(r"\bgood vibes sign\b"),
    "stairs_light": re.compile(r"\bstairs lights?\b"),
    "living_room_thermostat": re.compile(
        r"\b(?:living room )?(?:thermostat|temperature)\b|\bheat in the living room\b"
    ),
    "satellite_media_player": re.compile(
        r"\bsatellite (?:media player|speaker)\b|\bmusic (?:on|from) the satellite speaker\b"
        r"|\bmusic\b"
    ),
    "movie_mode": re.compile(r"\b(?:movie|film) mode\b"),
}

_ACTION_EVIDENCE = {
    "turn_on": re.compile(r"\bon\b"),
    "turn_off": re.compile(
        r"\boff\b|\bkill\b|\bblack out\b|\bdouse\b|\bsnuff out\b|\b(?:power|shut) down\b"
    ),
    "toggle": re.compile(r"\b(?:toggle|flip)\b"),
    "set_brightness": re.compile(
        r"\b(?:dim|set|put|take|bring|turn)\b.*\b(?:to|at)\b.*\b\d+(?:\.\d+)?\b"
    ),
    "adjust_brightness_up": re.compile(r"\b(?:brighten|crank)\b|\bbring\b.*\bup\b"),
    "adjust_brightness_down": re.compile(r"\bdim\b|\blower\b|\bturn\b.*\bdown\b"),
    "set_color": re.compile(r"\b(?:set|turn|change|make|paint|color)\b"),
    "set_temperature": re.compile(r"\b(?:set|turn|crank|drop)\b.*\b(?:to|at)\b"),
    "adjust_temperature_up": re.compile(
        r"\bwarmer\b|\bbump\b.*\bup\b|\bturn up the heat\b"
    ),
    "adjust_temperature_down": re.compile(
        r"\bcooler\b|\bbump\b.*\bdown\b|\bturn down the heat\b"
    ),
    "activate_scene": re.compile(r"\bactivate\b|\b(?:movie|film) mode on\b"),
    "pause_media": re.compile(r"\b(?:pause|stop|halt|silence)\b"),
}


def normalized_text(text: str) -> str:
    return _SPEAKER.sub("", text.strip(), count=1)


_MUSIC_SPEAKER_PREFIX = re.compile(r"^\s*speaker\s+([^\s]+)\s+", re.IGNORECASE)
_MUSIC_DEVICE_SUFFIX = re.compile(
    r"\s+on\s+(the\s+)?(speaker|soundbar|satellite(\s+speaker)?)\s*$",
    re.IGNORECASE,
)
_MUSIC_TRAILING_ON = re.compile(r"\s+on\s*$", re.IGNORECASE)
_MUSIC_LEAD_SONG = re.compile(
    r"^(?:play|put\s+on|listen\s+to)\s+(?:the\s+song|a\s+song|song)\s+",
    re.IGNORECASE,
)
_MUSIC_LEAD_ARTIST = re.compile(
    r"^(?:play\s+some|play\s+the\s+artist|play\s+music\s+by"
    r"|put\s+on\s+some|put\s+some|listen\s+to)\s+",
    re.IGNORECASE,
)
_MUSIC_LEAD_BARE = re.compile(
    r"^(?:play|put\s+on|i\s+want\s+to\s+hear|hear)\s+", re.IGNORECASE
)
_MUSIC_ACTION_WORD = re.compile(
    r"\b(?:play|put\s+(?:on|some)|listen\s+to|hear)\b", re.IGNORECASE
)
_MUSIC_SONG_BY_ARTIST = re.compile(r"\S\s+by\s+\S", re.IGNORECASE)


def extract_music_request(text: str) -> tuple[str, str, str] | None:
    """Split a music utterance into (media_type, query, speaker).

    Mirrors the voice music automation: a trailing device reference is
    stripped, "play some X" style requests resolve as artists and bare
    "play X" as free-text track search. Returns None when the text is not
    a playable music request.
    """
    rest = text or ""
    speaker = ""
    prefix = _MUSIC_SPEAKER_PREFIX.match(rest)
    if prefix:
        speaker = prefix.group(1)
        rest = rest[prefix.end() :]
    rest = rest.strip()
    if not rest or _COMPOUND_COMMAND.search(rest):
        return None
    if not _MUSIC_ACTION_WORD.search(rest):
        return None
    if _MUSIC_LEAD_SONG.match(rest):
        media_type = "music"
        query = _MUSIC_LEAD_SONG.sub("", rest, count=1)
    elif _MUSIC_LEAD_ARTIST.match(rest):
        media_type = "artist"
        query = _MUSIC_LEAD_ARTIST.sub("", rest, count=1)
        query = _MUSIC_TRAILING_ON.sub("", query)
        if _MUSIC_SONG_BY_ARTIST.search(query):
            media_type = "music"
    elif _MUSIC_LEAD_BARE.match(rest):
        media_type = "music"
        query = _MUSIC_LEAD_BARE.sub("", rest, count=1)
    else:
        return None
    query = _MUSIC_DEVICE_SUFFIX.sub("", query).strip()
    if not query:
        return None
    return (media_type, query, speaker)


def _has_command_evidence(text: str, target: str, action: str, color: str) -> bool:
    normalized = normalized_text(text).lower()
    if _COMPOUND_COMMAND.search(normalized):
        return False
    target_pattern = _TARGET_EVIDENCE.get(target)
    action_pattern = _ACTION_EVIDENCE.get(action)
    if (
        target_pattern is None
        or action_pattern is None
        or target_pattern.search(normalized) is None
        or action_pattern.search(normalized) is None
    ):
        return False
    if action != "set_color":
        return True
    requested = {
        candidate
        for candidate in COLORS
        if re.search(rf"\b{re.escape(candidate.replace('_', ' '))}\b", normalized)
    }
    return requested == {color}


def build_request(text: str) -> dict[str, Any]:
    return {
        "state": normalized_text(text),
        "model": MODEL,
        "questions": {
            "request_kind": {
                "type": "choice",
                "instructions": (
                    "Classify this utterance. A home command requests one concrete "
                    "smart-home action. Compound means two or more actions."
                ),
                "criteria": {
                    "home_command": "One concrete smart-home action",
                    "general_or_conversation": "General knowledge or conversation, with no home action",
                    "compound": "Two or more requested actions",
                    "unsupported": "A home request that cannot be represented by the available targets or actions",
                },
            },
            "target": {
                "type": "choice",
                "instructions": (
                    "Select the explicitly requested smart-home target. Do not infer "
                    "a room that was not stated."
                ),
                "criteria": {key: value.entity_id for key, value in TARGETS.items()}
                | {"none_or_unknown": "No explicit known target"},
            },
            "action": {
                "type": "choice",
                "instructions": "Select the single requested action.",
                "criteria": {
                    "turn_on": "Turn on",
                    "turn_off": "Turn off",
                    "toggle": "Toggle",
                    "set_brightness": "Set an exact brightness percentage",
                    "adjust_brightness_up": "Increase brightness",
                    "adjust_brightness_down": "Decrease brightness",
                    "set_color": "Set a named light color",
                    "set_temperature": "Set an exact thermostat temperature",
                    "adjust_temperature_up": "Make the thermostat warmer",
                    "adjust_temperature_down": "Make the thermostat cooler",
                    "activate_scene": "Activate a scene",
                    "pause_media": "Pause or stop media",
                    "play_music": "Play music, a song, an artist, or a playlist",
                    "none_or_unsupported": "No supported action",
                },
            },
            "color": {
                "type": "choice",
                "instructions": "Select the explicitly requested color, or none if color is irrelevant or absent.",
                "criteria": {color: color.replace("_", " ") for color in sorted(COLORS)}
                | {"none_or_unknown": "No supported color stated"},
            },
            "reference": {
                "type": "choice",
                "instructions": (
                    "Was the target explicitly named in this utterance? Pronouns and "
                    "phrases such as it, them, there, or down here are ambiguous."
                ),
                "criteria": {
                    "explicit_target": "A target or room is explicitly named",
                    "missing_or_ambiguous": "The target is omitted or only referenced indirectly",
                },
            },
        },
    }


def _choice(answers: dict[str, Any], name: str) -> tuple[str, float] | None:
    answer = answers.get(name)
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        return None
    choice = answer.get("choice")
    confidence = answer.get("confidence")
    if not isinstance(choice, str) or not isinstance(confidence, (int, float)):
        return None
    if not 0 <= float(confidence) <= 1:
        return None
    return choice, float(confidence)


def _one_number(text: str) -> float | None:
    values = [float(match.group(1)) for match in _NUMBER.finditer(text)]
    return values[0] if len(values) == 1 else None


def _decide_music(text: str, target_name: str, confidence: float) -> Decision:
    """Route a play-music classification without an explicit device target.

    Music always plays on the default satellite speaker, so unlike device
    commands it never asks the user to repeat with a device or room. Only
    an empty query or low confidence still clarifies.
    """
    if target_name != "satellite_media_player":
        return Decision(
            "clarify", speech="Please repeat with one supported device and action."
        )
    if confidence < LIGHT_THRESHOLD:
        if confidence >= CLARIFY_THRESHOLD:
            return Decision(
                "clarify", speech="Please repeat with the device or room and action."
            )
        return Decision("clarify", speech="I could not identify a safe home command.")
    if extract_music_request(text) is None:
        return Decision("clarify", speech="Please say what to play.")
    return Decision(
        "execute",
        command=Command("satellite_media_player", "play_music", confidence),
    )


def decide(text: str, payload: Any) -> Decision:
    if not isinstance(payload, dict) or payload.get("model") != MODEL:
        return Decision("reject", speech="Jev is unavailable.")
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        return Decision("reject", speech="Jev is unavailable.")

    kind = _choice(answers, "request_kind")
    if kind is None:
        return Decision("reject", speech="Jev is unavailable.")
    kind_name, kind_confidence = kind
    if kind_name == "general_or_conversation" and kind_confidence >= LIGHT_THRESHOLD:
        return Decision("fallback")
    if kind_name == "compound":
        return Decision("clarify", speech="Please ask for one action at a time.")
    if kind_name != "home_command":
        return Decision(
            "clarify", speech="Please repeat with one supported device and action."
        )

    target_answer = _choice(answers, "target")
    action_answer = _choice(answers, "action")
    reference_answer = _choice(answers, "reference")
    if target_answer is None or action_answer is None or reference_answer is None:
        return Decision("reject", speech="Jev is unavailable.")
    target_name, target_confidence = target_answer
    action, action_confidence = action_answer
    reference, reference_confidence = reference_answer
    target = TARGETS.get(target_name)
    confidence = min(
        kind_confidence, target_confidence, action_confidence, reference_confidence
    )

    if action == "play_music":
        return _decide_music(text, target_name, confidence)

    if target is None or action not in target.actions or reference != "explicit_target":
        return Decision(
            "clarify", speech="Please repeat with the device or room and one action."
        )
    if action in RESTRICTED_ACTIONS:
        return Decision("clarify", speech="That action needs confirmation.")

    value: float | None = None
    color = "none_or_unknown"
    if action in {"set_brightness", "set_temperature"}:
        value = _one_number(normalized_text(text))
        if value is None:
            return Decision("clarify", speech="Please repeat with one exact value.")
        if action == "set_brightness" and not 0 <= value <= 100:
            return Decision(
                "clarify",
                speech="Brightness must be between zero and one hundred percent.",
            )
    if action == "set_color":
        color_answer = _choice(answers, "color")
        if color_answer is None:
            return Decision("reject", speech="Jev is unavailable.")
        color, color_confidence = color_answer
        confidence = min(confidence, color_confidence)
        if color not in COLORS:
            return Decision("clarify", speech="Please name a supported color.")

    threshold = (
        CLIMATE_THRESHOLD
        if target_name == "living_room_thermostat"
        else LIGHT_THRESHOLD
    )
    if confidence < threshold:
        if confidence >= CLARIFY_THRESHOLD:
            return Decision(
                "clarify", speech="Please repeat with the device or room and action."
            )
        return Decision("clarify", speech="I could not identify a safe home command.")

    if not _has_command_evidence(normalized_text(text), target_name, action, color):
        return Decision(
            "clarify", speech="Please repeat with the exact device and action."
        )

    return Decision(
        "execute",
        command=Command(target_name, action, confidence, value=value, color=color),
    )
