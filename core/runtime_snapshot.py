"""Runtime snapshot writer.

The orchestrator periodically calls write() to atomically dump a JSON snapshot of
the agent's live state to disk. The MCP server (a separate process) reads this
file. Decoupling via file means: agent process owns state; MCP server is purely
read-only and can be restarted independently.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class RuntimeSnapshot:
    ts: str
    pid: int
    mode: str                 # paper | live
    feed_connected: bool
    last_tick_age_seconds: float
    halted: bool
    halt_reason: str
    cash: float
    equity: float
    realized_pnl: float
    starting_equity_today: float
    peak_equity: float
    open_positions: list[dict[str, Any]]
    current_regime: str
    regime_rationale: str
    nifty_ltp: float
    vix: float
    universe_size: int
    strategies_enabled: list[str]
    config_path: str
    drawdown_warning: bool = False
    daily_loss_warning: bool = False
    extras: dict[str, Any] = field(default_factory=dict)


def write(snapshot: RuntimeSnapshot, path: str | Path) -> None:
    """Atomic write — temp file then rename. On Windows falls back to direct overwrite
    if os.replace fails because the API process has the target file open."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(asdict(snapshot), f, default=str, indent=2)
        try:
            os.replace(tmp_name, p)
        except OSError:
            # Windows: target locked by another process — copy then delete temp
            import shutil as _shutil
            try:
                _shutil.copy2(tmp_name, p)
            finally:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read(path: str | Path) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
