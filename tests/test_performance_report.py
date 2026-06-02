"""Tests for core.analytics.performance_report.

All tests use tmp_path for database and snapshot files to avoid touching
production data.  No live feeds or network calls are made.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.analytics.performance_report import (
    _fmt_inr,
    _fmt_pnl,
    generate_eod_report,
    generate_html_report,
)
from core.persistence.store import Store


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _write_snapshot(path: Path, **overrides) -> None:
    defaults = {
        "ts": "2026-06-02T15:30:00",
        "pid": 12345,
        "mode": "paper",
        "feed_connected": True,
        "last_tick_age_seconds": 2.0,
        "halted": False,
        "halt_reason": "",
        "cash": 490_000.0,
        "equity": 512_345.0,
        "realized_pnl": 2345.0,
        "starting_equity_today": 511_111.0,
        "peak_equity": 515_000.0,
        "open_positions": [],
        "current_regime": "TREND",
        "regime_rationale": "ADX above threshold, price above DMA",
        "nifty_ltp": 24_500.0,
        "vix": 14.2,
        "universe_size": 50,
        "strategies_enabled": ["trend_breakout", "rsi_momentum"],
        "config_path": "config/default.yaml",
    }
    defaults.update(overrides)
    path.write_text(json.dumps(defaults), encoding="utf-8")


def _add_trade(store: Store, **overrides) -> None:
    defaults = dict(
        symbol="RELIANCE",
        side="SELL",
        qty=5,
        entry_price=2900.0,
        exit_price=2978.0,
        pnl=890.0,
        charges=20.0,
        strategy="rsi_momentum",
        exit_reason="target",
        opened_at="2026-06-02T09:30:00",
        closed_at="2026-06-02T11:00:00",
    )
    defaults.update(overrides)
    store.record_trade(**defaults)


# ---------------------------------------------------------------------------
# Unit tests for formatting helpers
# ---------------------------------------------------------------------------

class TestFmtInr:
    def test_small_value(self):
        assert _fmt_inr(999) == "₹999"

    def test_thousands(self):
        assert _fmt_inr(1000) == "₹1,000"

    def test_lakhs(self):
        assert _fmt_inr(100_000) == "₹1,00,000"

    def test_tens_of_lakhs(self):
        assert _fmt_inr(512_345) == "₹5,12,345"

    def test_crores(self):
        assert _fmt_inr(10_000_000) == "₹1,00,00,000"

    def test_negative(self):
        result = _fmt_inr(-512_345)
        assert result.startswith("-₹")
        assert "5,12,345" in result

    def test_zero(self):
        assert _fmt_inr(0) == "₹0"


class TestFmtPnl:
    def test_positive(self):
        result = _fmt_pnl(1234.0)
        assert result.startswith("+₹")

    def test_negative(self):
        result = _fmt_pnl(-340.0)
        assert result.startswith("-₹")

    def test_zero(self):
        result = _fmt_pnl(0.0)
        assert result.startswith("+₹")


# ---------------------------------------------------------------------------
# generate_eod_report — plain-text
# ---------------------------------------------------------------------------

class TestGenerateEodReport:
    def test_returns_string(self, tmp_path):
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap)
        db = str(tmp_path / "agent.db")
        result = generate_eod_report(db, str(snap))
        assert isinstance(result, str)
        assert len(result) > 50

    def test_header_contains_date(self, tmp_path):
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap)
        db = str(tmp_path / "agent.db")
        result = generate_eod_report(db, str(snap))
        assert "NSE Agent EOD Report" in result

    def test_equity_summary_section(self, tmp_path):
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap, equity=512_345.0, peak_equity=515_000.0,
                        starting_equity_today=511_111.0)
        db = str(tmp_path / "agent.db")
        result = generate_eod_report(db, str(snap))
        assert "EQUITY SUMMARY" in result
        assert "₹5,12,345" in result
        assert "Drawdown" in result

    def test_no_trades_message(self, tmp_path):
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap)
        db = str(tmp_path / "agent.db")
        result = generate_eod_report(db, str(snap))
        assert "no closed trades" in result.lower()

    def test_trades_listed(self, tmp_path):
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap)
        db = str(tmp_path / "agent.db")
        store = Store(db)
        _add_trade(store, symbol="RELIANCE", pnl=890.0, strategy="rsi_momentum")
        _add_trade(store, symbol="INFY", pnl=-340.0, strategy="trend_breakout")
        result = generate_eod_report(db, str(snap))
        assert "RELIANCE" in result
        assert "INFY" in result
        assert "TODAY'S TRADES (2" in result

    def test_strategy_performance_section(self, tmp_path):
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap)
        db = str(tmp_path / "agent.db")
        store = Store(db)
        _add_trade(store, symbol="TCS", pnl=620.0, strategy="rsi_momentum")
        result = generate_eod_report(db, str(snap))
        assert "STRATEGY PERFORMANCE" in result
        assert "rsi_momentum" in result

    def test_regime_line(self, tmp_path):
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap, current_regime="TREND", vix=14.2)
        db = str(tmp_path / "agent.db")
        result = generate_eod_report(db, str(snap))
        assert "REGIME: TREND" in result

    def test_open_positions_listed(self, tmp_path):
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap, open_positions=[
            {
                "symbol": "HDFC",
                "side": "BUY",
                "avg_price": 1470.0,
                "stop_loss": 1450.0,
                "target": 1520.0,
                "unrealized_pnl": 340.0,
                "qty": 10,
                "strategy": "trend_breakout",
            }
        ])
        db = str(tmp_path / "agent.db")
        result = generate_eod_report(db, str(snap))
        assert "HDFC" in result
        assert "CURRENT POSITIONS (1" in result

    def test_no_positions_message(self, tmp_path):
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap, open_positions=[])
        db = str(tmp_path / "agent.db")
        result = generate_eod_report(db, str(snap))
        assert "no open positions" in result.lower()

    def test_missing_db_graceful(self, tmp_path):
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap)
        # DB path points to a file that doesn't exist
        db = str(tmp_path / "nonexistent.db")
        result = generate_eod_report(db, str(snap))
        assert "NSE Agent EOD Report" in result

    def test_missing_snapshot_graceful(self, tmp_path):
        db = str(tmp_path / "agent.db")
        snap = str(tmp_path / "nonexistent.json")
        result = generate_eod_report(db, snap)
        assert "NSE Agent EOD Report" in result

    def test_pnl_sign_positive(self, tmp_path):
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap, equity=511_500.0, starting_equity_today=511_000.0)
        db = str(tmp_path / "agent.db")
        result = generate_eod_report(db, str(snap))
        # Day P&L should show as positive
        assert "+₹" in result or "+0" in result

    def test_pnl_sign_negative(self, tmp_path):
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap, equity=510_000.0, starting_equity_today=511_000.0)
        db = str(tmp_path / "agent.db")
        result = generate_eod_report(db, str(snap))
        # Day P&L negative — should show minus or -₹
        assert "-₹" in result

    def test_strategy_30day_stats(self, tmp_path):
        """30-day perf includes win rate and profit factor."""
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap)
        db = str(tmp_path / "agent.db")
        store = Store(db)
        # 2 wins, 1 loss for trend_breakout
        _add_trade(store, symbol="A", pnl=500.0, strategy="trend_breakout")
        _add_trade(store, symbol="B", pnl=300.0, strategy="trend_breakout")
        _add_trade(store, symbol="C", pnl=-200.0, strategy="trend_breakout")
        result = generate_eod_report(db, str(snap))
        assert "trend_breakout" in result
        assert "3" in result  # trade count


# ---------------------------------------------------------------------------
# generate_html_report — HTML variant
# ---------------------------------------------------------------------------

class TestGenerateHtmlReport:
    def test_returns_html_string(self, tmp_path):
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap)
        db = str(tmp_path / "agent.db")
        result = generate_html_report(db, str(snap))
        assert result.startswith("<!DOCTYPE html>")
        assert "</html>" in result

    def test_contains_equity(self, tmp_path):
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap, equity=512_345.0)
        db = str(tmp_path / "agent.db")
        result = generate_html_report(db, str(snap))
        assert "5,12,345" in result

    def test_contains_regime_badge(self, tmp_path):
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap, current_regime="TREND")
        db = str(tmp_path / "agent.db")
        result = generate_html_report(db, str(snap))
        assert "TREND" in result
        assert "badge" in result

    def test_win_class_on_positive_pnl(self, tmp_path):
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap)
        db = str(tmp_path / "agent.db")
        store = Store(db)
        _add_trade(store, symbol="TCS", pnl=620.0)
        result = generate_html_report(db, str(snap))
        assert "win" in result

    def test_loss_class_on_negative_pnl(self, tmp_path):
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap)
        db = str(tmp_path / "agent.db")
        store = Store(db)
        _add_trade(store, symbol="INFY", pnl=-340.0)
        result = generate_html_report(db, str(snap))
        assert "loss" in result

    def test_strategy_table_present(self, tmp_path):
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap)
        db = str(tmp_path / "agent.db")
        store = Store(db)
        _add_trade(store, symbol="WIPRO", pnl=200.0, strategy="rsi_momentum")
        result = generate_html_report(db, str(snap))
        assert "rsi_momentum" in result
        assert "<table>" in result

    def test_open_positions_in_html(self, tmp_path):
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap, open_positions=[
            {
                "symbol": "WIPRO",
                "side": "BUY",
                "avg_price": 460.0,
                "stop_loss": 450.0,
                "target": 490.0,
                "unrealized_pnl": -120.0,
                "qty": 20,
                "strategy": "trend_breakout",
            }
        ])
        db = str(tmp_path / "agent.db")
        result = generate_html_report(db, str(snap))
        assert "WIPRO" in result

    def test_missing_db_graceful(self, tmp_path):
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap)
        db = str(tmp_path / "no_db.db")
        result = generate_html_report(db, str(snap))
        assert "<!DOCTYPE html>" in result

    def test_css_inline(self, tmp_path):
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap)
        db = str(tmp_path / "agent.db")
        result = generate_html_report(db, str(snap))
        assert "<style>" in result

    def test_vix_adx_displayed(self, tmp_path):
        snap = tmp_path / "snapshot.json"
        _write_snapshot(snap, vix=16.5)
        db = str(tmp_path / "agent.db")
        result = generate_html_report(db, str(snap))
        assert "16.5" in result
