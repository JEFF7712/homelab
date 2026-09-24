DOMAIN = "jarvis_jev"
CONF_FALLBACK_AGENT = "fallback_agent"
# General conversation is a separate trust domain. A fallback stays disabled
# until its exposure, prompt, history, privacy, and recursion eval passes.
OLLAMA_FALLBACK_AGENT = ""

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"
REQUEST_TIMEOUT_SECONDS = 1.5
# Total deadline for one fallback delegation turn, bounding hosted-agent
# recursion cost even when the delegate responds slowly.
FALLBACK_TIMEOUT_SECONDS = 10.0
SHADOW_URL = "http://local-decision.voice.svc.cluster.local:8080/v1/systemone"
SHADOW_TIMEOUT_SECONDS = 15.0

LIGHT_THRESHOLD = 0.95
CLIMATE_THRESHOLD = 0.98
CLARIFY_THRESHOLD = 0.75

# Risk tiers for execution. Every action the router can emit must appear in
# exactly one set (enforced by test). STANDARD actions execute when they
# pass the structural and confidence gates. RESTRICTED actions always route
# to clarify: locks, garage doors, alarms, HVAC extremes, and anything
# destructive require explicit confirmation, never silent execution.
# The schema has no restricted actions today; adding one (e.g. a lock
# domain) must add it here first or the tier test fails.
STANDARD_ACTIONS = frozenset(
    {
        "turn_on",
        "turn_off",
        "toggle",
        "set_brightness",
        "adjust_brightness_up",
        "adjust_brightness_down",
        "set_color",
        "set_temperature",
        "adjust_temperature_up",
        "adjust_temperature_down",
        "activate_scene",
        "pause_media",
        "none_or_unsupported",
    }
)
RESTRICTED_ACTIONS = frozenset()
