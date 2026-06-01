"""Tests for MCP mutating tools (halt/resume/order/risk via command queue).

These tests mock `core.command_queue.enqueue` so no disk I/O occurs and
the queue file is never touched.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mcp_server.tools import TradingAgentTools


@pytest.fixture
def tools(tmp_path: Path) -> TradingAgentTools:
    (tmp_path / "config").mkdir()
    cfg_src = Path("config/default.yaml").read_text(encoding="utf-8")
    (tmp_path / "config" / "default.yaml").write_text(cfg_src, encoding="utf-8")
    return TradingAgentTools(
        snapshot_path=tmp_path / "snapshot.json",
        sqlite_path=tmp_path / "agent.db",
        config_path=tmp_path / "config" / "default.yaml",
    )


def _mock_cmd(cmd_id: str = "test-uuid-1234") -> MagicMock:
    """Return a mock Command object with the given id."""
    cmd = MagicMock()
    cmd.id = cmd_id
    return cmd


# ---------- halt_agent ----------

def test_halt_agent_queues_command(tools):
    mock_cmd = _mock_cmd("halt-1")
    with patch("core.command_queue.enqueue", return_value=mock_cmd) as mock_enqueue:
        result = tools.halt_agent(reason="test halt")
    mock_enqueue.assert_called_once_with("halt_agent", {"reason": "test halt"})
    assert result["queued"] is True
    assert result["command_id"] == "halt-1"
    assert "halt-1" in result["message"]


def test_halt_agent_default_reason(tools):
    mock_cmd = _mock_cmd("halt-2")
    with patch("core.command_queue.enqueue", return_value=mock_cmd) as mock_enqueue:
        result = tools.halt_agent()
    _, kwargs_or_args = mock_enqueue.call_args
    # positional: enqueue("halt_agent", {"reason": ...})
    call_args = mock_enqueue.call_args[0]
    assert call_args[0] == "halt_agent"
    assert "reason" in call_args[1]
    assert result["queued"] is True


# ---------- resume_agent ----------

def test_resume_agent_queues_command(tools):
    mock_cmd = _mock_cmd("resume-1")
    with patch("core.command_queue.enqueue", return_value=mock_cmd) as mock_enqueue:
        result = tools.resume_agent()
    mock_enqueue.assert_called_once_with("resume_agent", {})
    assert result["queued"] is True
    assert result["command_id"] == "resume-1"


# ---------- update_risk_param ----------

def test_update_risk_param_valid(tools):
    mock_cmd = _mock_cmd("risk-1")
    with patch("core.command_queue.enqueue", return_value=mock_cmd) as mock_enqueue:
        result = tools.update_risk_param("per_trade_risk_pct", 1.5)
    mock_enqueue.assert_called_once_with(
        "update_risk_param", {"param": "per_trade_risk_pct", "value": 1.5}
    )
    assert result["queued"] is True
    assert result["param"] == "per_trade_risk_pct"
    assert result["value"] == 1.5


def test_update_risk_param_unknown(tools):
    result = tools.update_risk_param("secret_lever", 99.0)
    assert "error" in result
    assert "Unknown param" in result["error"]


def test_update_risk_param_out_of_bounds(tools):
    # per_trade_risk_pct must be in [0.5, 2.0]
    result = tools.update_risk_param("per_trade_risk_pct", 5.0)
    assert "error" in result
    assert "must be in" in result["error"]


def test_update_risk_param_at_lower_bound(tools):
    mock_cmd = _mock_cmd("risk-2")
    with patch("core.command_queue.enqueue", return_value=mock_cmd):
        result = tools.update_risk_param("per_trade_risk_pct", 0.5)
    assert result["queued"] is True


def test_update_risk_param_at_upper_bound(tools):
    mock_cmd = _mock_cmd("risk-3")
    with patch("core.command_queue.enqueue", return_value=mock_cmd):
        result = tools.update_risk_param("daily_loss_circuit_pct", 5.0)
    assert result["queued"] is True


def test_update_risk_param_max_open_positions(tools):
    mock_cmd = _mock_cmd("risk-4")
    with patch("core.command_queue.enqueue", return_value=mock_cmd):
        result = tools.update_risk_param("max_open_positions", 5)
    assert result["queued"] is True
    assert result["value"] == 5


# ---------- place_paper_order ----------

def test_place_paper_order_valid(tools):
    mock_cmd = _mock_cmd("order-1")
    with patch("core.command_queue.enqueue", return_value=mock_cmd) as mock_enqueue:
        result = tools.place_paper_order("RELIANCE", "BUY", 10)
    mock_enqueue.assert_called_once_with(
        "place_paper_order",
        {"symbol": "RELIANCE", "side": "BUY", "qty": 10, "strategy": "manual"},
    )
    assert result["queued"] is True
    assert result["symbol"] == "RELIANCE"
    assert result["side"] == "BUY"
    assert result["qty"] == 10


def test_place_paper_order_sell(tools):
    mock_cmd = _mock_cmd("order-2")
    with patch("core.command_queue.enqueue", return_value=mock_cmd):
        result = tools.place_paper_order("TCS", "SELL", 5, strategy="manual_exit")
    assert result["queued"] is True
    assert result["side"] == "SELL"


def test_place_paper_order_invalid_side(tools):
    result = tools.place_paper_order("RELIANCE", "SHORT", 10)
    assert "error" in result
    assert "side must be BUY or SELL" in result["error"]


def test_place_paper_order_qty_zero(tools):
    result = tools.place_paper_order("RELIANCE", "BUY", 0)
    assert "error" in result
    assert "qty" in result["error"]


def test_place_paper_order_qty_too_large(tools):
    result = tools.place_paper_order("RELIANCE", "BUY", 1001)
    assert "error" in result
    assert "qty" in result["error"]


def test_place_paper_order_symbol_not_in_universe(tools):
    result = tools.place_paper_order("XYZABC", "BUY", 10)
    assert "error" in result
    assert "not in Nifty 50 universe" in result["error"]


def test_place_paper_order_custom_strategy(tools):
    mock_cmd = _mock_cmd("order-3")
    with patch("core.command_queue.enqueue", return_value=mock_cmd) as mock_enqueue:
        result = tools.place_paper_order("INFY", "BUY", 20, strategy="eod_review")
    call_params = mock_enqueue.call_args[0][1]
    assert call_params["strategy"] == "eod_review"
    assert result["queued"] is True
