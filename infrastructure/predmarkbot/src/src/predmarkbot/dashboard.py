"""Read-only HTTP dashboard for predmarkbot.

Exposes a single-page status view at / and a /health endpoint.
Served by aiohttp; no JS framework, no Jinja — pure f-strings.
"""
from __future__ import annotations

import logging
import socket
from datetime import UTC, date, datetime
from typing import Any

from aiohttp import web

from predmarkbot import __version__
from predmarkbot.state import StateStore

_log = logging.getLogger(__name__)

_STARTED_AT: datetime = datetime.now(UTC)

# ---------------------------------------------------------------------------
# SVG bar chart
# ---------------------------------------------------------------------------

_SVG_W = 840
_SVG_H = 60
_BAR_GAP = 1


def render_hourly_svg(buckets: list[int]) -> str:
    """Render a horizontal bar chart as inline SVG.

    ``buckets`` is a list of integer counts (length N).  Each bucket maps to
    one bar.  The chart is fixed at _SVG_W × _SVG_H px.
    """
    n = len(buckets)
    if n == 0:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{_SVG_W}" height="{_SVG_H}">'
            f'<text x="4" y="20" fill="#888" font-size="12">no data</text>'
            f"</svg>"
        )
    max_val = max(buckets) or 1
    bar_w = max(1, (_SVG_W - _BAR_GAP * (n - 1)) // n)
    bars: list[str] = []
    for i, v in enumerate(buckets):
        h = max(2, int(v / max_val * (_SVG_H - 4))) if v else 0
        x = i * (bar_w + _BAR_GAP)
        y = _SVG_H - h
        bars.append(
            f'<rect x="{x}" y="{y}" width="{bar_w}" height="{h}" '
            f'fill="#4a9eff" opacity="0.85"/>'
        )
    bars_str = "\n    ".join(bars)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{_SVG_W}" height="{_SVG_H}" '
        f'style="display:block;overflow:visible">\n'
        f"  <g>\n    {bars_str}\n  </g>\n"
        f"</svg>"
    )


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

_CSS = """\
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Courier New', Courier, monospace;
  background: #0d1117;
  color: #c9d1d9;
  padding: 1.5rem;
  max-width: 1100px;
  margin: 0 auto;
}
h1 { font-size: 1.1rem; color: #58a6ff; margin-bottom: 0.3rem; }
h2 { font-size: 0.9rem; color: #8b949e; text-transform: uppercase;
     letter-spacing: 0.08em; margin: 1.4rem 0 0.5rem; }
.header-line { font-size: 0.75rem; color: #8b949e; margin-bottom: 1.2rem; }
.header-line span { color: #c9d1d9; }

/* stat cards */
.cards { display: flex; flex-wrap: wrap; gap: 0.6rem; margin-bottom: 0.5rem; }
.card {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 6px;
  padding: 0.5rem 0.9rem;
  min-width: 120px;
}
.card .label { font-size: 0.65rem; color: #8b949e; text-transform: uppercase; }
.card .value { font-size: 1.3rem; color: #e6edf3; }
.card .sub   { font-size: 0.7rem; color: #8b949e; }

/* badge */
.badge {
  display: inline-block; padding: 0.15rem 0.5rem;
  border-radius: 3px; font-size: 0.75rem; font-weight: bold;
}
.badge-shadow { background: #1f3a5f; color: #58a6ff; }
.badge-demo   { background: #3d2b1a; color: #f0883e; }
.badge-live   { background: #1a3a2a; color: #3fb950; }
.badge-active { background: #3d1a1a; color: #f85149; }
.badge-clear  { background: #1a3a2a; color: #3fb950; }

/* svg chart */
.chart-wrap {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 6px;
  padding: 0.8rem;
  overflow-x: auto;
  margin-bottom: 0.5rem;
}
.chart-label { font-size: 0.65rem; color: #8b949e; margin-top: 0.3rem; }

/* tables */
table {
  width: 100%; border-collapse: collapse;
  font-size: 0.75rem; margin-bottom: 0.5rem;
}
th {
  text-align: left; padding: 0.3rem 0.5rem;
  border-bottom: 1px solid #30363d; color: #8b949e;
  font-weight: normal; text-transform: uppercase; font-size: 0.65rem;
}
td { padding: 0.3rem 0.5rem; border-bottom: 1px solid #21262d; }
tr:last-child td { border-bottom: none; }
.tbl-wrap {
  background: #161b22; border: 1px solid #30363d; border-radius: 6px;
  overflow: auto; margin-bottom: 0.5rem;
}
.yes { color: #3fb950; }
.no  { color: #f85149; }
.dim { color: #8b949e; }
"""


