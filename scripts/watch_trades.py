"""scripts/watch_trades.py — real-time tail of the structured JSONL event log.

Continuously follows logs/events.jsonl (or any path given via --log) and prints
new events as they arrive, with optional type filtering.

Usage:
    python scripts/watch_trades.py                    # all events
    python scripts/watch_trades.py --type order       # only order events
    python scripts/watch_trades.py --type signal      # only signal events
    python scripts/watch_trades.py --type daily_summary,order
    python scripts/watch_trades.py --log logs/events.jsonl --type order
    python scripts/watch_trades.py --last 50          # replay last N lines first
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
DIM = "\033[2m"
BLUE = "\033[34m"

# Color per event type
EVENT_COLORS = {
    "order": GREEN,
    "signal": CYAN,
    "daily_summary": MAGENTA,
    "regime": YELLOW,
    "halt": RED,
    "resume": GREEN,
}


def _color(event_type: str, text: str) -> str:
    c = EVENT_COLORS.get(event_type, RESET)
    return f"{c}{text}{RESET}"


def _fmt_inr(amount: float) -> str:
    sign = ""
    if amount < 0:
        sign = "-"
        amount = -amount
    s = f"{amount:.0f}"
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
    if pnl >= 0:
        return f"+{_fmt_inr(pnl)}"
    return _fmt_inr(pnl)


def _local_time(iso_ts: str) -> str:
    """Convert UTC ISO timestamp to IST HH:MM:SS."""
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo("Asia/Kolkata")
            dt = dt.astimezone(tz)
        except Exception:
            pass
        return dt.strftime("%H:%M:%S")
    except Exception:
        return iso_ts[:19]


# ---------------------------------------------------------------------------
# Formatters per event type
# ---------------------------------------------------------------------------

def _fmt_order(ev: dict) -> str:
    status = ev.get("status", "?")
    sym = ev.get("symbol", "?")
    side = ev.get("side", "?")
    qty = ev.get("qty", 0)
    price = ev.get("price", 0.0)
    rejection = ev.get("rejection")

    color = GREEN if status == "FILLED" else RED
    line = f"{color}{BOLD}{status:8}{RESET}  {sym:<12} {side:<5} {qty:>4}sh @ ₹{price:.2f}"
    if rejection:
        line += f"  {DIM}({rejection}){RESET}"
    return line


def _fmt_signal(ev: dict) -> str:
    allow = ev.get("allow", False)
    sym = ev.get("symbol", "?")
    strategy = ev.get("strategy", "?")
    regime = ev.get("regime", "?")
    qty = ev.get("qty", 0)
    rule = ev.get("rule", "")
    reason = ev.get("reason", "")

    if allow:
        verdict = f"{GREEN}ACCEPT{RESET}"
        detail = f"{qty}sh"
    else:
        verdict = f"{RED}REJECT{RESET}"
        detail = f"{rule}: {reason}" if rule else reason

    return f"{verdict}  {sym:<12} [{strategy}/{regime}]  {detail}"


def _fmt_daily_summary(ev: dict) -> str:
    equity = ev.get("equity", 0.0)
    day_pnl = ev.get("day_pnl", 0.0)
    day_pnl_pct = ev.get("day_pnl_pct", 0.0)
    trades = ev.get("trades_today", 0)
    regimes = ev.get("regime_distribution", {})

    pnl_str = _fmt_pnl(day_pnl)
    pct_str = f"{'+' if day_pnl_pct >= 0 else ''}{day_pnl_pct:.2f}%"
    color = GREEN if day_pnl >= 0 else RED
    regime_str = ", ".join(f"{k}:{v}" for k, v in regimes.items()) if regimes else "—"

    return (
        f"{BOLD}EOD{RESET}  equity={_fmt_inr(equity)}  "
        f"pnl={color}{pnl_str} ({pct_str}){RESET}  "
        f"trades={trades}  regimes=[{regime_str}]"
    )


def _fmt_regime(ev: dict) -> str:
    regime = ev.get("regime", "?")
    adx = ev.get("adx")
    vix = ev.get("vix")
    parts = [f"{BOLD}{regime}{RESET}"]
    if adx is not None:
        parts.append(f"ADX={adx:.1f}")
    if vix is not None:
        parts.append(f"VIX={vix:.1f}")
    return "  ".join(parts)


def _fmt_generic(ev: dict) -> str:
    """Fallback: print all fields except ts and event."""
    parts = []
    for k, v in ev.items():
        if k in ("ts", "event"):
            continue
        parts.append(f"{k}={v}")
    return "  ".join(parts) if parts else "(no fields)"


# ---------------------------------------------------------------------------
# Event formatter dispatch
# ---------------------------------------------------------------------------

FORMATTERS = {
    "order": _fmt_order,
    "signal": _fmt_signal,
    "daily_summary": _fmt_daily_summary,
    "regime": _fmt_regime,
}


def _format_event(ev: dict) -> str:
    event_type = ev.get("event", "unknown")
    ts = ev.get("ts", "")
    time_str = _local_time(ts)

    fmt = FORMATTERS.get(event_type, _fmt_generic)
    body = fmt(ev)

    prefix = f"{DIM}{time_str}{RESET}  {_color(event_type, f'[{event_type}]'):<24}"
    return f"{prefix}  {body}"


# ---------------------------------------------------------------------------
# JSONL tail implementation
# ---------------------------------------------------------------------------

def _parse_line(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _tail_file(
    path: Path,
    type_filter: set[str] | None,
    last_n: int,
    poll_interval: float,
) -> None:
    """
    Open the file, seek to end (or replay last_n lines), then follow new writes.
    Handles file rotation: if the file shrinks, re-open from the beginning.
    """

    def _open_file():
        try:
            return path.open("r", encoding="utf-8")
        except OSError:
            return None

    def _matches(ev: dict) -> bool:
        if type_filter is None:
            return True
        return ev.get("event") in type_filter

    # --- replay last N lines if requested ---
    if last_n > 0 and path.exists():
        try:
            all_lines = path.read_text(encoding="utf-8").splitlines()
            tail_lines = all_lines[-last_n:] if len(all_lines) > last_n else all_lines
            print(f"{DIM}--- replaying last {len(tail_lines)} lines ---{RESET}")
            for raw in tail_lines:
                ev = _parse_line(raw)
                if ev and _matches(ev):
                    print(_format_event(ev))
            print(f"{DIM}--- live tail starts below ---{RESET}")
        except OSError:
            pass

    # --- live tail ---
    fh = _open_file()
    if fh:
        fh.seek(0, 2)  # seek to end
        current_size = fh.tell()
    else:
        current_size = 0

    while True:
        time.sleep(poll_interval)

        if not path.exists():
            if fh:
                fh.close()
                fh = None
            continue

        if fh is None:
            fh = _open_file()
            if fh:
                fh.seek(0, 2)
                current_size = fh.tell()
            continue

        # Detect rotation or truncation
        try:
            new_size = path.stat().st_size
        except OSError:
            fh.close()
            fh = None
            current_size = 0
            continue

        if new_size < current_size:
            # File was truncated / rotated
            fh.close()
            fh = _open_file()
            if fh:
                current_size = fh.tell()
            else:
                current_size = 0
            continue

        current_size = new_size

        for raw in fh:
            ev = _parse_line(raw)
            if ev is None:
                continue
            if _matches(ev):
                print(_format_event(ev))
                sys.stdout.flush()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Tail the NSE agent structured event log in real-time"
    )
    parser.add_argument(
        "--log",
        default="logs/events.jsonl",
        help="Path to events JSONL file (default: logs/events.jsonl)",
    )
    parser.add_argument(
        "--type",
        default=None,
        dest="event_types",
        help=(
            "Comma-separated list of event types to show "
            "(e.g. order,signal,daily_summary,regime). "
            "Default: show all."
        ),
    )
    parser.add_argument(
        "--last",
        type=int,
        default=0,
        metavar="N",
        help="Replay the last N lines before tailing (default: 0 = skip history)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.5,
        help="Poll interval in seconds (default: 0.5)",
    )
    args = parser.parse_args()

    log_path = Path(args.log)

    type_filter: set[str] | None = None
    if args.event_types:
        type_filter = {t.strip().lower() for t in args.event_types.split(",")}

    # Print header
    filter_label = ", ".join(sorted(type_filter)) if type_filter else "all"
    print(f"{BOLD}=== NSE Agent Event Watcher ==={RESET}")
    print(f"Log:    {log_path}")
    print(f"Filter: {filter_label}")
    print(f"{DIM}Press Ctrl+C to exit{RESET}")
    print()

    if not log_path.exists():
        print(
            f"{YELLOW}Warning: {log_path} does not exist yet. "
            f"Waiting for agent to create it...{RESET}"
        )

    try:
        _tail_file(
            path=log_path,
            type_filter=type_filter,
            last_n=args.last,
            poll_interval=args.interval,
        )
    except KeyboardInterrupt:
        print("\nWatcher stopped.")


if __name__ == "__main__":
    main()
