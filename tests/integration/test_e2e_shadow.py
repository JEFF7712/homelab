from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
from textwrap import dedent

import aiosqlite
import pytest

from predmarkbot.config import load_config
from predmarkbot.runner import run

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_shadow_run_for_60s_records_no_orders(tmp_path: Path) -> None:
    key_id = os.environ.get("KALSHI_KEY_ID")
    key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
    if not key_id or not key_path:
        pytest.skip("KALSHI creds not configured")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        dedent(f"""
        mode: shadow
        kalshi:
          api_base_url: https://demo-api.kalshi.co/trade-api/v2
          ws_base_url: wss://demo-api.kalshi.co/trade-api/ws/v2
          key_id_env: KALSHI_KEY_ID
          private_key_path: {key_path}
        discovery:
          series: [KXHIGHNY]
        state:
          db_path: {tmp_path}/state.db
        notify:
          ntfy_url: https://ntfy.example
          ntfy_topic: predmarkbot
          ntfy_token_env: NTFY_TOKEN
    """)
    )
    cfg = load_config(cfg_path)
    task = asyncio.create_task(run(cfg))
    await asyncio.sleep(60)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, SystemExit):
        await task

    async with aiosqlite.connect(str(tmp_path / "state.db")) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT count(*) FROM orders") as cur:
            row = await cur.fetchone()
            order_count = int(row[0]) if row is not None else 0
        async with conn.execute("SELECT count(*) FROM shadow_intents") as cur:
            row = await cur.fetchone()
            shadow_count = int(row[0]) if row is not None else 0
    assert order_count == 0, "shadow mode must NEVER place real orders"
    # shadow_count >= 0 — may be 0 if no arb detected in 60s window
    _ = shadow_count