def _mode_badge(mode: str) -> str:
    cls = {"shadow": "badge-shadow", "demo": "badge-demo", "live": "badge-live"}.get(
        mode, "badge-shadow"
    )
    return f'<span class="badge {cls}">{mode.upper()}</span>'


def _ks_badge(active: bool) -> str:
    if active:
        return '<span class="badge badge-active">ACTIVE</span>'
    return '<span class="badge badge-clear">CLEAR</span>'


def _side_span(side: str) -> str:
    cls = "yes" if side.lower() == "yes" else "no"
    return f'<span class="{cls}">{side.upper()}</span>'


def _fmt_ts(raw: str) -> str:
    """Trim ISO timestamp to readable form."""
    return raw[:19].replace("T", " ") if raw else "—"


def render_page(data: dict[str, Any]) -> str:  # noqa: C901 – long but linear
    """Render the full HTML dashboard page from pre-queried data dict."""
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    uptime_secs = int((datetime.now(UTC) - _STARTED_AT).total_seconds())
    uptime_str = _fmt_uptime(uptime_secs)
    hostname = data.get("hostname", "unknown")
    mode: str = data.get("mode", "shadow")
    version: str = data.get("version", __version__)
    kill_active: bool = data.get("kill_active", False)

    # counts
    markets_watched: int = data.get("markets_watched", 0)
    intents_today: int = data.get("intents_today", 0)
    intents_all: int = data.get("intents_all", 0)
    orders_today: int = data.get("orders_today", 0)
    orders_all: int = data.get("orders_all", 0)
    fills_today: int = data.get("fills_today", 0)
    fills_all: int = data.get("fills_all", 0)
    pnl_today: int = data.get("pnl_today", 0)
    open_exposure: int = data.get("open_exposure", 0)

    # chart buckets (list[int], length = number of hours)
    hourly_buckets: list[int] = data.get("hourly_buckets", [])
    hourly_labels: list[str] = data.get("hourly_labels", [])

    # tables
    series_rows: list[dict[str, Any]] = data.get("series_rows", [])
    intent_rows: list[dict[str, Any]] = data.get("intent_rows", [])
    order_rows: list[dict[str, Any]] = data.get("order_rows", [])

    # --- cards HTML ---
    cards_html = f"""\
<div class="cards">
  <div class="card">
    <div class="label">Markets</div>
    <div class="value">{markets_watched}</div>
    <div class="sub">watched</div>
  </div>
  <div class="card">
    <div class="label">Shadow Intents</div>
    <div class="value">{intents_today}</div>
    <div class="sub">today / {intents_all} all-time</div>
  </div>
  <div class="card">
    <div class="label">Orders</div>
    <div class="value">{orders_today}</div>
    <div class="sub">today / {orders_all} all-time</div>
  </div>
  <div class="card">
    <div class="label">Fills</div>
    <div class="value">{fills_today}</div>
    <div class="sub">today / {fills_all} all-time</div>
  </div>
  <div class="card">
    <div class="label">P&amp;L Today</div>
    <div class="value">{pnl_today:+d}¢</div>
    <div class="sub">realized</div>
  </div>
  <div class="card">
    <div class="label">Open Exposure</div>
    <div class="value">{open_exposure}¢</div>
    <div class="sub">positions</div>
  </div>
  <div class="card">
    <div class="label">Kill Switch</div>
    <div class="value">{_ks_badge(kill_active)}</div>
  </div>
</div>"""

    # --- chart ---
    svg_html = render_hourly_svg(hourly_buckets)
    chart_x_labels = ""
    if hourly_labels:
        step = max(1, len(hourly_labels) // 12)
        sampled = [
            (i, lbl) for i, lbl in enumerate(hourly_labels) if i % step == 0
        ]
        chart_x_labels = "  ".join(lbl for _, lbl in sampled)

    chart_html = f"""\
<div class="chart-wrap">
  {svg_html}
  <div class="chart-label">{chart_x_labels or 'shadow intents per hour — last 7 days'}</div>
</div>"""

    # --- series table ---
    series_trs = ""
    if series_rows:
        for row in series_rows:
            series_trs += (
                f"<tr><td>{row['series']}</td>"
                f"<td>{row['count']}</td>"
                f"<td class='dim'>{row.get('top_tickers','')}</td></tr>\n"
            )
    else:
        series_trs = "<tr><td colspan='3' class='dim'>no data</td></tr>"

    series_table = f"""\
<div class="tbl-wrap">
<table>
<thead><tr><th>Series</th><th>Intents (24h)</th><th>Top Tickers</th></tr></thead>
<tbody>
{series_trs}</tbody>
</table>
</div>"""

    # --- intent table ---
    intent_trs = ""
    if intent_rows:
        for row in intent_rows:
            side_html = _side_span(row.get("side", ""))
            intent_trs += (
                f"<tr>"
                f"<td class='dim'>{_fmt_ts(row.get('ts',''))}</td>"
                f"<td>{row.get('ticker','')}</td>"
                f"<td>{side_html}</td>"
                f"<td>{row.get('price_cents','')}</td>"
                f"<td>{row.get('size','')}</td>"
                f"<td>{row.get('expected_edge_cents','')}</td>"
                f"<td class='dim'>{row.get('reasoning','')[:80]}</td>"
                f"</tr>\n"
            )
    else:
        intent_trs = "<tr><td colspan='7' class='dim'>no shadow intents yet</td></tr>"

    intent_table = f"""\
<div class="tbl-wrap">
<table>
<thead><tr>
  <th>Time</th><th>Ticker</th><th>Side</th>
  <th>Price¢</th><th>Size</th><th>Edge¢</th><th>Reasoning</th>
</tr></thead>
<tbody>
{intent_trs}</tbody>
</table>
</div>"""

    # --- orders table ---
    order_trs = ""
    if order_rows:
        for row in order_rows:
            side_html = _side_span(row.get("side", ""))
            order_trs += (
                f"<tr>"
                f"<td class='dim'>{_fmt_ts(row.get('submitted_at',''))}</td>"
                f"<td class='dim'>{row.get('client_order_id','')[:24]}</td>"
                f"<td>{row.get('ticker','')}</td>"
                f"<td>{side_html}</td>"
                f"<td>{row.get('price_cents','')}</td>"
                f"<td>{row.get('size','')}</td>"
                f"<td>{row.get('status','')}</td>"
                f"</tr>\n"
            )
    else:
        order_trs = "<tr><td colspan='7' class='dim'>no orders yet (shadow mode)</td></tr>"

    order_table = f"""\
<div class="tbl-wrap">
<table>
<thead><tr>
  <th>Time</th><th>Client Order ID</th><th>Ticker</th>
  <th>Side</th><th>Price¢</th><th>Size</th><th>Status</th>
</tr></thead>
<tbody>
{order_trs}</tbody>
</table>
</div>"""

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>predmarkbot</title>
<style>
{_CSS}
</style>
</head>
<body>
<h1>predmarkbot</h1>
<div class="header-line">
  v<span>{version}</span>
  &nbsp;|&nbsp; mode {_mode_badge(mode)}
  &nbsp;|&nbsp; uptime <span>{uptime_str}</span>
  &nbsp;|&nbsp; pod <span>{hostname}</span>
  &nbsp;|&nbsp; refreshed <span>{now_str}</span>
</div>

{cards_html}

<h2>Shadow intents per hour — last 7 days</h2>
{chart_html}

<h2>Per-series fire rate — last 24h</h2>
{series_table}

<h2>Recent shadow intents (last 20)</h2>
{intent_table}

<h2>Recent orders (last 10)</h2>
{order_table}

</body>
</html>
"""


def _fmt_uptime(secs: int) -> str:
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d}d {h:02d}h {m:02d}m"
    return f"{h:02d}h {m:02d}m {s:02d}s"


# ---------------------------------------------------------------------------
# Data queries
# ---------------------------------------------------------------------------


async def _query_data(state: StateStore, mode: str, kill_sentinel_path: str) -> dict[str, Any]:
    """Run all dashboard queries and return a unified data dict."""
    import os

    today = date.today().isoformat()
    hostname = os.environ.get("HOSTNAME") or socket.gethostname()
    kill_active = os.path.exists(kill_sentinel_path)  # noqa: ASYNC240  # one-shot read; sync I/O acceptable

    conn = state.conn

    async def _one(sql: str, params: tuple[Any, ...] = ()) -> Any:
        async with conn.execute(sql, params) as cur:
            row = await cur.fetchone()
        return row

    async def _all(sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return list(rows)

    markets_row = await _one("SELECT count(*) AS c FROM markets")
    markets_watched = int(markets_row[0]) if markets_row else 0

    intents_today_row = await _one(
        "SELECT count(*) AS c FROM shadow_intents WHERE date(ts)=?", (today,)
    )
    intents_today = int(intents_today_row[0]) if intents_today_row else 0

    intents_all_row = await _one("SELECT count(*) AS c FROM shadow_intents")
    intents_all = int(intents_all_row[0]) if intents_all_row else 0

    orders_today_row = await _one(
        "SELECT count(*) AS c FROM orders WHERE date(submitted_at)=?", (today,)
    )
    orders_today = int(orders_today_row[0]) if orders_today_row else 0

    orders_all_row = await _one("SELECT count(*) AS c FROM orders")
    orders_all = int(orders_all_row[0]) if orders_all_row else 0

    fills_today_row = await _one(
        "SELECT count(*) AS c FROM fills WHERE date(filled_at)=?", (today,)
    )
    fills_today = int(fills_today_row[0]) if fills_today_row else 0

    fills_all_row = await _one("SELECT count(*) AS c FROM fills")
    fills_all = int(fills_all_row[0]) if fills_all_row else 0

    pnl_row = await _one(
        "SELECT realized_cents FROM daily_pnl WHERE date=?", (today,)
    )
    pnl_today = int(pnl_row[0]) if pnl_row and pnl_row[0] is not None else 0

    exposure_row = await _one(
        "SELECT COALESCE(SUM(size * avg_price), 0) AS total FROM positions"
    )
    open_exposure = int(exposure_row[0]) if exposure_row else 0

    # Hourly buckets: last 7 days, grouped by hour
    hourly_raw = await _all(
        """
        SELECT strftime('%Y-%m-%d %H', ts) AS bucket, count(*) AS c
        FROM shadow_intents
        WHERE ts >= datetime('now', '-7 days')
        GROUP BY bucket
        ORDER BY bucket
        """
    )
    if hourly_raw:
        # Build a dense list filling in missing hours with 0
        from datetime import timedelta

        start_dt = datetime.now(UTC) - timedelta(days=7)
        end_dt = datetime.now(UTC)
        hour_map: dict[str, int] = {row[0]: int(row[1]) for row in hourly_raw}

        buckets: list[int] = []
        labels: list[str] = []
        cur_dt = start_dt.replace(minute=0, second=0, microsecond=0)
        while cur_dt <= end_dt:
            key = cur_dt.strftime("%Y-%m-%d %H")
            buckets.append(hour_map.get(key, 0))
            labels.append(cur_dt.strftime("%m/%d %Hh"))
            cur_dt += timedelta(hours=1)
    else:
        buckets = []
        labels = []

    # Series fire-rate table: last 24h
    series_raw = await _all(
        """
        SELECT
            m.series_ticker AS series,
            count(*) AS cnt
        FROM shadow_intents si
        JOIN markets m ON m.ticker = si.ticker
        WHERE si.ts >= datetime('now', '-24 hours')
        GROUP BY m.series_ticker
        ORDER BY cnt DESC
        """
    )
    # top 3 tickers per series (last 24h)
    series_rows: list[dict[str, Any]] = []
    for srow in series_raw:
        series_ticker = srow[0]
        count = int(srow[1])
        top_raw = await _all(
            """
            SELECT si.ticker, count(*) AS c
            FROM shadow_intents si
            JOIN markets m ON m.ticker = si.ticker
            WHERE m.series_ticker=? AND si.ts >= datetime('now', '-24 hours')
            GROUP BY si.ticker ORDER BY c DESC LIMIT 3
            """,
            (series_ticker,),
        )
        top_tickers = ", ".join(r[0] for r in top_raw)
        series_rows.append({"series": series_ticker, "count": count, "top_tickers": top_tickers})

    # Recent intents
    intent_raw = await _all(
        """
        SELECT ts, ticker, side, price_cents, size, expected_edge_cents, reasoning
        FROM shadow_intents ORDER BY intent_id DESC LIMIT 20
        """
    )
    intent_rows = [
        {
            "ts": r[0], "ticker": r[1], "side": r[2],
            "price_cents": r[3], "size": r[4],
            "expected_edge_cents": r[5], "reasoning": r[6],
        }
        for r in intent_raw
    ]

    # Recent orders
    order_raw = await _all(
        """
        SELECT submitted_at, client_order_id, ticker, side, price_cents, size, status
        FROM orders ORDER BY submitted_at DESC LIMIT 10
        """
    )
    order_rows = [
        {
            "submitted_at": r[0], "client_order_id": r[1], "ticker": r[2],
            "side": r[3], "price_cents": r[4], "size": r[5], "status": r[6],
        }
        for r in order_raw
    ]

    return {
        "hostname": hostname,
        "mode": mode,
        "version": __version__,
        "kill_active": kill_active,
        "markets_watched": markets_watched,
        "intents_today": intents_today,
        "intents_all": intents_all,
        "orders_today": orders_today,
        "orders_all": orders_all,
        "fills_today": fills_today,
        "fills_all": fills_all,
        "pnl_today": pnl_today,
        "open_exposure": open_exposure,
        "hourly_buckets": buckets,
        "hourly_labels": labels,
        "series_rows": series_rows,
        "intent_rows": intent_rows,
        "order_rows": order_rows,
    }


# ---------------------------------------------------------------------------
# aiohttp app
# ---------------------------------------------------------------------------


def make_app(state: StateStore, mode: str, kill_sentinel_path: str) -> web.Application:
    """Build and return an aiohttp Application."""
    app = web.Application()

    async def handle_index(request: web.Request) -> web.Response:
        del request
        try:
            data = await _query_data(state, mode, kill_sentinel_path)
            html = render_page(data)
            return web.Response(text=html, content_type="text/html")
        except Exception as exc:  # noqa: BLE001
            _log.exception("dashboard render error: %s", exc)
            return web.Response(
                text=f"<pre>Internal error: {exc}</pre>",
                status=500,
                content_type="text/html",
            )

    async def handle_health(request: web.Request) -> web.Response:
        del request
        return web.Response(text="ok\n", content_type="text/plain")

    app.router.add_get("/", handle_index)
    app.router.add_get("/health", handle_health)
    return app
