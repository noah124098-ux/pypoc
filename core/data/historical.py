"""Historical OHLC fetcher with fallback chain: yfinance -> nsepython.

Used by:
  - regime classifier warm-up at agent start
  - backtest engine
  - strategies that need n-day lookback before live ticks accumulate
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

log = logging.getLogger("agent.historical")


def fetch_daily(symbol: str, days: int = 365) -> Optional[pd.DataFrame]:
    """Fetch daily OHLC for a Nifty 50 symbol. Returns DataFrame indexed by date."""
    try:
        import yfinance as yf

        ticker = f"{symbol}.NS"
        end = datetime.now()
        start = end - timedelta(days=days + 30)
        df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False)
        if df is None or df.empty:
            return None
        df = df.rename(columns=str.lower)
        df.index = pd.to_datetime(df.index)
        return df[["open", "high", "low", "close", "volume"]].tail(days)
    except Exception as e:
        log.warning("yfinance failed for %s: %s — trying nsepython", symbol, e)

    try:
        from nsepython import equity_history

        end = datetime.now().strftime("%d-%m-%Y")
        start = (datetime.now() - timedelta(days=days + 30)).strftime("%d-%m-%Y")
        df = equity_history(symbol, "EQ", start, end)
        if df is None or df.empty:
            return None
        df = df.rename(
            columns={
                "CH_OPENING_PRICE": "open",
                "CH_TRADE_HIGH_PRICE": "high",
                "CH_TRADE_LOW_PRICE": "low",
                "CH_CLOSING_PRICE": "close",
                "CH_TOT_TRADED_QTY": "volume",
            }
        )
        df.index = pd.to_datetime(df["CH_TIMESTAMP"])
        return df[["open", "high", "low", "close", "volume"]].tail(days)
    except Exception as e:
        log.error("nsepython also failed for %s: %s", symbol, e)
        return None
