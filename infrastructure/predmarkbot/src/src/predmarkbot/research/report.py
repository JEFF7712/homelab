"""Generate the favorite-longshot research report."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from predmarkbot.research.horizons import HORIZON_OFFSETS
from predmarkbot.research.store import ResearchStore

# Strategy-suggestion threshold: only suggest if bias persists at T-6h+
# with magnitude >= 200 bps in a category with >= 1000 markets.
MIN_BIAS_BPS_FOR_STRATEGY = 200
MIN_MARKETS_FOR_STRATEGY = 1000

# Stratified strategy thresholds
MIN_STRAT_BIAS_BPS = 300
MIN_STRAT_CELL_N = 50
SERIES_DISTANCE_UNIT: dict[str, str] = {
    "KXHIGHNY":  "°F (NYC high temp)",
    "KXHIGHCHI": "°F (Chicago high temp)",
    "KXHIGHLAX": "°F (LAX high temp)",
    "KXHIGHMIA": "°F (Miami high temp)",
    "KXHIGHATL": "°F (Atlanta high temp)",
    "KXHIGHDEN": "°F (Denver high temp)",
    "KXHIGHHOU": "°F (Houston high temp)",
    "KXHIGHPHX": "°F (Phoenix high temp)",
}


async def write_report(*, store: ResearchStore, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240  # setup I/O before any awaits
    (out_dir / "plots").mkdir(exist_ok=True)  # noqa: ASYNC240  # setup I/O before any awaits

    async with store.conn.execute(
        "SELECT * FROM bucket_stats ORDER BY horizon, category, bucket_lo"
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]

    # Stratified heatmaps
    async with store.conn.execute(
        "SELECT * FROM strat_bucket_stats"
    ) as cur:
        strat_rows = [dict(r) for r in await cur.fetchall()]
    if strat_rows:
        strat_series = sorted({str(r["series_ticker"]) for r in strat_rows})
        strat_horizons = sorted({str(r["horizon"]) for r in strat_rows})
        for s in strat_series:
            for h in strat_horizons:
                _write_strat_heatmap(
                    rows=strat_rows, horizon=h, series_ticker=s, out_dir=out_dir,
                )

    if not rows and not strat_rows:
        (out_dir / "report.md").write_text(
            "# Favorite-Longshot Bias — Report\n\n"
            "no data; run `predmarkbot research pull` and "
            "`predmarkbot research analyze` first.\n"
        )
        return

    today = datetime.now(UTC).date().isoformat()
    md: list[str] = []
    md.append(f"# Favorite-Longshot Bias — Report ({today})\n")

    if rows:
        md.append("## Summary\n")

        # Dataset stats
        async with store.conn.execute(
            "SELECT category, count(*) AS n FROM markets "
            "WHERE result IN ('yes','no') GROUP BY category ORDER BY n DESC"
        ) as cur:
            by_cat = [(str(r["category"]), int(r["n"])) for r in await cur.fetchall()]
        total = sum(n for _, n in by_cat)
        md.append(f"- Total resolved markets analyzed: **{total}**")
        md.append("- Breakdown by category:\n")
        for cat, n in by_cat:
            md.append(f"  - `{cat}`: {n}")
        md.append("")

        # Cross-horizon table for ALL category
        md.append("## Cross-horizon bias table (all categories combined)\n")
        horizon_order = list(HORIZON_OFFSETS.keys())
        md.append(
            "| Bucket | " + " | ".join(horizon_order) + " |"
        )
        md.append("|" + "---|" * (1 + len(horizon_order)))
        all_rows = [r for r in rows if r["category"] == "ALL"]
        by_bucket: dict[int, dict[str, dict]] = {}  # type: ignore[type-arg]
        for r in all_rows:
            by_bucket.setdefault(int(r["bucket_lo"]), {})[str(r["horizon"])] = r
        for lo in sorted(by_bucket):
            cells = [f"{lo:2d}-{lo+5:2d}¢"]
            for h in horizon_order:
                cell_row = by_bucket[lo].get(h)
                if cell_row is None:
                    cells.append("—")
                else:
                    bias = int(cell_row["bias_bps"])
                    p = float(cell_row["p_value"])
                    n = int(cell_row["n_markets"])
                    if n < 30:
                        cells.append(f"(n={n})")
                    else:
                        marker = "**" if p < 0.01 else ""
                        cells.append(f"{marker}{bias:+d} bps{marker} (n={n})")
            md.append("| " + " | ".join(cells) + " |")
        md.append("")

    # ----- Stratified section -----
    if strat_rows:
        md.append("## Stratified longshot bias\n")
        per_series_n: dict[str, int] = {}
        for r in strat_rows:
            per_series_n[str(r["series_ticker"])] = (
                per_series_n.get(str(r["series_ticker"]), 0)
                + int(r["n_markets"])
            )
        for s in sorted(per_series_n):
            total_s = per_series_n[s]
            if total_s >= 1000:
                md.append(f"### {s} ({total_s} observations)\n")
                for h in sorted({str(r["horizon"]) for r in strat_rows
                                 if r["series_ticker"] == s}):
                    md.append(f"![{s} {h}](plots/strat_{s}_{h}.png)\n")
            else:
                md.append(
                    f"- *{s}: {total_s} stratified observations — "
                    f"too few for plot; see notebook 04 for raw data.*"
                )
        md.append("")

    # ----- Suggested strategies (existing + new v2) -----
    md.append("## Suggested strategies\n")
    sug = _suggest_strategies(rows)
    sug_v2 = _suggest_strat_strategies(strat_rows) if strat_rows else []
    if not sug and not sug_v2:
        md.append(
            "No bias pattern met the strategy threshold "
            f"(≥{MIN_BIAS_BPS_FOR_STRATEGY} bps un-stratified or "
            f"≥{MIN_STRAT_BIAS_BPS} bps stratified, persistent at T-6h+, "
            f"≥{MIN_MARKETS_FOR_STRATEGY} markets / "
            f"≥{MIN_STRAT_CELL_N} per cell).\n"
        )
    else:
        for s in sug:
            md.append(s)
            md.append("")
        for s in sug_v2:
            md.append(s)
            md.append("")

    if rows:
        horizons = sorted({str(r["horizon"]) for r in rows})
        categories = sorted({str(r["category"]) for r in rows})
        for h in horizons:
            for c in categories:
                _write_plot(rows=rows, horizon=h, category=c, out_dir=out_dir)
        if horizons and categories:
            md.append("## Bias curves\n")
            for h in horizons:
                md.append(f"### Horizon: {h}\n")
                md.append(f"![bias {h}](plots/bias_{h}_ALL.png)\n")

    (out_dir / "report.md").write_text("\n".join(md) + "\n")


def _write_plot(
    *, rows: list[dict], horizon: str, category: str, out_dir: Path  # type: ignore[type-arg]
) -> None:
    import matplotlib  # lazy: not installed in bot container
    matplotlib.use("Agg")  # headless backend; must come before pyplot
    import matplotlib.pyplot as plt  # noqa: E402

    filtered = [
        r for r in rows
        if r["horizon"] == horizon and r["category"] == category
        and int(r["n_markets"]) >= 30
    ]
    if not filtered:
        return
    filtered.sort(key=lambda r: int(r["bucket_lo"]))
    xs = [(int(r["bucket_lo"]) + int(r["bucket_hi"])) / 2 for r in filtered]
    bias = [int(r["bias_bps"]) / 100.0 for r in filtered]  # bps -> cents
    lo_band = [(float(r["ci_lo"]) - float(r["expected_rate"])) * 100 for r in filtered]
    hi_band = [(float(r["ci_hi"]) - float(r["expected_rate"])) * 100 for r in filtered]
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.fill_between(xs, lo_band, hi_band, alpha=0.25, label="95% Wilson CI")
    ax.plot(xs, bias, "o-", label="realized − expected (¢)")
    ax.axhline(0, linestyle="--", linewidth=0.8)
    ax.set_xlabel("Bucket midpoint price (¢)")
    ax.set_ylabel("Bias (realized − expected, ¢)")
    ax.set_title(f"Bias curve · horizon={horizon} · category={category}")
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(out_dir / "plots" / f"bias_{horizon}_{category}.png", dpi=120)
    plt.close(fig)


def _write_strat_heatmap(
    *, rows: list[dict], horizon: str,  # type: ignore[type-arg]
    series_ticker: str, out_dir: Path,
) -> None:
    import matplotlib  # lazy: not installed in bot container
    matplotlib.use("Agg")  # headless backend; must come before pyplot
    import matplotlib.pyplot as plt  # noqa: E402

    cells = [
        r for r in rows
        if r["horizon"] == horizon
        and r["series_ticker"] == series_ticker
        and int(r["n_markets"]) >= 30
    ]
    if not cells:
        return
    price_buckets = sorted({int(r["price_bucket_lo"]) for r in cells})
    dist_buckets = sorted({int(r["distance_bucket_idx"]) for r in cells})
    by_cell = {
        (int(r["price_bucket_lo"]), int(r["distance_bucket_idx"])): r
        for r in cells
    }
    bias_grid: list[list[float]] = []
    for pb in price_buckets:
        row: list[float] = []
        for db in dist_buckets:
            cell = by_cell.get((pb, db))
            row.append(
                float(cell["bias_bps"]) if cell is not None else float("nan")
            )
        bias_grid.append(row)

    fig, ax = plt.subplots(
        figsize=(max(6, 0.8 * len(dist_buckets)), max(4, 0.4 * len(price_buckets)))
    )
    im = ax.imshow(
        bias_grid, aspect="auto", origin="lower",
        cmap="RdBu_r",
    )
    ax.set_xticks(range(len(dist_buckets)))
    ax.set_xticklabels([str(d) for d in dist_buckets])
    ax.set_yticks(range(len(price_buckets)))
    ax.set_yticklabels([f"{pb}-{pb + 5}¢" for pb in price_buckets])
    ax.set_xlabel("Distance from implied median (strike-steps)")
    ax.set_ylabel("Price bucket (yes ¢)")
    ax.set_title(f"Bias heatmap · {series_ticker} · {horizon}")
    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label("bias (bps)")
    # Annotate cells with n
    for i, pb in enumerate(price_buckets):
        for j, db in enumerate(dist_buckets):
            cell = by_cell.get((pb, db))
            if cell is not None:
                ax.text(
                    j, i, f"n={int(cell['n_markets'])}",
                    ha="center", va="center", fontsize=7,
                )
    fig.tight_layout()
    fig.savefig(
        out_dir / "plots" / f"strat_{series_ticker}_{horizon}.png", dpi=120
    )
    plt.close(fig)


def _suggest_strat_strategies(rows: list[dict[str, object]]) -> list[str]:
    out: list[str] = []
    persistence_horizons = {"T-24h", "T-6h", "T-1h"}
    by_triple: dict[tuple[str, int, int], dict[str, dict[str, object]]] = {}
    for r in rows:
        series = str(r["series_ticker"])
        price_lo = int(str(r["price_bucket_lo"]))
        dist_idx = int(str(r["distance_bucket_idx"]))
        by_triple.setdefault((series, price_lo, dist_idx), {})[
            str(r["horizon"])
        ] = r
    for (series, price_lo, dist_idx), per_h in sorted(by_triple.items()):
        eligible = {h: per_h[h] for h in persistence_horizons if h in per_h}
        if len(eligible) < 2:
            continue
        biases = [int(str(eligible[h]["bias_bps"])) for h in eligible]
        ns = [int(str(eligible[h]["n_markets"])) for h in eligible]
        ps = [float(str(eligible[h]["p_value"])) for h in eligible]
        same_sign = all((b > 0) == (biases[0] > 0) for b in biases)
        passes = (
            same_sign
            and min(abs(b) for b in biases) >= MIN_STRAT_BIAS_BPS
            and min(ns) >= MIN_STRAT_CELL_N
            and max(ps) < 0.01
        )
        if not passes:
            continue
        side = "YES" if biases[0] > 0 else "NO"
        step_units = SERIES_DISTANCE_UNIT.get(series, "strike-steps")
        out.append(
            f"### `{series}` — price bucket {price_lo}-{price_lo + 5}¢, "
            f"distance bucket {dist_idx:+d} {step_units}\n"
            f"Buy **{side}** when a market in this cell is observed at "
            f"any of {sorted(eligible)}. Persistent bias of "
            f"{biases[0]:+d} bps across horizons; min n={min(ns)}; "
            f"max p={max(ps):.1e}."
        )
    return out


def _suggest_strategies(rows: list[dict]) -> list[str]:  # type: ignore[type-arg]
    out: list[str] = []
    actionable_horizons = {"T-6h", "T-24h", "T-7d"}
    # group by (category, bucket_lo) -> {horizon: row}
    grouped: dict[tuple[str, int], dict[str, dict]] = {}  # type: ignore[type-arg]
    for r in rows:
        grouped.setdefault(
            (str(r["category"]), int(r["bucket_lo"])), {},
        )[str(r["horizon"])] = r
    for (category, lo), per_h in grouped.items():
        if category == "ALL":
            continue
        ah = [h for h in actionable_horizons if h in per_h]
        if not ah:
            continue
        biases = [int(per_h[h]["bias_bps"]) for h in ah]
        ns = [int(per_h[h]["n_markets"]) for h in ah]
        if all(abs(b) >= MIN_BIAS_BPS_FOR_STRATEGY for b in biases) and all(
            (b > 0) == (biases[0] > 0) for b in biases
        ) and min(ns) >= MIN_MARKETS_FOR_STRATEGY:
            side = "NO" if biases[0] < 0 else "YES"
            out.append(
                f"### `{category}` bucket {lo}-{lo+5}¢\n"
                f"Buy **{side}** when price is in this bucket at any of "
                f"{sorted(ah)}. Persistent bias of "
                f"{biases[0]:+d} bps across horizons with n≥{min(ns)} markets."
            )
    return out
