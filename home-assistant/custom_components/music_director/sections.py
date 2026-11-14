"""Song sections from live loudness: quiet, normal and loud stretches.

The director feeds LedFx's live melbank level (about 60 times a second).
A fast average (a few seconds) is compared with a slow one (the song's
recent baseline, half a minute or so); sustained departures become sections.
Hysteresis and a minimum dwell keep single beats and fills from flipping
the lights. Pure logic: the caller supplies the clock.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

QUIET, NORMAL, LOUD = "quiet", "normal", "loud"

FAST_SECONDS = 6.0
SLOW_SECONDS = 40.0
# Ignore the first seconds of a track while the baseline settles.
WARMUP_SECONDS = 20.0
# A section must last this long before the next change.
MIN_DWELL_SECONDS = 16.0
# fast / slow ratio thresholds, with hysteresis.
ENTER_LOUD, LEAVE_LOUD = 1.3, 1.1
ENTER_QUIET, LEAVE_QUIET = 0.65, 0.85
# Below this baseline level the music is near silent: stay normal.
SILENCE = 1e-6

# How sections move the lights: strip brightness as a fraction of the set
# Music Mode Brightness (kept high), and an intensity offset for colours.
SECTION_BRIGHTNESS = {QUIET: 0.8, NORMAL: 0.9, LOUD: 1.0}
SECTION_INTENSITY = {QUIET: -0.1, NORMAL: 0.0, LOUD: 0.15}


@dataclass
class SectionTracker:
    fast: float = 0.0
    slow: float = 0.0
    started: float | None = None
    last_sample: float | None = None
    section: str = NORMAL
    changed: float = 0.0

    def reset(self, now: float) -> None:
        """Start a new track: forget the old baseline."""
        self.fast = self.slow = 0.0
        self.started = now
        self.last_sample = None
        self.section = NORMAL
        self.changed = now

    def add(self, level: float, now: float) -> str | None:
        """Feed one loudness sample. Returns the new section when it
        changes, else None."""
        if self.started is None:
            self.reset(now)
        level = max(0.0, level)
        if self.last_sample is None:
            self.fast = self.slow = level
        else:
            dt = max(0.0, now - self.last_sample)
            self.fast += (level - self.fast) * (1 - math.exp(-dt / FAST_SECONDS))
            self.slow += (level - self.slow) * (1 - math.exp(-dt / SLOW_SECONDS))
        self.last_sample = now
        assert self.started is not None
        if (
            now - self.started < WARMUP_SECONDS
            or now - self.changed < MIN_DWELL_SECONDS
        ):
            return None
        wanted = self._wanted()
        if wanted == self.section:
            return None
        self.section = wanted
        self.changed = now
        return wanted

    def _wanted(self) -> str:
        if self.slow <= SILENCE:
            return NORMAL
        ratio = self.fast / self.slow
        if self.section == LOUD:
            return LOUD if ratio > LEAVE_LOUD else NORMAL
        if self.section == QUIET:
            return QUIET if ratio < LEAVE_QUIET else NORMAL
        if ratio >= ENTER_LOUD:
            return LOUD
        if ratio <= ENTER_QUIET:
            return QUIET
        return NORMAL
