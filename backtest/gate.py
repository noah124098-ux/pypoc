"""The strict backtest gate.

Live trading mode refuses to start unless `data/backtest_gate.json` exists,
is recent, and reports `passed: true`. This is the safety guarantee that
no untested strategy makes it into production.

A gate result is **valid** when:
  - It was produced from a walk-forward run covering >= configured years
  - The aggregate metrics meet ALL of the configured thresholds
  - The gate file was written within the last 30 days (re-validation
    on a rolling basis catches strategy drift)
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from backtest.metrics import BacktestMetrics
from core.config import BacktestGateCfg

GATE_FILE_DEFAULT = "data/backtest_gate.json"
GATE_MAX_AGE_DAYS = 30


@dataclass
class GateThresholdCheck:
    name: str
    actual: float
    threshold: float
    pass_: bool
    direction: str  # "ge" or "le"


@dataclass
class GateResult:
    passed: bool
    timestamp: str
    period_start: Optional[str]
    period_end: Optional[str]
    metrics: dict
    checks: list[GateThresholdCheck] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    config: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        # Coerce dataclass list-of-dataclass to plain dicts (already done by asdict)
        return d


def evaluate_gate(
    metrics: BacktestMetrics,
    cfg: BacktestGateCfg,
    *,
    period_start: Optional[datetime] = None,
    period_end: Optional[datetime] = None,
    walk_forward_years: Optional[float] = None,
) -> GateResult:
    """Compare aggregate metrics against thresholds. Returns pass/fail with breakdown."""
    checks = [
        _ge("sharpe", metrics.sharpe, cfg.min_sharpe),
        _le("max_drawdown_pct", metrics.max_drawdown_pct, cfg.max_drawdown_pct),
        _ge("win_rate_pct", metrics.win_rate_pct, cfg.min_win_rate),
        _ge("profit_factor", metrics.profit_factor, cfg.min_profit_factor),
        _ge("n_trades", metrics.n_trades, cfg.min_trades),
    ]

    failures = [c.name for c in checks if not c.pass_]

    # Walk-forward duration check is separate and additive.
    if walk_forward_years is not None and walk_forward_years < cfg.walk_forward_years:
        failures.append(
            f"walk_forward_years_too_short ({walk_forward_years:.1f} < {cfg.walk_forward_years})"
        )

    return GateResult(
        passed=len(failures) == 0,
        timestamp=datetime.now(timezone.utc).isoformat(),
        period_start=period_start.isoformat() if period_start else None,
        period_end=period_end.isoformat() if period_end else None,
        metrics=metrics.to_dict(),
        checks=checks,
        failures=failures,
        config={
            "min_sharpe": cfg.min_sharpe,
            "max_drawdown_pct": cfg.max_drawdown_pct,
            "min_win_rate": cfg.min_win_rate,
            "min_profit_factor": cfg.min_profit_factor,
            "min_trades": cfg.min_trades,
            "walk_forward_years": cfg.walk_forward_years,
        },
    )


def write_gate_result(result: GateResult, path: str | Path = GATE_FILE_DEFAULT) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(result.to_dict(), indent=2, default=str), encoding="utf-8")


def read_gate_result(path: str | Path = GATE_FILE_DEFAULT) -> Optional[dict]:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def is_live_allowed(path: str | Path = GATE_FILE_DEFAULT) -> tuple[bool, str]:
    """Returns (allowed, reason). Used by `cli.py run` when mode=live."""
    data = read_gate_result(path)
    if data is None:
        return False, f"no gate result at {path} — run `python cli.py walk-forward` first"
    if not data.get("passed", False):
        failures = data.get("failures", [])
        return False, f"gate not passed: failures={failures}"
    ts_str = data.get("timestamp")
    if ts_str:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - ts
            if age > timedelta(days=GATE_MAX_AGE_DAYS):
                return False, f"gate result too old ({age.days} days > {GATE_MAX_AGE_DAYS}) — re-run walk-forward"
        except ValueError:
            return False, "gate timestamp unparseable — re-run walk-forward"
    return True, "ok"


def _ge(name: str, actual: float, threshold: float) -> GateThresholdCheck:
    return GateThresholdCheck(
        name=name, actual=float(actual), threshold=float(threshold),
        pass_=actual >= threshold, direction="ge",
    )


def _le(name: str, actual: float, threshold: float) -> GateThresholdCheck:
    return GateThresholdCheck(
        name=name, actual=float(actual), threshold=float(threshold),
        pass_=actual <= threshold, direction="le",
    )
