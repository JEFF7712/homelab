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
# Push on the effect's speed and reactivity (0-1 scales): punchier
# choruses and drops, calmer breakdowns.
SECTION_DRIVE = {QUIET: -0.15, NORMAL: 0.0, LOUD: 0.25}
# Bulb brightness as a fraction of Music Mode Brightness: normal parts sit at
# the set level, breakdowns dim and choruses lift (up to 100%).
BULB_SECTION_BRIGHTNESS = {QUIET: 0.8, NORMAL: 1.0, LOUD: 1.15}


def bulb_brightness_pct(brightness_pct: int, section: str) -> int:
    """The taking-part bulbs' brightness for a section, 1-100%."""
    return min(100, max(1, round(brightness_pct * BULB_SECTION_BRIGHTNESS[section])))


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


# Drops: a sudden, sustained jump in loudness (a drop or a chorus slamming
# in), as opposed to the gradual changes sections follow. The strip answers
# with a short flash. A one second average must rise well above the last few
# seconds and above the song's baseline, and stay there briefly, so single
# kicks and the return after a gap between phrases do not count.
PUNCH_SECONDS = 1.0
DROP_JUMP = 1.5
DROP_ABOVE_BASELINE = 1.25
DROP_HOLD_SECONDS = 0.6
DROP_COOLDOWN_SECONDS = 45.0
# Music coming back after near-silence (a pause, a gap between songs) is not
# a drop: none for this long after the one second average falls below a
# tenth of the baseline (the few-second average needs that long to catch up).
# Tuned on a live hip-hop recording on 2026-11-14: no drops in 90 s of verses,
# none when music resumed after a pause, one when a beat came in after a
# quiet intro.
SILENT_SHARE = 0.1
AFTER_SILENCE_SECONDS = 12.0


@dataclass
class DropDetector:
    punch: float = 0.0
    recent: float = 0.0
    baseline: float = 0.0
    started: float | None = None
    last_sample: float | None = None
    rising_since: float | None = None
    last_drop: float | None = None
    last_silent: float | None = None

    def reset(self, now: float) -> None:
        """Start a new track."""
        self.punch = self.recent = self.baseline = 0.0
        self.started = now
        self.last_sample = None
        self.rising_since = None
        self.last_drop = None
        self.last_silent = None

    def add(self, level: float, now: float) -> bool:
        """Feed one loudness sample. Returns True on a drop."""
        if self.started is None:
            self.reset(now)
        level = max(0.0, level)
        if self.last_sample is None:
            self.punch = self.recent = self.baseline = level
        else:
            dt = max(0.0, now - self.last_sample)
            self.punch += (level - self.punch) * (1 - math.exp(-dt / PUNCH_SECONDS))
            self.recent += (level - self.recent) * (1 - math.exp(-dt / FAST_SECONDS))
            self.baseline += (level - self.baseline) * (
                1 - math.exp(-dt / SLOW_SECONDS)
            )
        self.last_sample = now
        assert self.started is not None
        if self.punch <= SILENT_SHARE * self.baseline or self.baseline <= SILENCE:
            self.last_silent = now
        jumping = (
            self.baseline > SILENCE
            and self.punch >= DROP_JUMP * self.recent
            and self.punch >= DROP_ABOVE_BASELINE * self.baseline
        )
        if not jumping:
            self.rising_since = None
            return False
        if self.rising_since is None:
            self.rising_since = now
        if (
            now - self.started < WARMUP_SECONDS
            or now - self.rising_since < DROP_HOLD_SECONDS
            or (
                self.last_silent is not None
                and now - self.last_silent < AFTER_SILENCE_SECONDS
            )
            or (
                self.last_drop is not None
                and now - self.last_drop < DROP_COOLDOWN_SECONDS
            )
        ):
            return False
        self.last_drop = now
        self.rising_since = None
        return True
