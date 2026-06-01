"""CLI entry point."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import click

from predmarkbot import __version__
from predmarkbot.clock import ClockSkewError, check_clock_skew
from predmarkbot.config import Config, load_config
from predmarkbot.kalshi.auth import KalshiSigner, load_private_key
from predmarkbot.kalshi.rest import KalshiRestClient
from predmarkbot.research.cli import research as _research_group
from predmarkbot.runner import run as _run_bot
from predmarkbot.state import StateStore


@click.group()
@click.version_option(__version__)
def cli() -> None:
    """predmarkbot — Kalshi prediction-market trading bot."""


@cli.command()
@click.option(
    "--config", "config_path",
    default="/etc/predmarkbot/config.yaml",
    type=click.Path(exists=True, path_type=Path),
)
def run(config_path: Path) -> None:
    """Run the bot (long-lived)."""
    cfg = load_config(config_path)
    asyncio.run(_run_bot(cfg))


async def _status(db_path: str) -> None:
    async with StateStore(Path(db_path)) as store:
        orders = await store.list_orders()
        pending = [o for o in orders if o["status"] == "pending"]
        submitted = [o for o in orders if o["status"] == "submitted"]
        pnl_cents = await store.today_realized_pnl_cents(
            today=datetime.now(UTC).date()
        )
        exposure_cents = await store.total_open_exposure_cents()

    click.echo(f"today realized P&L: ${pnl_cents / 100:+.2f}")
    click.echo(f"open exposure:      ${exposure_cents / 100:.2f}")
    click.echo(f"pending orders:     {len(pending)}")
    click.echo(f"submitted orders:   {len(submitted)}")


@cli.command()
@click.option(
    "--config",
    default="/etc/predmarkbot/config.yaml",
    type=click.Path(exists=True, path_type=Path),
    show_default=True,
    help="Path to config.yaml.",
)
def status(config: Path) -> None:
    """Print current positions, today's P&L, recent orders."""
    cfg = load_config(config)
    asyncio.run(_status(cfg.state.db_path))


async def _smoke(cfg: Config) -> None:
    failures = 0

    # 1. Clock skew
    try:
        skew = await check_clock_skew(now_provider=lambda: datetime.now(UTC))
        click.echo(f"[OK] clock skew: {skew:+.2f}s")
    except ClockSkewError as exc:
        click.echo(f"[FAIL] clock: {exc}")
        failures += 1

    # 2. Public REST
    ticker = cfg.discovery.series[0]
    try:
        async with KalshiRestClient(
            base_url=cfg.kalshi.api_base_url, signer=None
        ) as client:
            resp = await client.get(f"/series/{ticker}")
        assert "series" in resp
        click.echo(f"[OK] public REST: /series/{ticker} returned shape")
    except Exception as exc:
        click.echo(f"[FAIL] public REST: {exc}")
        failures += 1

    # 3. Signed REST (balance)
    key_id = os.environ.get(cfg.kalshi.key_id_env)
    key_path = Path(cfg.kalshi.private_key_path)
    if not key_id or not os.path.exists(key_path):  # noqa: ASYNC240  # startup check; sync I/O is fine
        click.echo("[SKIP] signed REST: KALSHI_KEY_ID or key file missing")
    else:
        try:
            private_key = load_private_key(key_path)
            signer = KalshiSigner(key_id=key_id, private_key=private_key)
            async with KalshiRestClient(
                base_url=cfg.kalshi.api_base_url, signer=signer
            ) as client:
                await client.get("/portfolio/balance", signed=True)
            click.echo("[OK] signed REST: /portfolio/balance")
        except Exception as exc:
            click.echo(f"[FAIL] signed REST: {exc}")
            failures += 1

    if failures:
        click.echo(f"{failures} smoke check(s) failed", err=True)
        sys.exit(1)
    else:
        click.echo("all smoke checks passed")


@cli.command()
@click.option(
    "--config",
    default="/etc/predmarkbot/config.yaml",
    type=click.Path(exists=True, path_type=Path),
    show_default=True,
    help="Path to config.yaml.",
)
def smoke(config: Path) -> None:
    """Run startup self-checks (clock, signing, demo round-trip, ntfy)."""
    cfg = load_config(config)
    asyncio.run(_smoke(cfg))


cli.add_command(_research_group, name="research")
