"""Pure data-access functions used by the MCP server.

Kept separate from the MCP protocol layer so they can be unit-tested without an MCP runtime.
The MCP server in `mcp_server.server` is a thin wrapper that registers these as tools.

All tools are READ-ONLY. They read the runtime snapshot file (written by the orchestrator)
and the SQLite store. They never mutate state — that keeps the MCP surface safe to expose
to any client.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from core.config import load_settings
from core.runtime_snapshot import read as read_snapshot


class TradingAgentTools:
    """Implements the read-only operations the MCP server exposes.

    The orchestrator runs in another process; this class only talks to disk
    (snapshot.json + SQLite). Safe to instantiate from anywhere.
    """

    def __init__(
        self,
        snapshot_path: str | Path = "data/snapshot.json",
        sqlite_path: str | Path = "data/agent.db",
        config_path: str | Path = "config/default.yaml",
    ):
        self.snapshot_path = Path(snapshot_path)
        self.sqlite_path = Path(sqlite_path)
        self.config_path = Path(config_path)

    # ---------- helpers ----------

    def _connect(self) -> sqlite3.Connection:
        if not self.sqlite_path.exists():
            raise FileNotFoundError(
                f"SQLite DB not found at {self.sqlite_path}. "
                "Has the agent run at least once?"
            )
        c = sqlite3.connect(self.sqlite_path)
        c.row_factory = sqlite3.Row
        return c

    @staticmethod
    def _rows(cursor) -> list[dict[str, Any]]:
        return [dict(r) for r in cursor.fetchall()]

    # ---------- tools ----------

    def get_status(self) -> dict[str, Any]:
        """Live agent state: feed connectivity, equity, halt status, current regime."""
        snap = read_snapshot(self.snapshot_path)
        if snap is None:
            return {
                "running": False,
                "message": (
                    f"No snapshot at {self.snapshot_path}. "
                    "Start the agent with `python cli.py run` to generate one."
                ),
            }
        return {"running": True, **snap}

    def get_positions(self) -> list[dict[str, Any]]:
        """Current open positions with stop-loss / target / unrealized P&L."""
        snap = read_snapshot(self.snapshot_path)
        if snap is None:
            return []
        return snap.get("open_positions", [])

    def get_pnl(self) -> dict[str, Any]:
        """Realized + unrealized P&L summary, day P&L, drawdown from peak."""
        snap = read_snapshot(self.snapshot_path)
        if snap is None:
            return {"available": False, "reason": "agent not running"}
        equity = float(snap.get("equity", 0.0))
        peak = float(snap.get("peak_equity", equity)) or equity
        start_today = float(snap.get("starting_equity_today", equity)) or equity
        unrealized = sum(p.get("unrealized_pnl", 0.0) for p in snap.get("open_positions", []))
        return {
            "available": True,
            "cash": float(snap.get("cash", 0.0)),
            "equity": equity,
            "realized_pnl_total": float(snap.get("realized_pnl", 0.0)),
            "unrealized_pnl": unrealized,
            "day_pnl_pct": ((equity - start_today) / start_today * 100.0) if start_today else 0.0,
            "drawdown_from_peak_pct": ((peak - equity) / peak * 100.0) if peak else 0.0,
        }

    def get_recent_signals(self, limit: int = 50, accepted_only: bool = False) -> list[dict[str, Any]]:
        """Most recent strategy signals, including rejection reasons."""
        with self._connect() as c:
            q = "SELECT * FROM signals"
            params: tuple = ()
            if accepted_only:
                q += " WHERE accepted = 1"
            q += " ORDER BY id DESC LIMIT ?"
            params = (*params, int(limit))
            cur = c.execute(q, params)
            return self._rows(cur)

    def get_recent_trades(self, limit: int = 50) -> list[dict[str, Any]]:
        """Most recent closed trades."""
        with self._connect() as c:
            cur = c.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (int(limit),)
            )
            return self._rows(cur)

    def get_guardrail_rejections(self, limit: int = 50) -> list[dict[str, Any]]:
        """Recent guardrail-rejected events — useful to see which rules are firing."""
        with self._connect() as c:
            cur = c.execute(
                "SELECT * FROM guardrail_events ORDER BY id DESC LIMIT ?", (int(limit),)
            )
            return self._rows(cur)

    def get_equity_curve(self, limit: int = 500) -> list[dict[str, Any]]:
        """Time series of equity snapshots."""
        with self._connect() as c:
            cur = c.execute(
                "SELECT * FROM equity_snapshots ORDER BY ts DESC LIMIT ?", (int(limit),)
            )
            return self._rows(cur)

    def get_regime_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """History of regime classifications (TREND / RANGE / VOLATILE / UNKNOWN)."""
        with self._connect() as c:
            cur = c.execute(
                "SELECT * FROM regime_log ORDER BY id DESC LIMIT ?", (int(limit),)
            )
            return self._rows(cur)

    def get_universe(self) -> dict[str, Any]:
        """Configured trading universe (Nifty 50 by default)."""
        from core.data.universe import resolve_universe

        s = load_settings(self.config_path)
        symbols = resolve_universe(s.universe.source, s.universe.symbols)
        return {"source": s.universe.source, "count": len(symbols), "symbols": symbols}

    def get_config_summary(self) -> dict[str, Any]:
        """Configured risk + strategy parameters. Useful for verifying live config."""
        s = load_settings(self.config_path)
        return {
            "mode": s.mode,
            "capital_inr": s.capital.initial_inr,
            "risk": {
                "per_trade_risk_pct": s.risk.per_trade_risk_pct,
                "max_position_pct": s.risk.max_position_pct,
                "max_open_positions": s.risk.max_open_positions,
                "daily_loss_circuit_pct": s.risk.daily_loss_circuit_pct,
                "drawdown_circuit_pct": s.risk.drawdown_circuit_pct,
                "stale_tick_seconds": s.risk.stale_tick_seconds,
            },
            "regime": {
                "adx_trend_threshold": s.regime.adx_trend_threshold,
                "bb_width_range_threshold": s.regime.bb_width_range_threshold,
                "vix_volatile_threshold": s.regime.vix_volatile_threshold,
            },
            "strategies_enabled": [
                k for k, v in s.strategies.items() if v.get("enabled", False)
            ],
            "primary_feed": s.data.primary_feed,
        }

    # --- Mutating tools (via command queue) ---

    def halt_agent(self, reason: str = "manual halt via MCP") -> dict:
        """Enqueue a halt_agent command. The orchestrator will stop trading when it drains the queue."""
        from core.command_queue import enqueue
        cmd = enqueue("halt_agent", {"reason": reason})
        return {"queued": True, "command_id": cmd.id, "message": f"Halt command queued: {cmd.id}"}

    def resume_agent(self) -> dict:
        """Enqueue a resume_agent command to clear a manual halt."""
        from core.command_queue import enqueue
        cmd = enqueue("resume_agent", {})
        return {"queued": True, "command_id": cmd.id}

    def update_risk_param(self, param: str, value: float) -> dict:
        """Enqueue a risk-parameter update. Only pre-approved params within safe bounds are accepted."""
        SAFE_BOUNDS = {
            "per_trade_risk_pct": (0.5, 2.0),
            "max_open_positions": (2, 8),
            "daily_loss_circuit_pct": (2.0, 5.0),
        }
        if param not in SAFE_BOUNDS:
            return {"error": f"Unknown param {param}. Allowed: {list(SAFE_BOUNDS)}"}
        lo, hi = SAFE_BOUNDS[param]
        if not (lo <= value <= hi):
            return {"error": f"{param} must be in [{lo}, {hi}], got {value}"}
        from core.command_queue import enqueue
        cmd = enqueue("update_risk_param", {"param": param, "value": value})
        return {"queued": True, "command_id": cmd.id, "param": param, "value": value}

    def place_paper_order(self, symbol: str, side: str, qty: int, strategy: str = "manual") -> dict:
        """Enqueue a manual paper order. Goes through all guardrails in the orchestrator."""
        if side not in ("BUY", "SELL"):
            return {"error": "side must be BUY or SELL"}
        if qty <= 0 or qty > 1000:
            return {"error": "qty must be 1-1000"}
        from core.data.universe import resolve_universe
        universe = resolve_universe("nifty50", [])
        if symbol not in universe:
            return {"error": f"{symbol} not in Nifty 50 universe"}
        from core.command_queue import enqueue
        cmd = enqueue("place_paper_order", {"symbol": symbol, "side": side, "qty": qty, "strategy": strategy})
        return {"queued": True, "command_id": cmd.id, "symbol": symbol, "side": side, "qty": qty}
