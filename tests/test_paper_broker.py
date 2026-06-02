"""Tests for the paper broker — fills, slippage, stop-loss auto-exit, target auto-exit."""
from __future__ import annotations

import pytest

from core.broker.paper import PaperBroker
from core.types import OrderStatus, OrderType, Side


@pytest.fixture
def broker(execution_cfg) -> PaperBroker:
    b = PaperBroker(starting_cash=100_000.0, exec_cfg=execution_cfg)
    b.update_market_prices({"RELIANCE": 1300.0})
    return b


def test_buy_then_position_opens(broker):
    order = broker.place_order(
        symbol="RELIANCE", side=Side.BUY, qty=10, order_type=OrderType.MARKET,
        stop_loss=1280, target=1360, strategy="t",
    )
    assert order.status == OrderStatus.FILLED
    assert order.filled_qty == 10
    pos = broker.get_position("RELIANCE")
    assert pos is not None and pos.qty == 10


def test_insufficient_cash_rejected(execution_cfg):
    b = PaperBroker(starting_cash=1000.0, exec_cfg=execution_cfg)
    b.update_market_prices({"RELIANCE": 1300.0})
    order = b.place_order("RELIANCE", Side.BUY, 10, OrderType.MARKET, 1280, 1360)
    assert order.status == OrderStatus.REJECTED
    assert order.rejection_reason == "insufficient_cash"


def test_no_market_price_rejected(execution_cfg):
    b = PaperBroker(starting_cash=100_000.0, exec_cfg=execution_cfg)
    order = b.place_order("UNKNOWN", Side.BUY, 1, OrderType.MARKET, 95, 110)
    assert order.status == OrderStatus.REJECTED
    assert order.rejection_reason == "no_market_price"


def test_stop_loss_auto_exits(broker):
    broker.place_order("RELIANCE", Side.BUY, 10, OrderType.MARKET, 1280, 1360, strategy="t")
    broker.update_market_prices({"RELIANCE": 1279.0})  # below stop
    assert broker.get_position("RELIANCE") is None
    assert len(broker.trade_log) == 1
    assert broker.trade_log[0].exit_reason == "stop_loss"


def test_target_auto_exits(broker):
    broker.place_order("RELIANCE", Side.BUY, 10, OrderType.MARKET, 1280, 1360, strategy="t")
    broker.update_market_prices({"RELIANCE": 1361.0})  # above target
    assert broker.get_position("RELIANCE") is None
    assert broker.trade_log[0].exit_reason == "target"


def test_slippage_applied_on_buy(broker):
    order = broker.place_order("RELIANCE", Side.BUY, 1, OrderType.MARKET, 1280, 1360)
    # 5 bps slippage upward on buy => 1300 * 1.0005
    assert order.filled_price == pytest.approx(1300 * 1.0005)


def test_equity_includes_unrealized(broker):
    broker.place_order("RELIANCE", Side.BUY, 10, OrderType.MARKET, 1280, 1360)
    broker.update_market_prices({"RELIANCE": 1320.0})
    eq = broker.equity()
    assert eq > 100_000  # cash dropped but mark-up exceeds it


# ---------- on_exit callback tests ----------

def test_on_exit_callback_fires_on_stop_loss(broker):
    """on_exit must be called with (symbol, pnl, 'stop_loss', strategy) on auto-exit."""
    calls = []
    broker.on_exit = lambda sym, pnl, reason, strat: calls.append((sym, pnl, reason, strat))

    broker.place_order("RELIANCE", Side.BUY, 10, OrderType.MARKET, 1280, 1360, strategy="t")
    broker.update_market_prices({"RELIANCE": 1279.0})  # trigger stop loss

    assert len(calls) == 1
    sym, pnl, reason, strat = calls[0]
    assert sym == "RELIANCE"
    assert reason == "stop_loss"
    assert strat == "t"
    assert isinstance(pnl, float)


def test_on_exit_callback_fires_on_target(broker):
    """on_exit must be called with exit_reason='target' on target hit."""
    calls = []
    broker.on_exit = lambda sym, pnl, reason, strat: calls.append((sym, pnl, reason, strat))

    broker.place_order("RELIANCE", Side.BUY, 10, OrderType.MARKET, 1280, 1360, strategy="tb")
    broker.update_market_prices({"RELIANCE": 1361.0})  # trigger target

    assert len(calls) == 1
    assert calls[0][2] == "target"


def test_on_exit_callback_fires_on_short_stop_loss(execution_cfg):
    """on_exit fires for short positions auto-exited via stop loss."""
    b = PaperBroker(starting_cash=200_000.0, exec_cfg=execution_cfg)
    b.update_market_prices({"RELIANCE": 1300.0})

    calls = []
    b.on_exit = lambda sym, pnl, reason, strat: calls.append((sym, pnl, reason, strat))

    # Open a short: stop = 1320 (above entry), target = 1250 (below entry)
    b.place_order("RELIANCE", Side.SELL, 10, OrderType.MARKET, stop_loss=1320, target=1250, strategy="ss")
    b.update_market_prices({"RELIANCE": 1321.0})  # price rises above stop → stop loss

    assert len(calls) == 1
    sym, pnl, reason, strat = calls[0]
    assert sym == "RELIANCE"
    assert reason == "stop_loss"
    assert strat == "ss"


def test_on_exit_callback_not_set_does_not_raise(broker):
    """With on_exit=None (default), stop-loss exit must not raise."""
    assert broker.on_exit is None
    broker.place_order("RELIANCE", Side.BUY, 10, OrderType.MARKET, 1280, 1360, strategy="t")
    broker.update_market_prices({"RELIANCE": 1279.0})  # trigger stop loss
    # No exception; position is gone
    assert broker.get_position("RELIANCE") is None
