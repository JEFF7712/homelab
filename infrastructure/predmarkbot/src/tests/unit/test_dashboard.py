"""Unit tests for dashboard rendering functions."""
from __future__ import annotations

from predmarkbot.dashboard import render_hourly_svg, render_page

# ---------------------------------------------------------------------------
# render_hourly_svg
# ---------------------------------------------------------------------------


class TestRenderHourlySvg:
    def test_returns_svg_tag(self) -> None:
        svg = render_hourly_svg([1, 2, 3])
        assert svg.startswith("<svg ")
        assert "</svg>" in svg

    def test_correct_bar_count(self) -> None:
        n = 7
        svg = render_hourly_svg([i for i in range(n)])
        # Each bar is a <rect …/> element
        assert svg.count("<rect ") == n

    def test_empty_buckets_returns_no_data(self) -> None:
        svg = render_hourly_svg([])
        assert "no data" in svg
        assert "<rect " not in svg

    def test_all_zero_buckets_renders_zero_height_bars(self) -> None:
        # All-zero → bars are present but with height="0"
        svg = render_hourly_svg([0, 0, 0])
        assert svg.count("<rect ") == 3
        assert 'height="0"' in svg

    def test_single_bucket(self) -> None:
        svg = render_hourly_svg([5])
        assert svg.count("<rect ") == 1

    def test_large_input(self) -> None:
        buckets = list(range(168))  # 7 days × 24h
        svg = render_hourly_svg(buckets)
        assert svg.count("<rect ") == 168

    def test_svg_dimensions_present(self) -> None:
        svg = render_hourly_svg([1, 2])
        assert 'width="840"' in svg
        assert 'height="60"' in svg


# ---------------------------------------------------------------------------
# render_page
# ---------------------------------------------------------------------------


_FIXTURE: dict = {
    "hostname": "predmarkbot-abc-123",
    "mode": "shadow",
    "version": "0.0.99",
    "kill_active": False,
    "markets_watched": 11,
    "intents_today": 3,
    "intents_all": 42,
    "orders_today": 0,
    "orders_all": 0,
    "fills_today": 0,
    "fills_all": 0,
    "pnl_today": 0,
    "open_exposure": 0,
    "hourly_buckets": [0, 1, 2, 1, 0],
    "hourly_labels": ["01/01 00h", "01/01 01h", "01/01 02h", "01/01 03h", "01/01 04h"],
    "series_rows": [{"series": "KXHIGHNY", "count": 5, "top_tickers": "KXHIGHNY-25-T75"}],
    "intent_rows": [
        {
            "ts": "2025-01-01T12:00:00",
            "ticker": "KXHIGHNY-25-T75",
            "side": "yes",
            "price_cents": 3,
            "size": 5,
            "expected_edge_cents": 2,
            "reasoning": "longshot edge detected",
        }
    ],
    "order_rows": [],
}


class TestRenderPage:
    def test_returns_html_doctype(self) -> None:
        html = render_page(_FIXTURE)
        assert html.startswith("<!DOCTYPE html>")

    def test_includes_version(self) -> None:
        html = render_page(_FIXTURE)
        assert "0.0.99" in html

    def test_includes_hostname(self) -> None:
        html = render_page(_FIXTURE)
        assert "predmarkbot-abc-123" in html

    def test_mode_badge_shadow(self) -> None:
        html = render_page(_FIXTURE)
        assert "badge-shadow" in html
        assert "SHADOW" in html

    def test_mode_badge_live(self) -> None:
        data = {**_FIXTURE, "mode": "live"}
        html = render_page(data)
        assert "badge-live" in html
        assert "LIVE" in html

    def test_markets_watched_count(self) -> None:
        html = render_page(_FIXTURE)
        assert "11" in html

    def test_intents_today_and_all(self) -> None:
        html = render_page(_FIXTURE)
        # Both "3" (today) and "42" (all-time) should appear
        assert "3" in html
        assert "42" in html

    def test_kill_switch_clear(self) -> None:
        html = render_page(_FIXTURE)
        assert "badge-clear" in html
        assert "CLEAR" in html

    def test_kill_switch_active(self) -> None:
        data = {**_FIXTURE, "kill_active": True}
        html = render_page(data)
        assert "badge-active" in html
        assert "ACTIVE" in html

    def test_includes_svg_chart(self) -> None:
        html = render_page(_FIXTURE)
        assert "<svg " in html
        assert "<rect " in html

    def test_intent_table_row(self) -> None:
        html = render_page(_FIXTURE)
        assert "KXHIGHNY-25-T75" in html
        assert "longshot edge detected" in html

    def test_no_orders_shows_placeholder(self) -> None:
        html = render_page(_FIXTURE)
        assert "no orders yet" in html

    def test_auto_refresh_meta(self) -> None:
        html = render_page(_FIXTURE)
        assert 'http-equiv="refresh"' in html
        assert 'content="30"' in html

    def test_series_row_rendered(self) -> None:
        html = render_page(_FIXTURE)
        assert "KXHIGHNY" in html
        assert "KXHIGHNY-25-T75" in html

    def test_empty_buckets_no_bars(self) -> None:
        data = {**_FIXTURE, "hourly_buckets": [], "hourly_labels": []}
        html = render_page(data)
        assert "no data" in html

    def test_pnl_formatted_with_sign(self) -> None:
        data = {**_FIXTURE, "pnl_today": -150}
        html = render_page(data)
        assert "-150" in html

    def test_positive_pnl(self) -> None:
        data = {**_FIXTURE, "pnl_today": 50}
        html = render_page(data)
        assert "+50" in html
