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


# ── MIS intraday leverage (Angel One ~4x buying power) ──────────────────────

def _leveraged_cfg(leverage: float = 4.0):
    from core.config import ExecutionCfg
    return ExecutionCfg(
        slippage_bps=5,
        brokerage_per_order_inr=20,
        stt_pct=0.025,
        exchange_txn_pct=0.0030699,
        gst_pct=18.0,
        signal_cooldown_minutes=30,
        intraday_leverage=leverage,
        stamp_duty_pct=0.003,
    )


def test_buying_power_without_leverage_equals_cash(execution_cfg):
    b = PaperBroker(starting_cash=100_000.0, exec_cfg=execution_cfg)
    assert b.buying_power() == pytest.approx(100_000.0)


def test_buying_power_with_4x_leverage():
    b = PaperBroker(starting_cash=100_000.0, exec_cfg=_leveraged_cfg(4.0))
    assert b.buying_power() == pytest.approx(400_000.0)


def test_leverage_allows_position_beyond_cash():
    """1.5x-cash notional order fills under 4x MIS margin."""
    b = PaperBroker(starting_cash=100_000.0, exec_cfg=_leveraged_cfg(4.0))
    b.update_market_prices({"TCS": 1000.0})
    order = b.place_order("TCS", Side.BUY, 150, OrderType.MARKET, 950, 1100)
    assert order.status == OrderStatus.FILLED
    assert b.cash() < 0  # broker funds the difference intraday


def test_leverage_caps_total_exposure_at_4x():
    b = PaperBroker(starting_cash=100_000.0, exec_cfg=_leveraged_cfg(4.0))
    b.update_market_prices({"TCS": 1000.0})
    b.place_order("TCS", Side.BUY, 150, OrderType.MARKET, 950, 1100)
    # Another 300k notional would push total exposure to ~4.5x equity
    order2 = b.place_order("TCS", Side.BUY, 300, OrderType.MARKET, 950, 1100)
    assert order2.status == OrderStatus.REJECTED
    assert order2.rejection_reason == "insufficient_cash"


def test_no_leverage_still_rejects_beyond_cash():
    b = PaperBroker(starting_cash=100_000.0, exec_cfg=_leveraged_cfg(1.0))
    b.update_market_prices({"TCS": 1000.0})
    order = b.place_order("TCS", Side.BUY, 150, OrderType.MARKET, 950, 1100)
    assert order.status == OrderStatus.REJECTED


def test_short_with_leverage():
    b = PaperBroker(starting_cash=100_000.0, exec_cfg=_leveraged_cfg(4.0))
    b.update_market_prices({"TCS": 1000.0})
    order = b.place_order("TCS", Side.SELL, 200, OrderType.MARKET, 1050, 900)
    assert order.status == OrderStatus.FILLED


# ── Angel One charge model ───────────────────────────────────────────────────

def test_charges_match_angel_one_rates():
    """Round-trip 100 shares @ Rs 1000: verify each component vs published rates."""
    from core.broker.charges import compute_charges
    buy = compute_charges(
        side=Side.BUY, qty=100, price=1000.0, brokerage_per_order_inr=20,
        stt_pct=0.025, exchange_txn_pct=0.0030699, gst_pct=18.0, stamp_duty_pct=0.003,
    )
    sell = compute_charges(
        side=Side.SELL, qty=100, price=1000.0, brokerage_per_order_inr=20,
        stt_pct=0.025, exchange_txn_pct=0.0030699, gst_pct=18.0, stamp_duty_pct=0.003,
    )
    assert buy.brokerage == pytest.approx(20.0)        # min(20, 100) = 20
    assert buy.stt == 0.0                               # STT buy side: zero intraday
    assert buy.stamp_duty == pytest.approx(3.0)         # 0.003% of 1L
    assert buy.exchange_txn == pytest.approx(3.0699)
    assert buy.clearing == 0.0                          # Angel One: zero
    assert sell.stt == pytest.approx(25.0)              # 0.025% of 1L
    assert sell.stamp_duty == 0.0
    # GST = 18% on (brokerage + txn + sebi)
    assert sell.gst == pytest.approx((20.0 + 3.0699 + 0.1) * 0.18, rel=1e-3)


def test_brokerage_pct_cap_small_order():
    """Small order: brokerage = max(0.1% of turnover, Rs 5 minimum)."""
    from core.broker.charges import compute_charges
    # 2 shares @ 100 = Rs 200 turnover → 0.1% = 0.2 → floored to Rs 5
    c = compute_charges(
        side=Side.BUY, qty=2, price=100.0, brokerage_per_order_inr=20,
        stt_pct=0.025, exchange_txn_pct=0.0030699, gst_pct=18.0,
    )
    assert c.brokerage == pytest.approx(5.0)
    # 100 shares @ 100 = 10k turnover → 0.1% = Rs 10 < Rs 20 flat
    c2 = compute_charges(
        side=Side.BUY, qty=100, price=100.0, brokerage_per_order_inr=20,
        stt_pct=0.025, exchange_txn_pct=0.0030699, gst_pct=18.0,
    )
    assert c2.brokerage == pytest.approx(10.0)
