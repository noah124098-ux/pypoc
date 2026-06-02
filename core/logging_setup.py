"""Structured logging — text logs to console/file, JSONL events for downstream tooling.

Features:
- RotatingFileHandler: 10 MB per file, 5 rotated backups (configurable).
- Structured trade/order/halt helpers that emit a canonical log line.
- Quiet-hours mode: outside NSE market hours (09:15–15:35 IST) the file handler
  drops to WARNING to avoid filling disk with idle-loop noise.  Console handler
  is unaffected so interactive sessions remain verbose.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, time, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

# NSE market-hours window (IST) used for quiet-hours detection.
_MARKET_OPEN = time(9, 15)
_MARKET_CLOSE = time(15, 35)  # 5 min after EOD square-off at 15:30


def _is_market_hours() -> bool:
    """Return True if current IST wall-clock time is within NSE market hours."""
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo("Asia/Kolkata")
    except Exception:
        return True  # fail-open: don't suppress logs on tz import failure
    now_ist = datetime.now(tz).time()
    return _MARKET_OPEN <= now_ist <= _MARKET_CLOSE


class _QuietHoursFilter(logging.Filter):
    """Drop records below WARNING when outside market hours."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if record.levelno >= logging.WARNING:
            return True
        return _is_market_hours()


class JsonlEventLogger:
    """Append-only JSONL log of structured events (orders, signals, regime changes, etc.)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event_type: str, **fields: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def emit_daily_summary(
        self,
        *,
        equity: float,
        day_pnl: float,
        day_pnl_pct: float,
        trades_today: int,
        regime_distribution: dict[str, int],
    ) -> None:
        """Emit a 'daily_summary' event at EOD (call after 15:35 square-off)."""
        self.emit(
            "daily_summary",
            equity=round(equity, 2),
            day_pnl=round(day_pnl, 2),
            day_pnl_pct=round(day_pnl_pct, 4),
            trades_today=trades_today,
            regime_distribution=regime_distribution,
        )


# ---------------------------------------------------------------------------
# Structured trade-event helpers
# ---------------------------------------------------------------------------

def log_trade_filled(
    logger: logging.Logger,
    *,
    symbol: str,
    side: str,
    qty: int,
    price: float,
    strategy: str,
    regime: str,
) -> None:
    """Log a canonical TRADE line at INFO level.

    Example output:
        TRADE symbol=RELIANCE side=BUY qty=50 price=2450.00 strategy=trend_breakout regime=TREND
    """
    logger.info(
        "TRADE symbol=%s side=%s qty=%d price=%.2f strategy=%s regime=%s",
        symbol, side, qty, price, strategy, regime,
    )


def log_order_rejected(
    logger: logging.Logger,
    *,
    symbol: str,
    reason: str,
) -> None:
    """Log a canonical ORDER_REJECTED line at INFO level.

    Example output:
        ORDER_REJECTED symbol=RELIANCE reason=insufficient_cash
    """
    logger.info("ORDER_REJECTED symbol=%s reason=%s", symbol, reason)


def log_agent_halted(
    logger: logging.Logger,
    *,
    reason: str,
    pct: float,
) -> None:
    """Log a canonical AGENT_HALTED line at ERROR level.

    Example output:
        AGENT_HALTED reason=daily_loss_circuit pct=-3.12
    """
    logger.error("AGENT_HALTED reason=%s pct=%.2f", reason, pct)


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(
    level: str = "INFO",
    file: str | None = "logs/agent.log",
    max_bytes: int = 10_485_760,   # 10 MB
    backup_count: int = 5,
    quiet_hours: bool = True,
) -> logging.Logger:
    """Configure and return the root 'agent' logger.

    Args:
        level: Logging level string (e.g. "INFO", "DEBUG").
        file: Path to the rotating log file; None disables file logging.
        max_bytes: Maximum size of a single log file before rotation (bytes).
        backup_count: Number of rotated backup files to keep.
        quiet_hours: If True, apply _QuietHoursFilter to file handler so that
                     INFO/DEBUG messages are suppressed outside market hours.
    """
    logger = logging.getLogger("agent")
    logger.setLevel(level)
    if logger.handlers:
        return logger
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # Console handler — always at the configured level, no quiet-hours filter.
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # Rotating file handler.
    if file is not None:
        Path(file).parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        if quiet_hours:
            fh.addFilter(_QuietHoursFilter())
        logger.addHandler(fh)

    return logger
