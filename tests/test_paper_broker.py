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
