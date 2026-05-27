"""In-memory paper broker. Models slippage, brokerage, and stop-loss/target tracking.

The orchestrator pushes market prices each tick; this broker:
  - fills MARKET orders immediately at price + slippage
  - tracks open positions and applies stop-loss / target exits automatically
  - records realised + unrealised P&L for the persistence layer
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from core.broker.base import IBroker
from core.broker.charges import compute_charges
from core.config import ExecutionCfg
from core.types import Order, OrderStatus, OrderType, Position, Side


@dataclass
class TradeRecord:
    symbol: str
    side: Side
    qty: int
    entry_price: float
    exit_price: float
    pnl: float
    charges: float
    strategy: str
    opened_at: datetime
    closed_at: datetime
    exit_reason: str  # "stop_loss" | "target" | "manual" | "eod_squareoff"


class PaperBroker(IBroker):
    def __init__(self, starting_cash: float, exec_cfg: ExecutionCfg):
        self._cash = starting_cash
        self._exec = exec_cfg
        self._positions: dict[str, Position] = {}
        self._orders: dict[str, Order] = {}
        self._latest_prices: dict[str, float] = {}
        self.trade_log: list[TradeRecord] = []
        self.realized_pnl: float = 0.0
        self.sim_time: datetime | None = None  # set by backtest engine to override wall-clock

    def _now(self) -> datetime:
        return self.sim_time if self.sim_time is not None else datetime.utcnow()

    # ---------- IBroker API ----------

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
        order = Order(
            id=str(uuid.uuid4()),
            symbol=symbol,
            side=side,
            qty=qty,
            order_type=order_type,
            limit_price=limit_price,
            stop_loss=stop_loss,
            target=target,
            strategy=strategy,
        )
        ref_price = self._latest_prices.get(symbol)
        if ref_price is None:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = "no_market_price"
            self._orders[order.id] = order
            return order

        fill_price = self._apply_slippage(ref_price, side)
        cost = qty * fill_price
        charges = compute_charges(
            side=side,
            qty=qty,
            price=fill_price,
            brokerage_per_order_inr=self._exec.brokerage_per_order_inr,
            stt_pct=self._exec.stt_pct,
            exchange_txn_pct=self._exec.exchange_txn_pct,
            gst_pct=self._exec.gst_pct,
        ).total

        if side == Side.BUY:
            if self._cash < cost + charges:
                order.status = OrderStatus.REJECTED
                order.rejection_reason = "insufficient_cash"
                self._orders[order.id] = order
                return order
            self._cash -= cost + charges
            self._open_or_add_long(symbol, qty, fill_price, stop_loss, target, strategy)
        else:  # SELL — close long position
            pos = self._positions.get(symbol)
            if pos is None or pos.qty < qty:
                order.status = OrderStatus.REJECTED
                order.rejection_reason = "no_position_to_sell"
                self._orders[order.id] = order
                return order
            self._close_long(symbol, qty, fill_price, charges, exit_reason="manual")
            self._cash += qty * fill_price - charges

        order.status = OrderStatus.FILLED
        order.filled_price = fill_price
        order.filled_qty = qty
        self._orders[order.id] = order
        return order

    def cancel_order(self, order_id: str) -> bool:
        o = self._orders.get(order_id)
        if not o or o.status != OrderStatus.PENDING:
            return False
        o.status = OrderStatus.CANCELLED
        return True

    def get_positions(self) -> list[Position]:
        return list(self._positions.values())

    def get_position(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)

    def equity(self) -> float:
        mv = sum(p.market_value for p in self._positions.values())
        return self._cash + mv

    def cash(self) -> float:
        return self._cash

    def update_market_prices(self, prices: dict[str, float]) -> None:
        for sym, px in prices.items():
            self._latest_prices[sym] = px
            pos = self._positions.get(sym)
            if pos:
                pos.last_price = px
                self._maybe_exit_on_stop_or_target(pos)

    # ---------- Internals ----------

    def _apply_slippage(self, ref_price: float, side: Side) -> float:
        bps = self._exec.slippage_bps / 10000.0
        return ref_price * (1 + bps) if side == Side.BUY else ref_price * (1 - bps)

    def _open_or_add_long(
        self,
        symbol: str,
        qty: int,
        price: float,
        stop_loss: float,
        target: Optional[float],
        strategy: str,
    ) -> None:
        existing = self._positions.get(symbol)
        if existing:
            new_qty = existing.qty + qty
            existing.avg_price = (existing.avg_price * existing.qty + price * qty) / new_qty
            existing.qty = new_qty
            existing.stop_loss = stop_loss
            existing.target = target
        else:
            self._positions[symbol] = Position(
                symbol=symbol,
                qty=qty,
                avg_price=price,
                stop_loss=stop_loss,
                target=target,
                strategy=strategy,
                opened_at=self._now(),
                last_price=price,
            )

    def _close_long(
        self, symbol: str, qty: int, price: float, charges: float, exit_reason: str
    ) -> None:
        pos = self._positions[symbol]
        pnl = (price - pos.avg_price) * qty - charges
        self.realized_pnl += pnl
        self.trade_log.append(
            TradeRecord(
                symbol=symbol,
                side=Side.SELL,
                qty=qty,
                entry_price=pos.avg_price,
                exit_price=price,
                pnl=pnl,
                charges=charges,
                strategy=pos.strategy,
                opened_at=pos.opened_at,
                closed_at=self._now(),
                exit_reason=exit_reason,
            )
        )
        if qty >= pos.qty:
            del self._positions[symbol]
        else:
            pos.qty -= qty

    def _maybe_exit_on_stop_or_target(self, pos: Position) -> None:
        """Auto-exit on stop-loss or target hit. Called every price update."""
        if pos.last_price <= pos.stop_loss:
            self._auto_exit(pos, pos.stop_loss, "stop_loss")
        elif pos.target is not None and pos.last_price >= pos.target:
            self._auto_exit(pos, pos.target, "target")

    def _auto_exit(self, pos: Position, exit_price: float, reason: str) -> None:
        fill = self._apply_slippage(exit_price, Side.SELL)
        charges = compute_charges(
            side=Side.SELL,
            qty=pos.qty,
            price=fill,
            brokerage_per_order_inr=self._exec.brokerage_per_order_inr,
            stt_pct=self._exec.stt_pct,
            exchange_txn_pct=self._exec.exchange_txn_pct,
            gst_pct=self._exec.gst_pct,
        ).total
        self._close_long(pos.symbol, pos.qty, fill, charges, exit_reason=reason)
        self._cash += pos.qty * fill - charges
