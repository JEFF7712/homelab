"""Click subcommands for `predmarkbot research`."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import click

from predmarkbot.kalshi.auth import KalshiSigner, load_private_key
from predmarkbot.kalshi.rest import KalshiRestClient
from predmarkbot.research.analyze import (
    rebuild_bucket_stats,
    rebuild_horizon_prices,
    rebuild_strat_bucket_stats,
)
from predmarkbot.research.fetch import pull_all
from predmarkbot.research.report import write_report
from predmarkbot.research.store import ResearchStore

_log = logging.getLogger(__name__)


_DEMO_BASE = "https://demo-api.kalshi.co/trade-api/v2"
_PROD_BASE = "https://api.elections.kalshi.com/trade-api/v2"


def _default_db() -> Path:
    override = os.environ.get("PREDMARKBOT_RESEARCH_DB")
    if override:
        return Path(override)
    base = Path(
        os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))
    )
    return base / "predmarkbot" / "research.db"


def _resolve_signer(env: str) -> KalshiSigner | None:
    """Build a KalshiSigner from env vars; return None if not configured.

    Looks for:
      env == "prod"  -> KALSHI_PROD_KEY_ID + KALSHI_PROD_PRIVATE_KEY_PATH
      env == "demo"  -> KALSHI_DEMO_KEY_ID + KALSHI_DEMO_PRIVATE_KEY_PATH
    Falls back to KALSHI_KEY_ID + KALSHI_PRIVATE_KEY_PATH if the env-specific
    vars are absent.
    """
    prefix = env.upper()
    key_id = os.environ.get(f"KALSHI_{prefix}_KEY_ID") or os.environ.get(
        "KALSHI_KEY_ID"
    )
    key_path = os.environ.get(
        f"KALSHI_{prefix}_PRIVATE_KEY_PATH"
    ) or os.environ.get("KALSHI_PRIVATE_KEY_PATH")
    if not key_id or not key_path:
        return None
    return KalshiSigner(key_id=key_id, private_key=load_private_key(Path(key_path)))


@click.group()
def research() -> None:
    """Historical Kalshi data + offline analysis."""


@research.command()
@click.option(
    "--from", "from_date",
    default=None,
    help="ISO date (UTC) to start from. Defaults to today-180d.",
)
@click.option(
    "--to", "to_date",
    default=None,
    help="ISO date (UTC) to end at. Defaults to today.",
)
@click.option(
    "--categories",
    default=None,
    help="Comma-separated category filter (default: all categories).",
)
@click.option(
    "--series",
    default=None,
    help="Comma-separated series_ticker filter (server-side; recommended for "
    "tight scopes like 'KXHIGHNY').",
)
@click.option("--refetch", is_flag=True, help="Re-fetch even if covered.")
@click.option(
    "--rate",
    type=float,
    default=2.0,
    help="Sustained requests/sec to Kalshi (default: 2).",
)
@click.option(
    "--env",
    type=click.Choice(["demo", "prod"]),
    default="demo",
    help="Which Kalshi environment to pull from (default: demo).",
)
def pull(
    from_date: str | None,
    to_date: str | None,
    categories: str | None,
    series: str | None,
    refetch: bool,
    rate: float,
    env: str,
) -> None:
    """Fetch resolved markets + hourly candlesticks into the research DB."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    now = datetime.now(UTC)
    from_iso = (
        from_date + "T00:00:00Z" if from_date
        else (now - timedelta(days=180)).strftime("%Y-%m-%dT00:00:00Z")
    )
    to_iso = (
        to_date + "T00:00:00Z" if to_date
        else now.strftime("%Y-%m-%dT00:00:00Z")
    )
    cat_set = set(categories.split(",")) if categories else None
    ser_set = set(series.split(",")) if series else None
    asyncio.run(_run_pull(
        from_iso=from_iso, to_iso=to_iso,
        categories=cat_set, series=ser_set,
        refetch=refetch, rate=rate, env=env,
    ))


async def _run_pull(
    *, from_iso: str, to_iso: str,
    categories: set[str] | None, series: set[str] | None,
    refetch: bool, rate: float, env: str,
) -> None:
    base_url = _PROD_BASE if env == "prod" else _DEMO_BASE
    signer = _resolve_signer(env)
    _log.info(
        "research pull env=%s url=%s signed=%s",
        env, base_url, signer is not None,
    )
    async with (
        KalshiRestClient(base_url=base_url, signer=signer) as rest,
        ResearchStore(_default_db()) as store,
    ):
        n_m, n_c = await pull_all(
            rest=rest, store=store,
            from_close=from_iso, to_close=to_iso,
            categories=categories, series=series,
            refetch=refetch, rate_per_sec=rate,
        )
    click.echo(f"pulled {n_m} markets, {n_c} candle-fetches")


@research.command()
def analyze() -> None:
    """Rebuild horizon_prices + bucket_stats from source tables."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    asyncio.run(_run_analyze())


async def _run_analyze() -> None:
    async with ResearchStore(_default_db()) as store:
        n_h = await rebuild_horizon_prices(store=store)
        n_b = await rebuild_bucket_stats(store=store)
        n_s = await rebuild_strat_bucket_stats(store=store)
    click.echo(
        f"rebuilt {n_h} horizon_prices, {n_b} bucket_stats, {n_s} strat_bucket_stats"
    )


@research.command()
@click.option(
    "--out", "out_path",
    default=None, type=click.Path(path_type=Path),
    help="Output dir. Defaults to docs/research/YYYY-MM-DD-favorite-longshot/.",
)
def report(out_path: Path | None) -> None:
    """Write report.md + plots PNGs to a dated directory."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    today = datetime.now(UTC).date().isoformat()
    out = out_path or Path("docs/research") / f"{today}-favorite-longshot"
    asyncio.run(_run_report(out_dir=out))
    click.echo(f"report written to {out}")


async def _run_report(*, out_dir: Path) -> None:
    async with ResearchStore(_default_db()) as store:
        await write_report(store=store, out_dir=out_dir)


@research.command(name="run")
@click.option("--from", "from_date", default=None)
@click.option("--to", "to_date", default=None)
@click.option("--categories", default=None)
@click.option("--series", "series", default=None)
@click.option("--refetch", is_flag=True)
@click.option("--rate", type=float, default=2.0)
@click.option(
    "--env", type=click.Choice(["demo", "prod"]), default="demo",
)
@click.option("--out", "out_path", default=None, type=click.Path(path_type=Path))
@click.pass_context
def run_all(
    ctx: click.Context,
    from_date: str | None, to_date: str | None,
    categories: str | None, series: str | None,
    refetch: bool, rate: float, env: str,
    out_path: Path | None,
) -> None:
    """End-to-end: pull → analyze → report."""
    ctx.invoke(
        pull,
        from_date=from_date, to_date=to_date,
        categories=categories, series=series,
        refetch=refetch, rate=rate, env=env,
    )
    ctx.invoke(analyze)
    ctx.invoke(report, out_path=out_path)
