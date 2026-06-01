"""Configuration schema and loader."""
from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class Mode(StrEnum):
    SHADOW = "shadow"
    DEMO = "demo"
    PROD = "prod"


class KalshiConfig(BaseModel):
    api_base_url: str
    ws_base_url: str
    key_id_env: str
    private_key_path: str


class DiscoveryConfig(BaseModel):
    series: list[str]
    poll_interval_seconds: int = 300


class FeedConfig(BaseModel):
    reconcile_interval_seconds: int = 60
    ws_reconnect_max_backoff_seconds: int = 60
    drift_threshold_per_10min: int = 3


class RiskConfig(BaseModel):
    min_edge_cents: int = 1
    max_per_market_dollars: int = 50
    max_total_exposure_dollars: int = 200
    max_orders_per_minute: int = 30
    max_daily_loss_dollars: int = 25
    max_intent_size: int = 10  # contracts per intent


class StrategyConfig(BaseModel):
    type: Literal["longshot"] = "longshot"
    size_contracts: int = 5
    max_price_cents: int = 5
    min_seconds_to_close: int = 3600
    max_seconds_to_close: int = 86400
    historical_yes_rate: float = 0.14
    series_allowlist: list[str] = Field(default_factory=lambda: [
        "KXHIGHNY", "KXHIGHCHI", "KXHIGHLAX", "KXHIGHMIA", "KXHIGHDEN", "KXHIGHHOU",
        "KXLOWNY", "KXLOWCHI", "KXLOWLAX", "KXLOWMIA", "KXLOWDEN",
    ])


class StateConfig(BaseModel):
    db_path: str


class NotifyConfig(BaseModel):
    ntfy_url: str
    ntfy_topic: str
    ntfy_token_env: str


class DashboardConfig(BaseModel):
    enabled: bool = True
    port: int = 8080


class Config(BaseModel):
    mode: Mode
    kalshi: KalshiConfig
    discovery: DiscoveryConfig
    feed: FeedConfig = Field(default_factory=FeedConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    state: StateConfig
    notify: NotifyConfig
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    prod_confirmed: bool = False

    @model_validator(mode="after")
    def _require_prod_confirmation(self) -> Config:
        if self.mode is Mode.PROD and not self.prod_confirmed:
            raise ValueError(
                "mode=prod requires `prod_confirmed: true` in config (safety gate)"
            )
        return self


def load_config(path: Path) -> Config:
    """Load and validate config.yaml. Raises if missing or invalid."""
    text = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    return Config.model_validate(raw)
