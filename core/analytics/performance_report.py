"""EOD performance report generator — plain-text and HTML variants.

Queries SQLite for today's trades and last-30-day strategy stats, reads the
runtime snapshot for live equity/regime data, then renders a formatted report
suitable for Telegram (plain-text) or email (HTML).

Public API::

    from core.analytics.performance_report import generate_eod_report, generate_html_report

    text = generate_eod_report("data/agent.db", "data/snapshot.json")
    html = generate_html_report("data/agent.db", "data/snapshot.json")
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _fmt_inr(value: float) -> str:
    """Format a float as an Indian-locale currency string, e.g. ₹5,12,345."""
    negative = value < 0
    abs_val = abs(value)
    # Split at decimal
    int_part = int(abs_val)
    dec_part = round((abs_val - int_part) * 100)

    s = str(int_part)
    if len(s) <= 3:
        formatted = s
    else:
        # Indian grouping: last 3, then groups of 2
        last3 = s[-3:]
        rest = s[:-3]
        groups = []
        while len(rest) > 2:
            groups.append(rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.append(rest)
        groups.reverse()
        formatted = ",".join(groups) + "," + last3

    result = f"₹{formatted}"
    if dec_part:
        result += f".{dec_part:02d}"
    return f"-{result}" if negative else result


def _fmt_pnl(value: float) -> str:
    """Format P&L with a leading + or - sign."""
    sign = "+" if value >= 0 else ""
    return f"{sign}{_fmt_inr(value)}"


def _safe_div(num: float, denom: float, default: float = 0.0) -> float:
    return num / denom if denom else default


def _pct(num: float, denom: float) -> float:
    return round(_safe_div(num, denom) * 100, 2)


# ---------------------------------------------------------------------------
# data-loading helpers
# ---------------------------------------------------------------------------

def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _query_today_trades(db_path: str, today: str) -> list[dict[str, Any]]:
    """Return all trades closed on *today* (ISO date, e.g. '2026-06-02')."""
    if not Path(db_path).exists():
        return []
    try:
        conn = _connect(db_path)
        rows = conn.execute(
            "SELECT * FROM trades WHERE closed_at >= ? ORDER BY closed_at DESC",
            (today,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


def _query_strategy_perf(db_path: str, days: int = 30) -> list[dict[str, Any]]:
    """Compute per-strategy P&L stats from the last *days* calendar days."""
    if not Path(db_path).exists():
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    try:
        conn = _connect(db_path)
        rows = conn.execute(
            "SELECT strategy, pnl FROM trades WHERE closed_at >= ? ORDER BY closed_at",
            (cutoff,),
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return []

    groups: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        strat = row["strategy"] or "unknown"
        groups[strat].append(float(row["pnl"]))

    result = []
    for strat, pnls in sorted(groups.items()):
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        pf = _safe_div(gross_profit, gross_loss) if gross_loss else (
            float("inf") if gross_profit else 0.0
        )
        result.append({
            "strategy": strat,
            "trades": len(pnls),
            "win_rate": _pct(len(wins), len(pnls)),
            "profit_factor": round(pf, 2),
            "net_pnl": round(sum(pnls), 2),
        })
    return result


def _query_latest_regime(db_path: str) -> dict[str, Any]:
    """Return the most recent regime log row."""
    if not Path(db_path).exists():
        return {}
    try:
        conn = _connect(db_path)
        row = conn.execute(
            "SELECT * FROM regime_log ORDER BY ts DESC LIMIT 1",
        ).fetchone()
        conn.close()
        return dict(row) if row else {}
    except sqlite3.Error:
        return {}


def _read_snapshot(snapshot_path: str) -> dict[str, Any]:
    p = Path(snapshot_path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------------------
# trade rendering helpers
# ---------------------------------------------------------------------------

def _risk_multiple(trade: dict[str, Any]) -> float | None:
    """Compute realised R-multiple: pnl / risk_per_share / qty."""
    pnl = float(trade.get("pnl", 0.0))
    entry = float(trade.get("entry_price", 0.0))
    # We don't store stop_loss on the closed trade, so use a simplified
    # proxy: risk = |pnl_per_share|  — only meaningful for directional context
    # A better proxy: if we have charges, exclude them from the entry price delta
    qty = int(trade.get("qty") or 1)
    if qty <= 0 or entry <= 0:
        return None
    # pnl already net of charges
    r_raw = pnl / entry / qty * 100  # percentage return
    return round(r_raw, 1)


def _trade_line(trade: dict[str, Any]) -> str:
    pnl = float(trade.get("pnl", 0.0))
    icon = "+" if pnl >= 0 else "-"
    symbol = str(trade.get("symbol", "?")).ljust(12)
    strategy = str(trade.get("strategy") or "?")[:13].ljust(13)
    side = str(trade.get("side", "BUY")).upper()
    pnl_str = _fmt_pnl(pnl)
    r = _risk_multiple(trade)
    r_str = f"  ({r:+.1f}%)" if r is not None else ""
    return f"  {icon} {symbol} {strategy} {side}  {pnl_str}{r_str}"


# ---------------------------------------------------------------------------
# report assembly
# ---------------------------------------------------------------------------

def _build_data(db_path: str, snapshot_path: str) -> dict[str, Any]:
    """Gather all data needed for both report variants."""
    today = date.today().isoformat()
    snapshot = _read_snapshot(snapshot_path)
    today_trades = _query_today_trades(db_path, today)
    strat_perf = _query_strategy_perf(db_path, days=30)
    latest_regime = _query_latest_regime(db_path)

    # equity figures (prefer snapshot; fall back to DB-last row)
    equity = float(snapshot.get("equity") or 0.0)
    peak_equity = float(snapshot.get("peak_equity") or equity)
    starting_equity_today = float(snapshot.get("starting_equity_today") or equity)
    cash = float(snapshot.get("cash") or 0.0)

    day_pnl = equity - starting_equity_today
    day_pnl_pct = _pct(day_pnl, starting_equity_today) if starting_equity_today else 0.0
    drawdown_pct = (
        _pct(peak_equity - equity, peak_equity) if peak_equity > 0 else 0.0
    )

    # open positions from snapshot
    open_positions: list[dict[str, Any]] = snapshot.get("open_positions") or []

    # regime
    regime_name = snapshot.get("current_regime") or latest_regime.get("regime") or "UNKNOWN"
    regime_rationale = snapshot.get("regime_rationale") or ""
    vix = float(snapshot.get("vix") or latest_regime.get("vix") or 0.0)
    adx = float(latest_regime.get("adx") or 0.0)

    return {
        "date": today,
        "equity": equity,
        "cash": cash,
        "peak_equity": peak_equity,
        "day_pnl": day_pnl,
        "day_pnl_pct": day_pnl_pct,
        "drawdown_pct": drawdown_pct,
        "today_trades": today_trades,
        "strat_perf": strat_perf,
        "open_positions": open_positions,
        "regime_name": regime_name,
        "regime_rationale": regime_rationale,
        "vix": vix,
        "adx": adx,
    }


# ---------------------------------------------------------------------------
# Plain-text report
# ---------------------------------------------------------------------------

def generate_eod_report(db_path: str, snapshot_path: str) -> str:
    """Generate a plain-text EOD performance report for email/Telegram.

    Returns a formatted string with sections for equity summary, today's
    trades, 30-day strategy performance, open positions, and market regime.

    Parameters
    ----------
    db_path:
        Path to the SQLite database written by :class:`core.persistence.store.Store`.
    snapshot_path:
        Path to the JSON runtime snapshot written by the orchestrator.

    Returns
    -------
    Formatted plain-text string.
    """
    d = _build_data(db_path, snapshot_path)
    lines: list[str] = []

    # header
    lines.append(f"=== NSE Agent EOD Report — {d['date']} ===")
    lines.append("")

    # equity summary
    lines.append("EQUITY SUMMARY")
    lines.append(f"  Today's P&L:     {_fmt_pnl(d['day_pnl'])} ({d['day_pnl_pct']:+.2f}%)")
    lines.append(f"  Equity:          {_fmt_inr(d['equity'])}")
    lines.append(f"  Peak Equity:     {_fmt_inr(d['peak_equity'])}")
    lines.append(f"  Drawdown:        {d['drawdown_pct']:.2f}%")
    lines.append("")

    # today's trades
    trades = d["today_trades"]
    lines.append(f"TODAY'S TRADES ({len(trades)} trade{'s' if len(trades) != 1 else ''})")
    if trades:
        for t in trades:
            lines.append(_trade_line(t))
    else:
        lines.append("  (no closed trades today)")
    lines.append("")

    # strategy performance (30 days)
    strat_perf = d["strat_perf"]
    lines.append("STRATEGY PERFORMANCE (last 30 days)")
    if strat_perf:
        for sp in strat_perf:
            pf_str = f"{sp['profit_factor']:.1f}" if sp["profit_factor"] != float("inf") else "inf"
            lines.append(
                f"  {sp['strategy']:<20} {sp['trades']:3d} trades, "
                f"{sp['win_rate']:5.1f}% win, PF {pf_str}"
            )
    else:
        lines.append("  (no data for last 30 days)")
    lines.append("")

    # open positions
    open_pos = d["open_positions"]
    lines.append(f"CURRENT POSITIONS ({len(open_pos)} open)")
    if open_pos:
        for pos in open_pos:
            sym = str(pos.get("symbol", "?")).ljust(8)
            side = str(pos.get("side") or "BUY").upper()
            sl = pos.get("stop_loss")
            tgt = pos.get("target")
            unreal = float(pos.get("unrealized_pnl") or 0.0)
            sl_str = f"{sl:,.0f}" if sl is not None else "N/A"
            tgt_str = f"{tgt:,.0f}" if tgt is not None else "N/A"
            lines.append(
                f"  {sym} {side}  SL: {sl_str:>7}  Target: {tgt_str:>7}  "
                f"P&L: {_fmt_pnl(unreal)}"
            )
    else:
        lines.append("  (no open positions)")
    lines.append("")

    # regime
    regime_str = d["regime_name"]
    regime_details = []
    if d["adx"]:
        regime_details.append(f"ADX {d['adx']:.1f}")
    if d["vix"]:
        regime_details.append(f"VIX {d['vix']:.1f}")
    detail_suffix = f" ({', '.join(regime_details)})" if regime_details else ""
    lines.append(f"REGIME: {regime_str}{detail_suffix}")
    if d["regime_rationale"]:
        lines.append(f"  {d['regime_rationale']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


_CSS = """
body { font-family: 'Segoe UI', Arial, sans-serif; background: #0f0f0f; color: #e0e0e0;
       margin: 0; padding: 20px; }
