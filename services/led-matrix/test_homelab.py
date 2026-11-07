"""Unit tests for HomelabRenderer and PrometheusSource multi-view caching."""

from __future__ import annotations

import time
import unittest

from modes.base import HAState, PlaybackState, RenderContext
from modes.homelab import QUERIES, HomelabRenderer, PrometheusSource
from PIL import Image


class HomelabDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = PrometheusSource("http://127.0.0.1:9090")

    def tearDown(self) -> None:
        self.source.close()

    def _ctx(self, tick: float = 0.0) -> RenderContext:
        return RenderContext(
            tick=tick,
            dt=0.05,
            ha_state=HAState(),
            spotify_state=PlaybackState(),
            spotify_art=None,
        )

    def test_snapshot_per_view_caching_and_freshness(self) -> None:
        # Prepopulate cache for K8S and CI/CD
        k8s_data = {"nodes": [({"node": "node-1"}, 1.0)]}
        ci_data = {"ci": [({"repository": "homelab", "state": "success"}, 1.0)]}

        self.source._cache["K8S"] = k8s_data
        self.source._updated["K8S"] = time.monotonic()
        self.source._cache["CI/CD"] = ci_data
        self.source._updated["CI/CD"] = time.monotonic()

        # Switching views returns cached values immediately without reporting offline
        val_k8s, fresh_k8s = self.source.snapshot("K8S")
        self.assertTrue(fresh_k8s)
        self.assertEqual(val_k8s, k8s_data)

        val_ci, fresh_ci = self.source.snapshot("CI/CD")
        self.assertTrue(fresh_ci)
        self.assertEqual(val_ci, ci_data)

        # Uncached view returns empty and offline
        val_net, fresh_net = self.source.snapshot("NETWORK")
        self.assertFalse(fresh_net)
        self.assertEqual(val_net, {})

    def test_stale_data_reports_offline(self) -> None:
        self.source._cache["HEALTH"] = {"cpu": [({"instance": "nas"}, 42.0)]}
        # Updated 65 seconds ago (older than 60s TTL)
        self.source._updated["HEALTH"] = time.monotonic() - 65.0

        val, fresh = self.source.snapshot("HEALTH")
        self.assertFalse(fresh)
        self.assertEqual(val, {"cpu": [({"instance": "nas"}, 42.0)]})

    def test_renderer_smooth_view_rotation(self) -> None:
        # Populate all 4 views
        now = time.monotonic()
        for view, expressions in QUERIES.items():
            self.source._cache[view] = {
                key: [({"test": "metric"}, 1.0)] for key in expressions
            }
            self.source._updated[view] = now

        renderer = HomelabRenderer(self.source)

        # Render each view according to its rotation period
        for idx, expected_view in enumerate(HomelabRenderer.VIEWS):
            tick = idx * HomelabRenderer.PERIOD + 0.1
            ctx = self._ctx(tick=tick)
            img = renderer.render(ctx, size=64)
            self.assertEqual(img.size, (64, 64))
            self.assertIsInstance(img, Image.Image)

            # Ensure data was returned and fresh
            values, fresh = self.source.snapshot(expected_view)
            self.assertTrue(
                fresh, f"View {expected_view} should be fresh during rotation"
            )
            self.assertTrue(
                bool(values), f"View {expected_view} should have cached values"
            )

    def test_renderer_displays_offline_when_unpopulated(self) -> None:
        renderer = HomelabRenderer(self.source)
        ctx = self._ctx(tick=0.0)
        img = renderer.render(ctx, size=64)
        self.assertEqual(img.size, (64, 64))

        # Check that DATA OFFLINE branch was taken (fresh is False)
        _, fresh = self.source.snapshot("K8S")
        self.assertFalse(fresh)

    def test_background_poll_cycles_and_populates_views(self) -> None:
        sample_metric = [({"host": "homelab-01"}, 5.0)]

        def fake_query(expression: str) -> list[tuple[dict[str, str], float]]:
            return sample_metric

        self.source._query = fake_query  # type: ignore[assignment]
        self.source.start()

        # Request K8S
        self.source.snapshot("K8S")

        # Wait briefly for background thread to query
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            with self.source._lock:
                if len(self.source._cache) >= 1 and "K8S" in self.source._cache:
                    break
            time.sleep(0.05)

        with self.source._lock:
            self.assertIn("K8S", self.source._cache)
            self.assertGreater(self.source._updated.get("K8S", 0.0), 0.0)

        # Calling snapshot for K8S should now be fresh
        vals, fresh = self.source.snapshot("K8S")
        self.assertTrue(fresh)
        self.assertIn("nodes", vals)


if __name__ == "__main__":
    unittest.main()
