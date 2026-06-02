"""Design system for the NSE Trading Agent dashboard.

Single source of truth for colors, metric display, badge styling,
section headers, and DataFrame column configs.

Usage:
    from dashboard.design import COLORS, _metric_card, _badge, _section
"""
from __future__ import annotations

import html as _html

import streamlit as st

# ---------------------------------------------------------------------------
# 1. CONSISTENT COLOR PALETTE
# ---------------------------------------------------------------------------

COLORS: dict[str, str] = {
    "profit": "#00C853",    # green  — positive P&L, wins, connected
    "loss": "#FF1744",      # red    — negative P&L, losses, errors
    "neutral": "#9E9E9E",   # grey   — zero / neutral / unknown values
    "trend": "#1565C0",     # blue   — TREND regime
    "range": "#6A1B9A",     # purple — RANGE regime
    "volatile": "#E65100",  # orange — VOLATILE regime
    "unknown": "#424242",   # dark grey — UNKNOWN regime / missing data
    "warning": "#FF8F00",   # amber  — warnings, near-threshold states
    "accent": "#0288D1",    # light blue — accent / highlight / live data
}

# Regime -> COLORS key mapping (used in multiple places)
REGIME_COLORS: dict[str, str] = {
    "TREND":    COLORS["trend"],
    "RANGE":    COLORS["range"],
    "VOLATILE": COLORS["volatile"],
    "UNKNOWN":  COLORS["unknown"],
}


def regime_hex(regime: str) -> str:
    """Return the canonical hex color for a regime label."""
    return REGIME_COLORS.get(str(regime).upper(), COLORS["unknown"])


# ---------------------------------------------------------------------------
# 2. CONSISTENT METRIC DISPLAY
# ---------------------------------------------------------------------------

def _metric_card(
    label: str,
    value: str,
    delta: float | str | None = None,
    color: str | None = None,
    help: str | None = None,
) -> None:
    """Render a metric with consistent styling.

    Args:
        label:  Metric label shown above the value.
        value:  Pre-formatted string value (e.g. '₹1,23,456').
        delta:  Numeric delta (float) or pre-formatted string.  When a float
                is supplied the sign determines delta_color automatically.
        color:  Unused kwarg kept for API compatibility; styling is handled
                by Streamlit's built-in delta_color logic.
        help:   Optional tooltip text.
    """
    if isinstance(delta, (int, float)):
        delta_color = "normal" if delta >= 0 else "inverse"
        delta_str: str | None = f"{delta:+.2f}"
    elif isinstance(delta, str):
        # Try to infer direction from the string so callers don't need to
        delta_color = "off"
        delta_str = delta
    else:
        delta_color = "off"
        delta_str = None

    kwargs: dict = dict(label=label, value=value, delta=delta_str, delta_color=delta_color)
    if help is not None:
        kwargs["help"] = help
    st.metric(**kwargs)


# ---------------------------------------------------------------------------
# 3. CONSISTENT BADGE STYLING
# ---------------------------------------------------------------------------

def _safe_html(text: str) -> str:
    """HTML-escape text for safe injection into unsafe_allow_html blocks."""
    return _html.escape(str(text) if text else "")


def _badge(text: str, color: str, size: str = "small") -> None:
    """Render a colored inline badge.

    Args:
        text:  Badge label (will be HTML-escaped).
        color: Background hex color (e.g. COLORS['profit']).
        size:  'small' (default, 11px) or 'normal' (13px).
    """
    font_size = "11px" if size == "small" else "13px"
    st.markdown(
        f"<span style=\"background:{color};padding:2px 8px;border-radius:4px;"
        f"font-size:{font_size};color:white;font-weight:600\">"
        f"{_safe_html(text)}</span>",
        unsafe_allow_html=True,
    )


def _regime_badge(regime: str, size: str = "small") -> None:
    """Render a regime badge using the canonical regime color palette."""
    _badge(regime, regime_hex(regime), size=size)


def _status_badge(
    label: str,
    ok: bool,
    true_color: str | None = None,
    false_color: str | None = None,
    size: str = "small",
) -> None:
    """Render a boolean status badge (green when ok, red when not ok by default)."""
    color = (true_color or COLORS["profit"]) if ok else (false_color or COLORS["loss"])
    _badge(label, color, size=size)


# ---------------------------------------------------------------------------
# 4. CONSISTENT SECTION HEADERS
# ---------------------------------------------------------------------------

def _section(title: str, subtitle: str | None = None) -> None:
    """Render a consistent section header with optional subtitle and divider."""
    st.markdown(f"#### {title}")
    if subtitle:
        st.caption(subtitle)
    st.divider()


# ---------------------------------------------------------------------------
# 5. CONSISTENT TABLE STYLING HELPERS
# ---------------------------------------------------------------------------

#: Common column_config entries for st.dataframe() calls.
#: Import and spread into column_config= as needed.

def col_inr(label: str, fmt: str = "₹%.0f") -> st.column_config.NumberColumn:
    """Return a NumberColumn config for Indian Rupee monetary values."""
    return st.column_config.NumberColumn(label, format=fmt)


def col_pct(label: str, fmt: str = "%.1f%%") -> st.column_config.NumberColumn:
    """Return a NumberColumn config for percentage values."""
    return st.column_config.NumberColumn(label, format=fmt)


def col_dt(label: str, fmt: str = "DD/MM HH:mm") -> st.column_config.DatetimeColumn:
    """Return a DatetimeColumn config for timestamp columns."""
    return st.column_config.DatetimeColumn(label, format=fmt)


#: Default DataFrame container style (light background, slight rounding).
DF_CONTAINER_STYLE: dict[str, str] = {
    "background-color": "#f8f9fa",
    "border-radius": "4px",
}
