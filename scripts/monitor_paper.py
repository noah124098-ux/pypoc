"""scripts/monitor_paper.py — live terminal dashboard for the NSE paper-trading agent.

Polls data/snapshot.json every 10 seconds and renders a refreshed dashboard.
Run this in a separate terminal while the agent is running:

    python scripts/monitor_paper.py
    python scripts/monitor_paper.py --snapshot data/snapshot.json --interval 5
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _read_snapshot(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _fmt_inr(amount: float) -> str:
    """Format INR with Indian grouping: ₹5,12,345"""
    sign = ""
    if amount < 0:
        sign = "-"
        amount = -amount
    s = f"{amount:.0f}"
    # Indian grouping: last 3 digits, then groups of 2
    if len(s) > 3:
        last3 = s[-3:]
        rest = s[:-3]
        groups = []
        while len(rest) > 2:
            groups.append(rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.append(rest)
        groups.reverse()
        s = ",".join(groups) + "," + last3
    return f"{sign}₹{s}"


def _fmt_pnl(pnl: float) -> str:
    """Format P&L with sign prefix."""
    if pnl >= 0:
        return f"+{_fmt_inr(pnl)}"
    return _fmt_inr(pnl)


def _fmt_pct(pct: float) -> str:
    if pct >= 0:
        return f"+{pct:.2f}%"
    return f"{pct:.2f}%"


def _uptime(start_ts: str | None) -> str:
    """Return human-readable uptime from an ISO timestamp."""
    if not start_ts:
        return "?"
    try:
        start = datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - start
        total_seconds = int(delta.total_seconds())
        hours, remainder = divmod(max(total_seconds, 0), 3600)
        minutes = remainder // 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    except Exception:
        return "?"


def _feed_age(age_seconds: float | None) -> str:
    if age_seconds is None:
        return "?"
    age = int(age_seconds)
    if age < 60:
        return f"{age}s ago"
    return f"{age // 60}m ago"


def _dma_arrow(snap: dict, dma_key: str) -> str:
    """Extract DMA trend indicator from extras if present, else return '?'."""
    extras = snap.get("extras", {})
    val = extras.get(dma_key)
    if val is None:
        return "?"
    return "↑" if val else "↓"


def _regime_line(snap: dict) -> str:
    regime = snap.get("current_regime", "UNKNOWN")
    adx = snap.get("extras", {}).get("adx")
    vix = snap.get("vix", 0.0)
    adx_str = f", ADX {adx:.1f}" if adx is not None else ""
    vix_str = f", VIX {vix:.1f}" if vix else ""
    return f"{regime} ({adx_str.lstrip(', ')}{vix_str})"


def _pad(s: str, width: int) -> str:
    """Left-align and pad/truncate to width."""
    s = str(s)
    if len(s) > width:
        return s[:width]
    return s.ljust(width)


def _rpad(s: str, width: int) -> str:
    """Right-align to width."""
    s = str(s)
    if len(s) > width:
        return s[-width:]
    return s.rjust(width)


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

CLEAR = "\033[2J\033[H"
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
DIM = "\033[2m"


def _color_pnl(pnl: float, text: str) -> str:
    if pnl > 0:
        return f"{GREEN}{text}{RESET}"
    if pnl < 0:
        return f"{RED}{text}{RESET}"
    return text


def _render(snap: dict, now_ist: str) -> str:
    lines: list[str] = []
    sep = "=" * 72

    # --- header ---
    equity = float(snap.get("equity", 0.0))
    peak = float(snap.get("peak_equity", equity) or equity)
    start_today = float(snap.get("starting_equity_today", equity) or equity)
    day_pnl = equity - start_today
    day_pnl_pct = (day_pnl / start_today * 100.0) if start_today else 0.0
    dd_pct = ((peak - equity) / peak * 100.0) if peak else 0.0

    regime_str = _regime_line(snap)
    dma50 = _dma_arrow(snap, "dma50_up")
    dma200 = _dma_arrow(snap, "dma200_up")

    uptime_str = _uptime(snap.get("ts"))

    lines.append(sep)
    lines.append(f"{BOLD}=== NSE Paper Agent Monitor ==={RESET}")
    lines.append(f"Time: {now_ist} IST | Uptime: {uptime_str}")
    lines.append(sep)

    # --- equity / regime two-column ---
    eq_col = (
        f"{BOLD}{_fmt_inr(equity)}{RESET} "
        f"({_color_pnl(day_pnl, _fmt_pnl(day_pnl) + ', ' + _fmt_pct(day_pnl_pct))})"
    )
    eq_peak = f"Peak: {_fmt_inr(peak)} | DD: {dd_pct:.2f}%"
    regime_col = f"{CYAN}{regime_str}{RESET}"
    dma_col = f"50-DMA: {dma50} | 200-DMA: {dma200}"

    lines.append(f"{'EQUITY':<38} {'REGIME'}")
    lines.append(f"{eq_col:<55} {regime_col}")
    lines.append(f"{eq_peak:<38} {dma_col}")
    lines.append("")

    # --- positions ---
    positions = snap.get("open_positions", [])
    max_pos = snap.get("extras", {}).get("max_open_positions", 5)
    lines.append(f"{BOLD}OPEN POSITIONS ({len(positions)}/{max_pos}){RESET}")
    if not positions:
        lines.append(f"  {DIM}No open positions{RESET}")
    else:
        hdr = (
            f"  {'SYMBOL':<12} {'SIDE':<5} {'QTY':>5}  "
            f"{'AVG':>8}  {'LAST':>8}  {'P&L':>12}  {'SL':>8}  {'TGT':>8}"
        )
        lines.append(hdr)
        lines.append("  " + "-" * 70)
        for p in positions:
            sym = _pad(p.get("symbol", "?"), 12)
            side = _pad(p.get("side", "?"), 5)
            qty = _rpad(str(p.get("qty", 0)), 5)
            avg = _rpad(f"{p.get('avg_price', 0):.0f}", 8)
            last = _rpad(f"{p.get('last_price', 0):.0f}", 8)
            upnl = float(p.get("unrealized_pnl", 0.0))
            pnl_str = _color_pnl(upnl, _rpad(_fmt_pnl(upnl), 12))
            sl = _rpad(f"{p.get('stop_loss', 0):.0f}", 8)
            tgt = _rpad(f"{p.get('target', 0):.0f}", 8)
            lines.append(
                f"  {sym} {side} {qty}sh  {avg}  {last}  {pnl_str}  {sl}  {tgt}"
            )
    lines.append("")

    # --- today's stats ---
    # Pull from extras if available (orchestrator can populate), else show snapshot defaults
    extras = snap.get("extras", {})
    trades = extras.get("trades_today", 0)
    wins = extras.get("wins_today", 0)
    losses = extras.get("losses_today", 0)
    today_pnl = extras.get("realized_pnl_today", snap.get("realized_pnl", 0.0))
    signals_total = extras.get("signals_today", 0)
    signals_accepted = extras.get("signals_accepted_today", 0)
    signals_rejected = extras.get("signals_rejected_today", 0)

    lines.append(f"{BOLD}TODAY'S STATS{RESET}")
    pnl_display = _color_pnl(today_pnl, _fmt_pnl(today_pnl))
    lines.append(
        f"Trades: {trades:>3}  |  Wins: {wins}  |  Losses: {losses}  |  P&L: {pnl_display}"
    )
    lines.append(
        f"Signals: {signals_total:>3}  |  Accepted: {signals_accepted}  |  Rejected: {signals_rejected}"
    )
    lines.append("")

    # --- status line ---
    halted = snap.get("halted", False)
    halt_reason = snap.get("halt_reason", "")
    feed_ok = snap.get("feed_connected", False)
    tick_age = snap.get("last_tick_age_seconds")

    if halted:
        status_str = f"{RED}HALTED{RESET}"
        halt_display = halt_reason or "manual"
    else:
        status_str = f"{GREEN}RUNNING{RESET}"
        halt_display = "None"

    feed_str = (
        f"{GREEN}Live ({_feed_age(tick_age)}){RESET}"
        if feed_ok
        else f"{RED}Disconnected{RESET}"
    )

    lines.append(f"{BOLD}STATUS:{RESET} {status_str} | Feed: {feed_str} | Halt: {halt_display}")
    lines.append(sep)
    lines.append(f"{DIM}Press Ctrl+C to exit{RESET}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# main loop
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live terminal monitor for the NSE paper-trading agent"
    )
    parser.add_argument(
        "--snapshot",
        default="data/snapshot.json",
        help="Path to snapshot.json (default: data/snapshot.json)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=10.0,
        help="Poll interval in seconds (default: 10)",
    )
    args = parser.parse_args()

    snapshot_path = Path(args.snapshot)
    interval = max(1.0, float(args.interval))

    print(f"Watching {snapshot_path} every {interval:.0f}s — Ctrl+C to exit")

    try:
        while True:
            try:
                import zoneinfo
                tz = zoneinfo.ZoneInfo("Asia/Kolkata")
                now_ist = datetime.now(tz).strftime("%H:%M:%S")
            except Exception:
                now_ist = datetime.now().strftime("%H:%M:%S")

            snap = _read_snapshot(snapshot_path)
            if snap is None:
                output = (
                    f"{CLEAR}{BOLD}=== NSE Paper Agent Monitor ==={RESET}\n"
                    f"Time: {now_ist} IST\n\n"
                    f"{YELLOW}Waiting for agent to start...{RESET}\n"
                    f"Snapshot not found at: {snapshot_path}\n\n"
                    f"Start the agent:  python cli.py run\n\n"
                    f"{DIM}Press Ctrl+C to exit{RESET}"
                )
            else:
                output = CLEAR + _render(snap, now_ist)

            sys.stdout.write(output + "\n")
            sys.stdout.flush()

            time.sleep(interval)

    except KeyboardInterrupt:
        print("\nMonitor stopped.")


if __name__ == "__main__":
    main()
