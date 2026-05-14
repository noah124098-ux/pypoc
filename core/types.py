"""Shared domain types used across modules."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    PARTIAL = "PARTIAL"


class Regime(str, Enum):
    TREND = "TREND"
    RANGE = "RANGE"
    VOLATILE = "VOLATILE"
    UNKNOWN = "UNKNOWN"


@dataclass
class Tick:
    symbol: str
    ltp: float
    bid: float
    ask: float
    volume: int
    ts: datetime


@dataclass
class Candle:
    symbol: str
    interval: str  # "1m" | "5m" | "15m" | "1d"
    open: float
    high: float
    low: float
    close: float
    volume: int
    ts: datetime


@dataclass
class Signal:
    symbol: str
    side: Side
    strategy: str
    regime: Regime
    entry_price: float
    stop_loss: float
    target: float
    confidence: float = 0.5
    rationale: str = ""
    ts: datetime = field(default_factory=datetime.utcnow)

    @property
    def risk_per_share(self) -> float:
        if self.side == Side.BUY:
            return max(0.0, self.entry_price - self.stop_loss)
        return max(0.0, self.stop_loss - self.entry_price)


@dataclass
class Order:
    id: str
    symbol: str
    side: Side
    qty: int
    order_type: OrderType
    limit_price: Optional[float]
    stop_loss: float
    target: Optional[float]
    status: OrderStatus = OrderStatus.PENDING
    filled_price: Optional[float] = None
    filled_qty: int = 0
    rejection_reason: Optional[str] = None
    strategy: str = ""
    ts: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Position:
    symbol: str
    qty: int
    avg_price: float
    stop_loss: float
    target: Optional[float]
    strategy: str
    opened_at: datetime
    last_price: float = 0.0

    @property
    def market_value(self) -> float:
        return self.qty * (self.last_price or self.avg_price)

    @property
    def unrealized_pnl(self) -> float:
        return self.qty * ((self.last_price or self.avg_price) - self.avg_price)
