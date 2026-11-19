"""Fireworks show: an endless, directed display.

A show director plays segments (singles, mirrored pairs, sweeps, willow
curtains, ground mines, colour themes, crossette chains, mini barrages)
and builds to a finale every couple of minutes: a quickening build, a
barrage, a closing salute volley, then darkness before the next show.

Shell types follow real pyrotechnics: peony, chrysanthemum, willow, palm,
ring, double ring, crossette, crackle, strobe, heart, multi-break bouquet,
and salute, plus ground mines and comets.
"""

from __future__ import annotations

import heapq
import itertools
from collections.abc import Callable

import numpy as np
from PIL import Image

from modes.base import ModeRenderer, RenderContext

REFERENCE_HZ = 30.0
MAX_DT = 0.05

SHELL_GRAVITY = 36.0
STAR_GRAVITY = 12.0
# Sparks and crackle pops are only shed below this; burst stars always spawn.
MAX_PARTICLES = 2600
SKY_DECAY = 0.74  # afterglow per reference frame

COLORS: dict[str, tuple[float, float, float]] = {
    "red": (255, 40, 40),
    "orange": (255, 130, 20),
    "yellow": (255, 225, 50),
    "lime": (140, 255, 40),
    "green": (40, 255, 90),
    "cyan": (40, 220, 255),
    "blue": (50, 90, 255),
    "violet": (170, 70, 255),
    "magenta": (255, 50, 200),
    "pink": (255, 120, 170),
    "gold": (255, 170, 50),
    "silver": (215, 225, 255),
    "white": (255, 255, 240),
}
VIVID = (
    "red",
    "orange",
    "yellow",
    "lime",
    "green",
    "cyan",
    "blue",
    "violet",
    "magenta",
    "pink",
)
THEMES = (
    ("red", "white", "blue"),
    ("cyan", "blue", "violet"),
    ("red", "orange", "gold"),
    ("green", "lime", "gold"),
    ("magenta", "pink", "violet"),
)


def color(name: str) -> np.ndarray:
    return np.array(COLORS[name], dtype=np.float32)


class Particles:
    """Struct-of-arrays particle store: stars, sparks, and shells alike."""

    FIELDS = (
        "pos",
        "vel",
        "color",
        "age",
        "life",
        "drag",
        "gravity",
        "spark_rate",
        "split_at",
        "crackle",
        "strobe",
    )

    def __init__(self) -> None:
        self.pos = np.zeros((0, 2))
        self.vel = np.zeros((0, 2))
        self.color = np.zeros((0, 3), dtype=np.float32)
        self.age = np.zeros(0)
        self.life = np.zeros(0)
        self.drag = np.zeros(0)
        self.gravity = np.zeros(0)
        self.spark_rate = np.zeros(0)
        self.split_at = np.zeros(0)
        self.crackle = np.zeros(0, dtype=bool)
        self.strobe = np.zeros(0, dtype=bool)

    def __len__(self) -> int:
        return len(self.pos)

    def add(
        self,
        pos: np.ndarray,
        vel: np.ndarray,
        rgb: np.ndarray,
        life: np.ndarray | float,
        drag: float = 1.6,
        gravity: float = STAR_GRAVITY,
        spark_rate: float = 0.0,
        split_at: float = np.inf,
        crackle: bool = False,
        strobe: bool = False,
    ) -> None:
        n = len(vel)
        if n == 0:
            return
        new = {
            "pos": np.broadcast_to(pos, (n, 2)),
            "vel": vel,
            "color": np.broadcast_to(rgb, (n, 3)).astype(np.float32),
            "age": np.zeros(n),
            "life": np.broadcast_to(life, (n,)),
            "drag": np.full(n, drag),
            "gravity": np.full(n, gravity),
            "spark_rate": np.full(n, spark_rate),
            "split_at": np.full(n, split_at),
            "crackle": np.full(n, crackle),
            "strobe": np.full(n, strobe),
        }
        for name in self.FIELDS:
            setattr(self, name, np.concatenate((getattr(self, name), new[name])))

    def keep(self, mask: np.ndarray) -> None:
        for name in self.FIELDS:
            setattr(self, name, getattr(self, name)[mask])


