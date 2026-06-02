"""Shared Plotly chart builders and style helpers for the dashboard."""
from __future__ import annotations

import html as _html


def safe_html(text: str) -> str:
    """Escape user-controlled or LLM-generated text before injecting into HTML."""
    return _html.escape(str(text) if text else "")


def color_pnl(val) -> str:
    """Pandas Styler applymap callback — color a P&L cell green/red/gray."""
    color = "green" if val > 0 else ("red" if val < 0 else "gray")
    return f"color: {color}; font-weight: bold"


def regime_color(regime: str) -> str:
    """Return a hex color string for a regime label."""
    return {
        "TREND": "#2ecc71",
        "RANGE": "#3498db",
        "VOLATILE": "#e74c3c",
        "UNKNOWN": "#95a5a6",
    }.get(regime, "#95a5a6")


def fmt_inr(value: float) -> str:
    """Format a number in Indian number system (lakhs/crores) with ₹ prefix.

    Examples:  500000 → ₹5,00,000   1234567 → ₹12,34,567   999 → ₹999
    """
    try:
        val = int(round(abs(value)))
        s = str(val)
        if len(s) <= 3:
            formatted = s
        elif len(s) <= 5:
            formatted = s[:-3] + "," + s[-3:]
        else:
            # First group of 3 from right, then groups of 2
            last3 = s[-3:]
            rest = s[:-3]
            groups = []
            while len(rest) > 2:
                groups.append(rest[-2:])
                rest = rest[:-2]
            groups.append(rest)
            groups.reverse()
            formatted = ",".join(groups) + "," + last3
        sign = "-" if value < 0 else ""
        return f"₹{sign}{formatted}"
    except Exception:
        return f"₹{value:,.0f}"


def time_ago(ts_str: str) -> str:
    """Return a human-readable 'X ago' string from an ISO timestamp."""
    if not ts_str:
        return "unknown"
    try:
        from datetime import datetime
        opened = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00").replace("+00:00", ""))
        delta = datetime.utcnow() - opened
        total_secs = int(delta.total_seconds())
        if total_secs < 60:
            return f"{total_secs}s ago"
        if total_secs < 3600:
            return f"{total_secs // 60}m ago"
        if total_secs < 86400:
            return f"{total_secs // 3600}h {(total_secs % 3600) // 60}m ago"
        return f"{total_secs // 86400}d ago"
    except Exception:
        return str(ts_str)[:16]


def is_market_hours() -> bool:
    """Return True if current time (IST) is within NSE market hours (09:15-15:30 Mon-Fri)."""
    try:
        from datetime import datetime, timedelta
        now_utc = datetime.utcnow()
        # IST = UTC + 5:30
        now_ist = now_utc + timedelta(hours=5, minutes=30)
        if now_ist.weekday() >= 5:  # Saturday=5, Sunday=6
            return False
        market_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
        return market_open <= now_ist <= market_close
    except Exception:
        return False


def pnl_by_period(trades_df) -> dict:
    """Return dict of period -> grouped P&L DataFrames."""
    import pandas as pd
    from datetime import datetime, timedelta
    if trades_df.empty:
        return {}
    df = trades_df.copy()
    df["closed_at"] = pd.to_datetime(df["closed_at"])
    df["pnl_net"] = df["pnl"] - df["charges"]
    df["date"] = df["closed_at"].dt.date
    df["week"] = df["closed_at"].dt.to_period("W").apply(lambda x: str(x.start_time.date()))
    df["month"] = df["closed_at"].dt.to_period("M").astype(str)
    df["quarter"] = df["closed_at"].dt.to_period("Q").astype(str)

    cutoff_3m = datetime.utcnow() - timedelta(days=90)
    df_3m = df[df["closed_at"] >= cutoff_3m]

    return {
        "daily": df.groupby("date")["pnl_net"].sum().reset_index().rename(columns={"date": "period"}),
        "weekly": df.groupby("week")["pnl_net"].sum().reset_index().rename(columns={"week": "period"}),
        "monthly": df.groupby("month")["pnl_net"].sum().reset_index().rename(columns={"month": "period"}),
        "3month": df_3m.groupby("month")["pnl_net"].sum().reset_index().rename(columns={"month": "period"}),
    }
