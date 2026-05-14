"""Broker abstraction. Paper, Angel One live, and others all implement this contract."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from core.types import Order, OrderType, Position, Side


class IBroker(ABC):
    """Minimum surface every broker (paper or live) must implement."""

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: Side,
        qty: int,
        order_type: OrderType,
        stop_loss: float,
        target: Optional[float],
        limit_price: Optional[float] = None,
        strategy: str = "",
    ) -> Order:
        """Submit an order. Implementation must validate and persist."""

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    def get_positions(self) -> list[Position]: ...

    @abstractmethod
    def get_position(self, symbol: str) -> Optional[Position]: ...

    @abstractmethod
    def equity(self) -> float:
        """Total account value (cash + market value of positions)."""

    @abstractmethod
    def cash(self) -> float: ...

    @abstractmethod
    def update_market_prices(self, prices: dict[str, float]) -> None:
        """Push latest LTPs so positions and stop-losses can be evaluated."""