.card { background: #1a1a2e; border-radius: 12px; padding: 20px; margin-bottom: 16px;
        border: 1px solid #2a2a4a; }
h1 { color: #7c83fd; font-size: 1.4em; margin: 0 0 8px 0; border-bottom: 1px solid #2a2a4a;
     padding-bottom: 8px; }
h2 { color: #a0a8ff; font-size: 1.1em; margin: 0 0 12px 0; }
table { width: 100%; border-collapse: collapse; }
th { text-align: left; color: #888; font-size: 0.85em; padding: 4px 8px;
     border-bottom: 1px solid #2a2a4a; }
td { padding: 6px 8px; font-size: 0.9em; border-bottom: 1px solid #1a1a2e; }
.win { color: #4ade80; }
.loss { color: #f87171; }
.neutral { color: #94a3b8; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px;
         font-size: 0.8em; font-weight: bold; }
.badge-trend { background: #1e3a5f; color: #60a5fa; }
.badge-range { background: #3a1e5f; color: #c084fc; }
.badge-volatile { background: #3a1e1e; color: #f87171; }
.badge-unknown { background: #2a2a2a; color: #888; }
.metric-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
.metric { text-align: center; background: #0f0f1e; border-radius: 8px; padding: 12px; }
.metric-value { font-size: 1.4em; font-weight: bold; }
.metric-label { font-size: 0.75em; color: #888; margin-top: 4px; }
"""


def generate_html_report(db_path: str, snapshot_path: str) -> str:
    """Generate an HTML EOD performance report for email.

    Returns a self-contained HTML string with inline CSS (dark theme).

    Parameters
    ----------
    db_path:
        Path to the SQLite database written by :class:`core.persistence.store.Store`.
    snapshot_path:
        Path to the JSON runtime snapshot written by the orchestrator.

    Returns
    -------
    HTML string.
    """
    d = _build_data(db_path, snapshot_path)

    day_pnl_cls = "win" if d["day_pnl"] >= 0 else "loss"
    regime_lower = d["regime_name"].lower()
    regime_badge_cls = f"badge-{regime_lower}" if regime_lower in ("trend", "range", "volatile") else "badge-unknown"

    parts: list[str] = []

    parts.append(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NSE Agent EOD Report — {d['date']}</title>
<style>{_CSS}</style>
</head>
<body>
""")

    # Title card
    parts.append(f"""<div class="card">
  <h1>NSE Agent EOD Report</h1>
  <p style="margin:0; color:#888; font-size:0.9em;">{d['date']} &bull;
  Regime: <span class="badge {regime_badge_cls}">{_html_escape(d['regime_name'])}</span>
  &nbsp; VIX: {d['vix']:.1f} &nbsp; ADX: {d['adx']:.1f}</p>
</div>
""")

    # Equity metrics grid
    dd_cls = "loss" if d["drawdown_pct"] > 2 else "neutral"
    parts.append(f"""<div class="card">
  <h2>EQUITY SUMMARY</h2>
  <div class="metric-grid">
    <div class="metric">
      <div class="metric-value {day_pnl_cls}">{_html_escape(_fmt_pnl(d['day_pnl']))}</div>
      <div class="metric-label">Today P&amp;L ({d['day_pnl_pct']:+.2f}%)</div>
    </div>
    <div class="metric">
      <div class="metric-value">{_html_escape(_fmt_inr(d['equity']))}</div>
      <div class="metric-label">Equity</div>
    </div>
    <div class="metric">
      <div class="metric-value">{_html_escape(_fmt_inr(d['peak_equity']))}</div>
      <div class="metric-label">Peak Equity</div>
    </div>
    <div class="metric">
      <div class="metric-value {dd_cls}">{d['drawdown_pct']:.2f}%</div>
      <div class="metric-label">Drawdown</div>
    </div>
  </div>
</div>
""")

    # Today's trades
    trades = d["today_trades"]
    parts.append(f"""<div class="card">
  <h2>TODAY'S TRADES ({len(trades)})</h2>
""")
    if trades:
        parts.append("""  <table>
    <thead>
      <tr><th>Symbol</th><th>Strategy</th><th>Side</th><th>P&amp;L</th><th>Return %</th></tr>
    </thead>
    <tbody>
""")
        for t in trades:
            pnl = float(t.get("pnl", 0.0))
            pnl_cls = "win" if pnl >= 0 else "loss"
            r = _risk_multiple(t)
            r_str = f"{r:+.1f}%" if r is not None else "—"
            parts.append(
                f"      <tr>"
                f"<td>{_html_escape(str(t.get('symbol', '')))}</td>"
                f"<td>{_html_escape(str(t.get('strategy') or ''))}</td>"
                f"<td>{_html_escape(str(t.get('side', '')).upper())}</td>"
                f"<td class='{pnl_cls}'>{_html_escape(_fmt_pnl(pnl))}</td>"
                f"<td class='{pnl_cls}'>{r_str}</td>"
                f"</tr>\n"
            )
        parts.append("    </tbody>\n  </table>\n")
    else:
        parts.append("  <p class='neutral'>No closed trades today.</p>\n")
    parts.append("</div>\n")

    # Strategy performance
    strat_perf = d["strat_perf"]
    parts.append("""<div class="card">
  <h2>STRATEGY PERFORMANCE (last 30 days)</h2>
""")
    if strat_perf:
        parts.append("""  <table>
    <thead>
      <tr><th>Strategy</th><th>Trades</th><th>Win %</th><th>PF</th><th>Net P&amp;L</th></tr>
    </thead>
    <tbody>
""")
        for sp in strat_perf:
            pnl_cls = "win" if sp["net_pnl"] >= 0 else "loss"
            pf = sp["profit_factor"]
            pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
            parts.append(
                f"      <tr>"
                f"<td>{_html_escape(sp['strategy'])}</td>"
                f"<td>{sp['trades']}</td>"
                f"<td>{sp['win_rate']:.1f}%</td>"
                f"<td>{pf_str}</td>"
                f"<td class='{pnl_cls}'>{_html_escape(_fmt_pnl(sp['net_pnl']))}</td>"
                f"</tr>\n"
            )
        parts.append("    </tbody>\n  </table>\n")
    else:
        parts.append("  <p class='neutral'>No data for last 30 days.</p>\n")
    parts.append("</div>\n")

    # Open positions
    open_pos = d["open_positions"]
    parts.append(f"""<div class="card">
  <h2>CURRENT POSITIONS ({len(open_pos)} open)</h2>
""")
    if open_pos:
        parts.append("""  <table>
    <thead>
      <tr><th>Symbol</th><th>Side</th><th>Stop Loss</th><th>Target</th><th>Unrealised P&amp;L</th></tr>
    </thead>
    <tbody>
""")
        for pos in open_pos:
            unreal = float(pos.get("unrealized_pnl") or 0.0)
            pnl_cls = "win" if unreal >= 0 else "loss"
            sl = pos.get("stop_loss")
            tgt = pos.get("target")
            sl_str = f"{sl:,.0f}" if sl is not None else "—"
            tgt_str = f"{tgt:,.0f}" if tgt is not None else "—"
            parts.append(
                f"      <tr>"
                f"<td>{_html_escape(str(pos.get('symbol', '')))}</td>"
                f"<td>{_html_escape(str(pos.get('side') or 'BUY').upper())}</td>"
                f"<td>{sl_str}</td>"
                f"<td>{tgt_str}</td>"
                f"<td class='{pnl_cls}'>{_html_escape(_fmt_pnl(unreal))}</td>"
                f"</tr>\n"
            )
        parts.append("    </tbody>\n  </table>\n")
    else:
        parts.append("  <p class='neutral'>No open positions.</p>\n")
    parts.append("</div>\n")

    parts.append("</body>\n</html>")
    return "".join(parts)