class Shells:
    """Rising shells. Each bursts by calling its burst callback at its fuse."""

    def __init__(self) -> None:
        self.pos = np.zeros((0, 2))
        self.vel = np.zeros((0, 2))
        self.fuse = np.zeros(0)
        self.trail = np.zeros(0)  # sparks per second on the way up
        self.bursts: list[Callable[[np.ndarray], None]] = []

    def __len__(self) -> int:
        return len(self.pos)

    def add(
        self,
        pos: np.ndarray,
        vel: np.ndarray,
        fuse: float,
        trail: float,
        burst: Callable[[np.ndarray], None],
    ) -> None:
        self.pos = np.vstack((self.pos, pos))
        self.vel = np.vstack((self.vel, vel))
        self.fuse = np.append(self.fuse, fuse)
        self.trail = np.append(self.trail, trail)
        self.bursts.append(burst)

    def keep(self, mask: np.ndarray) -> None:
        self.pos = self.pos[mask]
        self.vel = self.vel[mask]
        self.fuse = self.fuse[mask]
        self.trail = self.trail[mask]
        self.bursts = list(itertools.compress(self.bursts, mask))


class FireworksShow:
    """Particle physics, shell catalogue, and rendering for one show."""

    def __init__(self, rng: np.random.Generator, size: int = 64) -> None:
        self.rng = rng
        self.size = size
        self.ground = size - 1.0
        self.stars = Particles()
        self.shells = Shells()
        self.sky = np.zeros((size, size, 3), dtype=np.float32)
        self.flash = 0.0
        self.bursts = 0  # total bursts, for tests and pacing

    # ---- geometry -------------------------------------------------------

    def _sphere(self, n: int, speed: float) -> np.ndarray:
        """Directions of a 3D sphere projected to 2D: dense at the rim like
        a real shell seen from the side."""
        v = self.rng.normal(size=(n, 3))
        v /= np.linalg.norm(v, axis=1, keepdims=True)
        return v[:, :2] * speed * self.rng.uniform(0.92, 1.05, (n, 1))

    def _ring(self, n: int, speed: float) -> np.ndarray:
        """A ring tilted toward or away from the viewer: an ellipse."""
        angles = np.linspace(0, 2 * np.pi, n, endpoint=False)
        tilt = self.rng.uniform(0.25, 1.0)
        spin = self.rng.uniform(0, np.pi)
        x, y = np.cos(angles), np.sin(angles) * tilt
        c, s = np.cos(spin), np.sin(spin)
        return np.column_stack((x * c - y * s, x * s + y * c)) * speed

    def _heart(self, n: int, speed: float) -> np.ndarray:
        t = np.linspace(0, 2 * np.pi, n, endpoint=False)
        x = 16 * np.sin(t) ** 3
        y = -(13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t))
        return np.column_stack((x, y)) / 17 * speed

    def _flash(self, amount: float) -> None:
        self.flash = min(1.0, self.flash + amount)
        self.bursts += 1

    # ---- shell catalogue ------------------------------------------------
    # Each burst takes the burst position, colours, and a size scale.

    def peony(self, at: np.ndarray, rgb: list[np.ndarray], scale: float) -> None:
        n = int(70 * scale)
        vel = self._sphere(n, 30 * scale)
        cols = np.array([rgb[i % len(rgb)] for i in range(n)])
        self.stars.add(at, vel, cols, self.rng.uniform(1.2, 1.7, n), drag=1.5)
        self._flash(0.25 * scale)

    def chrysanthemum(
        self, at: np.ndarray, rgb: list[np.ndarray], scale: float
    ) -> None:
        n = int(55 * scale)
        vel = self._sphere(n, 30 * scale)
        self.stars.add(
            at, vel, rgb[0], self.rng.uniform(1.3, 1.8, n), drag=1.5, spark_rate=14
        )
        self._flash(0.25 * scale)

    def willow(self, at: np.ndarray, rgb: list[np.ndarray], scale: float) -> None:
        n = int(45 * scale)
        vel = self._sphere(n, 24 * scale)
        self.stars.add(
            at,
            vel,
            color("gold") * 0.9,
            self.rng.uniform(3.0, 3.8, n),
            drag=2.4,
            gravity=7,
            spark_rate=10,
        )
        self._flash(0.2 * scale)

    def palm(self, at: np.ndarray, rgb: list[np.ndarray], scale: float) -> None:
        n = int(self.rng.integers(6, 10))
        angles = np.linspace(0, 2 * np.pi, n, endpoint=False) + self.rng.uniform(0, 1)
        vel = np.column_stack((np.cos(angles), np.sin(angles))) * 30 * scale
        self.stars.add(
            at, vel, rgb[0], self.rng.uniform(1.6, 2.0, n), drag=1.2, spark_rate=45
        )
        self._flash(0.3 * scale)

    def ring(self, at: np.ndarray, rgb: list[np.ndarray], scale: float) -> None:
        n = int(44 * scale)
        self.stars.add(at, self._ring(n, 26 * scale), rgb[0], 1.5, drag=1.4)
        self._flash(0.2 * scale)

    def double_ring(self, at: np.ndarray, rgb: list[np.ndarray], scale: float) -> None:
        outer = rgb[0]
        inner = rgb[1 % len(rgb)]
        self.stars.add(at, self._ring(int(44 * scale), 28 * scale), outer, 1.5)
        self.stars.add(at, self._ring(int(28 * scale), 15 * scale), inner, 1.4)
        self._flash(0.25 * scale)

    def crossette(self, at: np.ndarray, rgb: list[np.ndarray], scale: float) -> None:
        n = 8
        angles = np.linspace(0, 2 * np.pi, n, endpoint=False) + self.rng.uniform(0, 1)
        vel = np.column_stack((np.cos(angles), np.sin(angles))) * 24 * scale
        self.stars.add(at, vel, rgb[0], 1.6, drag=1.0, spark_rate=20, split_at=0.45)
        self._flash(0.2 * scale)

    def crackle(self, at: np.ndarray, rgb: list[np.ndarray], scale: float) -> None:
        n = int(60 * scale)
        tint = color("gold") if self.rng.random() < 0.6 else color("silver")
        self.stars.add(
            at,
            self._sphere(n, 26 * scale),
            tint,
            self.rng.uniform(0.9, 1.3, n),
            crackle=True,
        )
        self._flash(0.3 * scale)

    def strobe(self, at: np.ndarray, rgb: list[np.ndarray], scale: float) -> None:
        n = int(50 * scale)
        self.stars.add(
            at,
            self._sphere(n, 24 * scale),
            color("white"),
            self.rng.uniform(1.6, 2.2, n),
            drag=2.0,
            gravity=6,
            strobe=True,
        )
        self._flash(0.2 * scale)

    def heart(self, at: np.ndarray, rgb: list[np.ndarray], scale: float) -> None:
        self.stars.add(
            at, self._heart(40, 24 * scale), color("pink"), 1.5, drag=1.6, gravity=6
        )
        self._flash(0.2 * scale)

    def bouquet(self, at: np.ndarray, rgb: list[np.ndarray], scale: float) -> None:
        """Multi-break: the shell scatters small shells that each burst."""
        n = int(self.rng.integers(3, 6))
        angles = self.rng.uniform(0, 2 * np.pi, n)
        vel = np.column_stack((np.cos(angles), np.sin(angles))) * 16
        for i in range(n):
            sub = [color(self.rng.choice(VIVID))]
            self.shells.add(
                at,
                vel[i],
                self.rng.uniform(0.3, 0.6),
                4,
                lambda p, sub=sub: self.peony(p, sub, 0.45 * scale),
            )
        self._flash(0.1)

    def salute(self, at: np.ndarray, rgb: list[np.ndarray], scale: float) -> None:
        """A bright report: a white flash and a short burst of crackle."""
        n = int(45 * scale)
        self.stars.add(
            at,
            self._sphere(n, 22 * scale),
            color("white"),
            self.rng.uniform(0.25, 0.45, n),
            drag=3.0,
            crackle=True,
        )
        self._flash(0.8)

    SHELLS = (
        "peony",
        "chrysanthemum",
        "willow",
        "palm",
        "ring",
        "double_ring",
        "crossette",
        "crackle",
        "strobe",
        "heart",
        "bouquet",
        "salute",
    )

    # ---- launches -------------------------------------------------------

    def launch(
        self,
        x: float,
        kind: str,
        rgb: list[np.ndarray] | None = None,
        apex: float | None = None,
        scale: float = 1.0,
    ) -> None:
        """Fire a shell from the ground at column x to burst near `apex`
        (a row; smaller is higher)."""
        if rgb is None:
            rgb = self.pick_colors()
        if apex is None:
            apex = self.rng.uniform(10, 26)
        rise = self.ground - apex
        speed = np.sqrt(2 * SHELL_GRAVITY * rise)
        burst = getattr(self, kind)
        trail = 30.0 if kind == "palm" else 10.0
        self.shells.add(
            np.array([x, self.ground]),
            np.array([self.rng.normal(0, 2.0), -speed]),
            speed / SHELL_GRAVITY,
            trail,
            lambda p: burst(p, rgb, scale),
        )

    def mine(self, x: float, rgb: np.ndarray | None = None) -> None:
        """A ground fan of sparking stars thrown straight up."""
        n = 18
        angles = -np.pi / 2 + self.rng.uniform(-0.45, 0.45, n)
        speed = self.rng.uniform(38, 52, (n, 1))
        vel = np.column_stack((np.cos(angles), np.sin(angles))) * speed
        tint = rgb if rgb is not None else color(self.rng.choice(VIVID))
        self.stars.add(
            np.array([x, self.ground]),
            vel,
            tint,
            self.rng.uniform(1.0, 1.4, n),
            drag=0.6,
            gravity=30,
            spark_rate=12,
        )
        self._flash(0.15)

    def comet(self, x: float) -> None:
        """A single heavy-sparking star rising from the ground."""
        self.stars.add(
            np.array([x, self.ground]),
            np.array([[self.rng.normal(0, 4), -self.rng.uniform(48, 58)]]),
            color("gold"),
            1.4,
            drag=0.3,
            gravity=30,
            spark_rate=70,
        )

    def pick_colors(self) -> list[np.ndarray]:
        names = self.rng.choice(VIVID, 2, replace=False)
        return [color(str(n)) for n in names]

    # ---- simulation -----------------------------------------------------

    def _emit_sparks(
        self, pos: np.ndarray, rgb: np.ndarray, rate: np.ndarray, dt: float
    ) -> None:
        """Short-lived, dim sparks shed behind moving stars."""
        if len(self.stars) >= MAX_PARTICLES or not len(pos):
            return
        emit = self.rng.random(len(pos)) < rate * dt
        n = int(emit.sum())
        if n == 0:
            return
        warm = np.array([1.0, 0.75, 0.45], dtype=np.float32)
        self.stars.add(
            pos[emit] + self.rng.normal(0, 0.3, (n, 2)),
            self.rng.normal(0, 2.5, (n, 2)),
            rgb[emit] * warm * 0.55,
            self.rng.uniform(0.25, 0.6, n),
            drag=3.0,
            gravity=10,
        )

    def _update_shells(self, dt: float) -> None:
        shells = self.shells
        if not len(shells):
            return
        shells.vel[:, 1] += SHELL_GRAVITY * dt
        shells.pos += shells.vel * dt
        shells.fuse -= dt
        tail = np.tile(np.array([255, 190, 110], dtype=np.float32), (len(shells), 1))
        self._emit_sparks(shells.pos, tail, shells.trail, dt)
        self._plot(shells.pos, tail)
        burst = shells.fuse <= 0
        due = [(shells.bursts[i], shells.pos[i].copy()) for i in np.flatnonzero(burst)]
        shells.keep(~burst)
        for callback, at in due:
            callback(at)

    def _update_stars(self, dt: float) -> None:
        stars = self.stars
        if not len(stars):
            return
        stars.age += dt
        stars.vel *= np.exp(-stars.drag * dt)[:, None]
        stars.vel[:, 1] += stars.gravity * dt
        stars.pos += stars.vel * dt

        # Decide every fate first, then keep survivors, then add newcomers,
        # so masks always match the arrays they index.
        split = stars.age >= stars.split_at
        expired = (stars.age >= stars.life) | (stars.pos[:, 1] > self.size + 2)
        pops = expired & stars.crackle & (stars.life > 0.5)
        sparking = ~split & ~expired & (stars.spark_rate > 0)
        splits = (stars.pos[split], stars.vel[split], stars.color[split])
        popped = stars.pos[pops]
        sparks = (
            stars.pos[sparking],
            stars.color[sparking],
            stars.spark_rate[sparking],
        )
        stars.keep(~split & ~expired)

        # Crossettes split into four stars at right angles to their path.
        for p, v, c in zip(*splits, strict=True):
            heading = np.arctan2(v[1], v[0])
            angles = heading + np.array([0.79, 2.36, 3.93, 5.50])
            kids = np.column_stack((np.cos(angles), np.sin(angles))) * 14
            stars.add(p, kids, c, 0.9, drag=1.2, spark_rate=10)
        # Crackle stars pop into white specks as they die.
        if len(popped) and len(stars) < MAX_PARTICLES:
            n = len(popped) * 2
            stars.add(
                np.repeat(popped, 2, axis=0),
                self.rng.normal(0, 6, (n, 2)),
                color("white"),
                self.rng.uniform(0.05, 0.12, n),
                drag=4.0,
                gravity=0,
            )
        self._emit_sparks(*sparks, dt)

    def _plot(self, pos: np.ndarray, rgb: np.ndarray) -> None:
        px = np.round(pos[:, 0]).astype(int)
        py = np.round(pos[:, 1]).astype(int)
        inside = (px >= 0) & (px < self.size) & (py >= 0) & (py < self.size)
        # Max blending: overlapping bursts keep their hues instead of
        # summing to white.
        np.maximum.at(self.sky, (py[inside], px[inside]), rgb[inside])

    def _draw_stars(self) -> None:
        stars = self.stars
        if not len(stars):
            return
        progress = np.clip(stars.age / np.maximum(stars.life, 1e-6), 0, 1)
        # White-hot at ignition, settling into colour, dimming toward the end.
        heat = np.clip(1 - stars.age / 0.12, 0, 1)[:, None]
        rgb = stars.color * (1 - heat) + 255 * heat
        level = (1 - progress) ** 0.9
        twinkle = stars.crackle & (progress > 0.55)
        level[twinkle] *= self.rng.random(int(twinkle.sum())) < 0.45
        blink = stars.strobe & (progress > 0.3)
        level[blink] *= (np.floor(stars.age[blink] * 14 + stars.pos[blink, 0]) % 2) == 0
        self._plot(stars.pos, rgb * level[:, None])

    def step(self, dt: float) -> None:
        self._update_shells(dt)
        self._update_stars(dt)

    def render(self, dt: float) -> Image.Image:
        self.sky *= SKY_DECAY ** (dt * REFERENCE_HZ)
        self._draw_stars()
        self.flash *= 0.5 ** (dt / 0.09)
        glow = self.flash * np.array([12, 10, 14], dtype=np.float32)
        frame = np.clip(self.sky + glow, 0, 255)
        return Image.fromarray(frame.astype(np.uint8), mode="RGB")


