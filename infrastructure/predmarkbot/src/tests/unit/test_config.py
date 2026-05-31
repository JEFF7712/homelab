from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from predmarkbot.config import Config, Mode, load_config


def test_load_minimal_config(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(dedent("""
        mode: shadow
        kalshi:
          api_base_url: https://demo-api.kalshi.co/trade-api/v2
          ws_base_url: wss://demo-api.kalshi.co/trade-api/ws/v2
          key_id_env: KALSHI_KEY_ID
          private_key_path: /tmp/key.pem
        discovery:
          series: [KXHIGHNY]
        notify:
          ntfy_url: https://ntfy.rupan.dev
          ntfy_topic: predmarkbot
          ntfy_token_env: NTFY_TOKEN
        state:
          db_path: /tmp/state.db
    """))
    cfg = load_config(cfg_path)
    assert isinstance(cfg, Config)
    assert cfg.mode is Mode.SHADOW
    assert cfg.kalshi.api_base_url == "https://demo-api.kalshi.co/trade-api/v2"
    assert cfg.discovery.series == ["KXHIGHNY"]
    # Defaults from spec
    assert cfg.risk.min_edge_cents == 1
    assert cfg.risk.max_per_market_dollars == 50
    assert cfg.risk.max_total_exposure_dollars == 200
    assert cfg.risk.max_orders_per_minute == 30
    assert cfg.risk.max_daily_loss_dollars == 25


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")


def test_mode_prod_requires_explicit_confirm(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(dedent("""
        mode: prod
        kalshi:
          api_base_url: https://api.elections.kalshi.com/trade-api/v2
          ws_base_url: wss://api.elections.kalshi.com/trade-api/ws/v2
          key_id_env: KALSHI_KEY_ID
          private_key_path: /tmp/key.pem
        discovery:
          series: [KXHIGHNY]
        notify:
          ntfy_url: https://ntfy.rupan.dev
          ntfy_topic: predmarkbot
          ntfy_token_env: NTFY_TOKEN
        state:
          db_path: /tmp/state.db
    """))
    with pytest.raises(ValueError, match="prod_confirmed"):
        load_config(cfg_path)
