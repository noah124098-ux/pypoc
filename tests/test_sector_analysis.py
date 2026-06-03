"""Tests for sector rotation analysis: NIFTY50_SECTORS mapping and
compute_sector_performance() in core/analytics/metrics.py."""
from __future__ import annotations

import pytest

from core.analytics.metrics import NIFTY50_SECTORS, compute_sector_performance
from core.data.universe import NIFTY_50


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _trade(symbol: str, pnl: float) -> dict:
    return {"symbol": symbol, "pnl": pnl}


# ---------------------------------------------------------------------------
# 1. NIFTY50_SECTORS mapping completeness
# ---------------------------------------------------------------------------

def test_nifty50_sectors_covers_all_universe_symbols():
    """Every symbol in NIFTY_50 must appear in NIFTY50_SECTORS."""
    missing = [s for s in NIFTY_50 if s not in NIFTY50_SECTORS]
    assert missing == [], (
        f"{len(missing)} Nifty 50 symbols missing from NIFTY50_SECTORS: {missing}"
    )


def test_nifty50_sectors_no_empty_sector_names():
    """All sector values must be non-empty strings."""
    bad = [sym for sym, sec in NIFTY50_SECTORS.items() if not sec or not sec.strip()]
    assert bad == [], f"Symbols with empty sector: {bad}"


# ---------------------------------------------------------------------------
# 2. compute_sector_performance — basic correctness
# ---------------------------------------------------------------------------

def test_sector_performance_basic_grouping():
    """Trades for IT and Banking symbols are grouped correctly."""
    trades = [
        _trade("TCS", 500.0),
        _trade("INFY", -100.0),
        _trade("HDFCBANK", 200.0),
        _trade("SBIN", 300.0),
    ]
    result = compute_sector_performance(trades)

    assert "IT" in result
    assert "Banking" in result

    it = result["IT"]
    assert it["n_trades"] == 2
    assert it["pnl"] == pytest.approx(400.0)
    assert it["win_rate"] == pytest.approx(50.0)  # 1 win out of 2
    assert it["best_symbol"] == "TCS"
    assert it["worst_symbol"] == "INFY"

    banking = result["Banking"]
    assert banking["n_trades"] == 2
    assert banking["pnl"] == pytest.approx(500.0)
    assert banking["win_rate"] == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# 3. compute_sector_performance — edge cases
# ---------------------------------------------------------------------------

def test_sector_performance_empty_trades():
    """Empty trade list returns empty dict without error."""
    result = compute_sector_performance([])
    assert result == {}


def test_sector_performance_unknown_symbol_grouped_as_other():
    """Symbols not in the mapping fall into the 'Other' bucket."""
    trades = [_trade("UNKNOWN_XYZ", 100.0)]
    result = compute_sector_performance(trades)
    assert "Other" in result
    assert result["Other"]["n_trades"] == 1
    assert result["Other"]["pnl"] == pytest.approx(100.0)


def test_sector_performance_custom_mapping():
    """A caller-supplied mapping overrides the default NIFTY50_SECTORS."""
    custom = {"AAPL": "Tech", "MSFT": "Tech", "JPM": "Finance"}
    trades = [
        _trade("AAPL", 1000.0),
        _trade("MSFT", -200.0),
        _trade("JPM", 500.0),
    ]
    result = compute_sector_performance(trades, symbol_to_sector=custom)
    assert "Tech" in result
    assert "Finance" in result
    assert result["Tech"]["n_trades"] == 2
    assert result["Tech"]["pnl"] == pytest.approx(800.0)
    assert result["Tech"]["win_rate"] == pytest.approx(50.0)
    assert result["Finance"]["pnl"] == pytest.approx(500.0)
    assert result["Finance"]["win_rate"] == pytest.approx(100.0)
