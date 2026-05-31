"""Abstract Strategy interface."""
from __future__ import annotations

import abc

from predmarkbot.events import OrderbookUpdate, TradeIntent


class Strategy(abc.ABC):
    @abc.abstractmethod
    async def on_update(self, update: OrderbookUpdate) -> list[TradeIntent]: ...
