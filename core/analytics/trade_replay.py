"""Trade replay engine — reconstruct a trade's full lifecycle from SQLite + events log.

Combines:
- Trade record from SQLite (entry/exit prices, times, P&L, charges)
- Signal record (strategy, regime, rationale, confidence, stop_loss, target)
- Events log (what happened during the trade lifecycle: bars, stop hits, etc.)
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class TradeReplayFrame:
    timestamp: str
    price: float
    action: str             # "entry" | "stop_loss" | "target" | "eod_squareoff" | "bar"
    pnl_running: float
    regime: str
    signal_confidence: float
    guardrail_notes: list   # list of guardrail checks that ran


@dataclass
class TradeReplay:
    trade_id: int
    symbol: str
    strategy: str
    entry_price: float
    exit_price: float
    stop_loss: float
    target: float
    pnl: float
    charges: float
    entry_time: str
    exit_time: str
    exit_reason: str
    regime_at_entry: str
    frames: list            # list of TradeReplayFrame (candles between entry and exit)
    signal_rationale: str
    rejection_checks: list  # guardrail checks that PASSED (allowed the trade)


def load_trade_replay(
    trade_id: int,
    db_path: str,
    events_path: str = "logs/events.jsonl",
) -> Optional[TradeReplay]:
    """Load a trade and reconstruct its full replay from SQLite + events log.

    Combines:
    - Trade record from SQLite (entry/exit prices, times, P&L)
    - Signal record (strategy, regime, rationale, confidence)
    - Events log (what happened during the trade lifecycle)

    Returns None if trade not found.
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # Get trade
        trade = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        if not trade:
            conn.close()
            return None

        # Get the signal that generated this trade (match by symbol + approximate time)
        signal = conn.execute(
            "SELECT * FROM signals WHERE symbol=? AND ts <= ? AND accepted=1 ORDER BY ts DESC LIMIT 1",
            (trade["symbol"], trade["opened_at"]),
        ).fetchone()

        conn.close()

        entry_price = float(trade["entry_price"])

        # Parse events log for this trade period
        frames: list[TradeReplayFrame] = []
        events_file = Path(events_path)
        if events_file.exists():
            try:
                with open(events_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                            if ev.get("symbol") == trade["symbol"]:
                                price = float(ev.get("price", 0.0))
                                qty = float(trade["qty"]) if "qty" in trade.keys() else 1.0
                                pnl_running = (price - entry_price) * qty
                                frames.append(
                                    TradeReplayFrame(
                                        timestamp=ev.get("ts", ""),
                                        price=price,
                                        action=ev.get("type", "bar"),
                                        pnl_running=round(pnl_running, 2),
                                        regime=ev.get("regime", ""),
                                        signal_confidence=float(ev.get("confidence", 0.0)),
                                        guardrail_notes=ev.get("guardrail_notes", []),
                                    )
                                )
                        except (json.JSONDecodeError, ValueError, TypeError):
                            pass
            except OSError:
                pass

        # Build stop_loss / target from signal if available
        stop_loss = 0.0
        target = 0.0
        regime_at_entry = ""
        signal_rationale = ""
        if signal:
            stop_loss = float(signal["stop_loss"]) if signal["stop_loss"] is not None else 0.0
            target = float(signal["target"]) if signal["target"] is not None else 0.0
            regime_at_entry = signal["regime"] or ""
            signal_rationale = signal["rationale"] or ""

        # trade keys helper — sqlite3.Row supports .keys() but not .get()
        def _tget(key: str, default="") -> str:
            try:
                v = trade[key]
                return v if v is not None else default
            except (IndexError, KeyError):
                return default

        return TradeReplay(
            trade_id=trade_id,
            symbol=trade["symbol"],
            strategy=_tget("strategy"),
            entry_price=entry_price,
            exit_price=float(trade["exit_price"]),
            stop_loss=stop_loss,
            target=target,
            pnl=float(trade["pnl"]),
            charges=float(trade["charges"]),
            entry_time=trade["opened_at"],
            exit_time=trade["closed_at"],
            exit_reason=_tget("exit_reason"),
            regime_at_entry=regime_at_entry,
            frames=frames,
            signal_rationale=signal_rationale,
            rejection_checks=[],
        )
    except Exception:
        return None


def list_recent_trades(db_path: str, limit: int = 50) -> list:
    """Return list of recent trades with basic info for replay selection."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        trades = conn.execute(
            "SELECT id, symbol, strategy, entry_price, exit_price, pnl, opened_at, exit_reason "
            "FROM trades ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(t) for t in trades]
    except Exception:
        return []
