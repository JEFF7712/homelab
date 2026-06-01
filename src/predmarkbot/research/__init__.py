"""predmarkbot.research — offline analysis of Kalshi historical data.

This subpackage is intentionally NOT imported by the runtime bot
(predmarkbot.runner, predmarkbot.cli's run/status/smoke subcommands).
Its dependencies live in the `research` group and are excluded from the
production container image.
"""