class ShowDirector:
    """Plans the show as timed cues: segments separated by short pauses, a
    finale roughly every FINALE_EVERY seconds, then a dark pause."""

    FINALE_EVERY = (120.0, 170.0)

    def __init__(self, show: FireworksShow) -> None:
        self.show = show
        self.rng = show.rng
        self.time = 0.0
        self.cues: list[tuple[float, int, Callable[[], None]]] = []
        self.counter = itertools.count()
        self.segments: list[str] = []
        self.next_finale = self.rng.uniform(*self.FINALE_EVERY)
        self.planned_until = 0.0

    def cue(self, at: float, action: Callable[[], None]) -> None:
        heapq.heappush(self.cues, (at, next(self.counter), action))

    def advance(self, dt: float) -> None:
        self.time += dt
        while self.planned_until <= self.time + 0.5:
            self._plan_next()
        while self.cues and self.cues[0][0] <= self.time:
            _, _, action = heapq.heappop(self.cues)
            action()

    # ---- segment planning -----------------------------------------------

    def _plan_next(self) -> None:
        start = max(self.planned_until, self.time)
        if start >= self.next_finale:
            duration = self._finale(start)
            self.next_finale = start + duration + self.rng.uniform(*self.FINALE_EVERY)
            name = "finale"
        else:
            segments: dict[str, Callable[[float], float]] = {
                "singles": self._singles,
                "pairs": self._pairs,
                "sweep": self._sweep,
                "willows": self._willows,
                "mines": self._mines,
                "theme": self._theme,
                "crossettes": self._crossettes,
                "barrage": self._barrage,
            }
            weights = np.array([3, 2, 2, 1.2, 1.2, 1.5, 1, 0.8])
            options = list(segments)
            # Avoid repeating the previous segment.
            if self.segments and self.segments[-1] in options:
                weights[options.index(self.segments[-1])] = 0
            name = str(self.rng.choice(options, p=weights / weights.sum()))
            duration = segments[name](start)
        self.segments.append(name)
        self.planned_until = start + duration + self.rng.uniform(0.8, 2.2)

    def _x(self, margin: float = 10) -> float:
        return float(self.rng.uniform(margin, self.show.size - margin))

    def _launch(self, at: float, **kwargs) -> None:
        x = kwargs.pop("x", None)
        self.cue(
            at, lambda: self.show.launch(x if x is not None else self._x(), **kwargs)
        )

    def _random_kind(self, finale: bool = False) -> str:
        kinds = list(FireworksShow.SHELLS)
        weights = {
            "peony": 4,
            "chrysanthemum": 3,
            "willow": 1.5,
            "palm": 2,
            "ring": 2,
            "double_ring": 1.5,
            "crossette": 1.5,
            "crackle": 1.0 if finale else 1.5,
            "strobe": 0.5 if finale else 1.0,
            "heart": 0.5,
            "bouquet": 1.5 if finale else 0.8,
            "salute": 0.6 if finale else 0.2,
        }
        w = np.array([weights[k] for k in kinds])
        return str(self.rng.choice(kinds, p=w / w.sum()))

    def _singles(self, start: float) -> float:
        t = start
        for _ in range(int(self.rng.integers(4, 8))):
            self._launch(t, kind=self._random_kind())
            t += self.rng.uniform(1.0, 2.0)
        return t - start

    def _pairs(self, start: float) -> float:
        t = start
        for _ in range(int(self.rng.integers(3, 5))):
            kind = self._random_kind()
            rgb = self.show.pick_colors()
            offset = self.rng.uniform(10, 22)
            apex = self.rng.uniform(12, 24)
            mid = self.show.size / 2
            for x in (mid - offset, mid + offset):
                self._launch(t, x=x, kind=kind, rgb=rgb, apex=apex, scale=0.8)
            t += self.rng.uniform(1.6, 2.4)
        return t - start

    def _sweep(self, start: float) -> float:
        n = int(self.rng.integers(5, 8))
        xs = np.linspace(8, self.show.size - 8, n)
        if self.rng.random() < 0.5:
            xs = xs[::-1]
        kind = str(self.rng.choice(["peony", "ring", "chrysanthemum", "palm"]))
        rgb = self.show.pick_colors()
        apexes = np.linspace(22, 12, n) if self.rng.random() < 0.5 else np.full(n, 16.0)
        for i, (x, apex) in enumerate(zip(xs, apexes, strict=True)):
            self._launch(
                start + i * 0.28,
                x=float(x),
                kind=kind,
                rgb=rgb,
                apex=float(apex),
                scale=0.65,
            )
        return n * 0.28 + 1.5

    def _willows(self, start: float) -> float:
        t = start
        for _ in range(int(self.rng.integers(3, 5))):
            self._launch(t, kind="willow", apex=self.rng.uniform(8, 16))
            t += self.rng.uniform(1.2, 1.8)
        return t - start + 2.0

    def _mines(self, start: float) -> float:
        t = start
        for _ in range(int(self.rng.integers(4, 7))):
            x = self._x(8)
            if self.rng.random() < 0.65:
                self.cue(t, lambda x=x: self.show.mine(x))
            else:
                self.cue(t, lambda x=x: self.show.comet(x))
            t += self.rng.uniform(0.5, 1.1)
        return t - start + 1.0

    def _theme(self, start: float) -> float:
        theme = THEMES[int(self.rng.integers(len(THEMES)))]
        t = start
        for i in range(int(self.rng.integers(5, 8))):
            rgb = [color(theme[i % len(theme)]), color(theme[(i + 1) % len(theme)])]
            kind = str(
                self.rng.choice(["peony", "double_ring", "chrysanthemum", "ring"])
            )
            self._launch(t, kind=kind, rgb=rgb)
            t += self.rng.uniform(0.7, 1.3)
        return t - start

    def _crossettes(self, start: float) -> float:
        t = start
        for _ in range(int(self.rng.integers(4, 7))):
            self._launch(t, kind="crossette", scale=0.85)
            t += self.rng.uniform(0.4, 0.8)
        return t - start + 1.0

    def _barrage(self, start: float, seconds: float = 4.0) -> float:
        t = start
        while t < start + seconds:
            self._launch(t, kind=self._random_kind(finale=True), scale=0.7)
            t += self.rng.uniform(0.15, 0.35)
        return seconds + 1.0

    def _finale(self, start: float) -> float:
        # Build: launches come faster and faster.
        t = start
        interval = 0.9
        while t < start + 10:
            self._launch(t, kind=self._random_kind(finale=True), scale=0.8)
            t += interval
            interval = max(0.2, interval * 0.85)
        # Barrage: overlapping bursts across the sky, with ground mines.
        end = t + 9
        while t < end:
            self._launch(t, kind=self._random_kind(finale=True), scale=0.65)
            if self.rng.random() < 0.15:
                x = self._x(6)
                self.cue(t, lambda x=x: self.show.mine(x))
            t += self.rng.uniform(0.14, 0.26)
        # Closing volley: crackle and salutes together, then darkness.
        t += 0.6
        for x in np.linspace(8, self.show.size - 8, 7):
            kind = "salute" if self.rng.random() < 0.5 else "crackle"
            self._launch(
                t + self.rng.uniform(0, 0.25),
                x=float(x),
                kind=kind,
                apex=self.rng.uniform(10, 20),
            )
        return t - start + 2.5 + 4.0


class FireworksRenderer(ModeRenderer):
    """Fireworks mode: an endless directed show."""

    def __init__(self, seed: int | None = None) -> None:
        self.seed = seed
        self.size = 0
        self.reset()

    def reset(self) -> None:
        """Start a fresh show (called when switching into the mode)."""
        rng = np.random.default_rng(self.seed)
        self.show = FireworksShow(rng, self.size or 64)
        self.director = ShowDirector(self.show)
        self.size = self.show.size

    def render(self, ctx: RenderContext, size: int = 64) -> Image.Image:
        if size != self.size:
            self.size = size
            self.reset()
        dt = min(max(ctx.dt, 0.0), MAX_DT)
        self.director.advance(dt)
        self.show.step(dt)
        return self.show.render(dt)
