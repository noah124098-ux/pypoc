"""Tick -> candle aggregator tests."""
from __future__ import annotations

from datetime import datetime, timedelta

from core.data.aggregator import CandleAggregator
from core.types import Candle, Tick


def _tick(symbol: str, price: float, ts: datetime) -> Tick:
    return Tick(symbol=symbol, ltp=price, bid=price - 0.1, ask=price + 0.1, volume=100, ts=ts)


def test_emits_candle_on_bucket_rollover():
    agg = CandleAggregator(intervals=["1m"])
    received: list[Candle] = []
    agg.on_candle(received.append)

    base = datetime(2026, 5, 13, 10, 30, 0)
    agg.ingest(_tick("X", 100, base))
    agg.ingest(_tick("X", 102, base + timedelta(seconds=15)))
    agg.ingest(_tick("X", 99, base + timedelta(seconds=30)))
    # Cross over to next minute
    agg.ingest(_tick("X", 101, base + timedelta(minutes=1, seconds=5)))

    assert len(received) == 1
    c = received[0]
    assert c.open == 100
    assert c.high == 102
    assert c.low == 99
    assert c.close == 99
    assert c.interval == "1m"


def test_history_grows_only_on_close():
    agg = CandleAggregator(intervals=["1m"])
    base = datetime(2026, 5, 13, 10, 30, 0)
    agg.ingest(_tick("X", 100, base))
    agg.ingest(_tick("X", 101, base + timedelta(seconds=10)))
    assert agg.history("X", "1m") == []  # bucket still open
    agg.ingest(_tick("X", 102, base + timedelta(minutes=1)))
    assert len(agg.history("X", "1m")) == 1
