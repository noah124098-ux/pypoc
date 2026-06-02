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
from typing import Callable, Optional

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
        self._positions: dict[str, Position] = {}        # long positions
        self._short_positions: dict[str, Position] = {}  # short positions
        self._orders: dict[str, Order] = {}
        self._latest_prices: dict[str, float] = {}
        self.trade_log: list[TradeRecord] = []
        self.realized_pnl: float = 0.0
        self.sim_time: datetime | None = None  # set by backtest engine to override wall-clock
        self.on_exit: Optional[Callable[[str, float, str, str], None]] = None  # set by orchestrator

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
        # Detect if this BUY is covering an open short (affects STT)
        _is_short_cover = side == Side.BUY and symbol in self._short_positions
        charges = compute_charges(
            side=side,
            qty=qty,
            price=fill_price,
            brokerage_per_order_inr=self._exec.brokerage_per_order_inr,
            stt_pct=self._exec.stt_pct,
            exchange_txn_pct=self._exec.exchange_txn_pct,
            gst_pct=self._exec.gst_pct,
            is_short_cover=_is_short_cover,
        ).total

        if side == Side.BUY:
            long_pos = self._positions.get(symbol)
            short_pos = self._short_positions.get(symbol)
            if short_pos is not None:
                # Covering a short position
                self._cover_short(symbol, qty, fill_price, charges, exit_reason="manual")
                self._cash -= qty * fill_price + charges
            else:
                # Opening / adding to a long position
                if self._cash < cost + charges:
                    order.status = OrderStatus.REJECTED
                    order.rejection_reason = "insufficient_cash"
                    self._orders[order.id] = order
                    return order
                self._cash -= cost + charges
                self._open_or_add_long(symbol, qty, fill_price, stop_loss, target, strategy)
        else:  # SELL
            long_pos = self._positions.get(symbol)
            if long_pos is not None:
                # Closing a long position
                if long_pos.qty < qty:
                    order.status = OrderStatus.REJECTED
                    order.rejection_reason = "no_position_to_sell"
                    self._orders[order.id] = order
                    return order
                self._close_long(symbol, qty, fill_price, charges, exit_reason="manual")
                self._cash += qty * fill_price - charges
            else:
                # Opening a short position — margin = cost (simplified: full notional held)
                if self._cash < cost + charges:
                    order.status = OrderStatus.REJECTED
                    order.rejection_reason = "insufficient_cash"
                    self._orders[order.id] = order
                    return order
                self._cash -= cost + charges  # margin held
                self._open_short(symbol, qty, fill_price, stop_loss, target, strategy)

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
        return list(self._positions.values()) + list(self._short_positions.values())

    def get_position(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol) or self._short_positions.get(symbol)

    def equity(self) -> float:
        long_mv = sum(p.market_value for p in self._positions.values())
        # Short unrealised P&L: entry_price - last_price (profit when price falls)
        short_pnl = sum(
            (p.avg_price - p.last_price) * p.qty for p in self._short_positions.values()
        )
        return self._cash + long_mv + short_pnl

    def cash(self) -> float:
        return self._cash

    def update_market_prices(self, prices: dict[str, float]) -> None:
        for sym, px in prices.items():
            self._latest_prices[sym] = px
            pos = self._positions.get(sym)
            if pos:
                pos.last_price = px
                self._maybe_exit_on_stop_or_target(pos)
            spos = self._short_positions.get(sym)
            if spos:
                spos.last_price = px
                self._maybe_exit_short_on_stop_or_target(spos)

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
        if self.on_exit is not None:
            self.on_exit(symbol, pnl, exit_reason, pos.strategy)
        if qty >= pos.qty:
            del self._positions[symbol]
        else:
            pos.qty -= qty

    def _maybe_exit_on_stop_or_target(self, pos: Position) -> None:
        """Auto-exit long on stop-loss or target hit."""
        if pos.last_price <= pos.stop_loss:
            self._auto_exit(pos, pos.stop_loss, "stop_loss")
        elif pos.target is not None and pos.last_price >= pos.target:
            self._auto_exit(pos, pos.target, "target")

    def _auto_exit(self, pos: Position, exit_price: float, reason: str) -> None:
        fill = self._apply_slippage(exit_price, Side.SELL)
        charges = compute_charges(
            side=Side.SELL, qty=pos.qty, price=fill,
            brokerage_per_order_inr=self._exec.brokerage_per_order_inr,
            stt_pct=self._exec.stt_pct, exchange_txn_pct=self._exec.exchange_txn_pct,
            gst_pct=self._exec.gst_pct,
        ).total
        self._close_long(pos.symbol, pos.qty, fill, charges, exit_reason=reason)
        self._cash += pos.qty * fill - charges

    def _open_short(
        self, symbol: str, qty: int, price: float,
        stop_loss: float, target: Optional[float], strategy: str,
    ) -> None:
        self._short_positions[symbol] = Position(
            symbol=symbol, qty=qty, avg_price=price,
            stop_loss=stop_loss, target=target, strategy=strategy,
            opened_at=self._now(), last_price=price,
        )

    def _cover_short(
        self, symbol: str, qty: int, price: float, charges: float, exit_reason: str
    ) -> None:
        pos = self._short_positions[symbol]
        pnl = (pos.avg_price - price) * qty - charges  # profit when price falls
        self.realized_pnl += pnl
        # Return held margin + unrealised P&L
        self._cash += pos.qty * pos.avg_price + pnl
        self.trade_log.append(TradeRecord(
            symbol=symbol, side=Side.BUY, qty=qty,
            entry_price=pos.avg_price, exit_price=price,
            pnl=pnl, charges=charges, strategy=pos.strategy,
            opened_at=pos.opened_at, closed_at=self._now(), exit_reason=exit_reason,
        ))
        if self.on_exit is not None:
            self.on_exit(symbol, pnl, exit_reason, pos.strategy)
        if qty >= pos.qty:
            del self._short_positions[symbol]
        else:
            pos.qty -= qty

    def _maybe_exit_short_on_stop_or_target(self, pos: Position) -> None:
        """Auto-exit short: stop = price rises to stop_loss, target = price falls to target."""
        if pos.last_price >= pos.stop_loss:
            self._auto_exit_short(pos, pos.stop_loss, "stop_loss")
        elif pos.target is not None and pos.last_price <= pos.target:
            self._auto_exit_short(pos, pos.target, "target")

    def _auto_exit_short(self, pos: Position, exit_price: float, reason: str) -> None:
        fill = self._apply_slippage(exit_price, Side.BUY)  # covering = BUY
        charges = compute_charges(
            side=Side.BUY, qty=pos.qty, price=fill,
            brokerage_per_order_inr=self._exec.brokerage_per_order_inr,
            stt_pct=self._exec.stt_pct, exchange_txn_pct=self._exec.exchange_txn_pct,
            gst_pct=self._exec.gst_pct,
            is_short_cover=True,
        ).total
        self._cover_short(pos.symbol, pos.qty, fill, charges, exit_reason=reason)
