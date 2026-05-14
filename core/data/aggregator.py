"""Tick -> candle aggregator. Takes raw ticks and emits 1-min/5-min/15-min candles on close.

Strategies and the regime classifier subscribe to candle closes — not raw ticks — to keep
decisions deterministic and testable.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from core.types import Candle, Tick

INTERVAL_MAP = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
}


@dataclass
class _Bucket:
    open: float
    high: float
    low: float
    close: float
    volume: int
    bucket_start: datetime


class CandleAggregator:
    """Aggregates ticks into candles per (symbol, interval). Emits on bucket roll-over."""

    def __init__(self, intervals: list[str]):
        for i in intervals:
            if i not in INTERVAL_MAP:
                raise ValueError(f"Unsupported interval: {i}")
        self._intervals = intervals
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._listeners: list[Callable[[Candle], None]] = []
        self._history: dict[tuple[str, str], deque[Candle]] = defaultdict(lambda: deque(maxlen=500))

    def on_candle(self, callback: Callable[[Candle], None]) -> None:
        self._listeners.append(callback)

    def history(self, symbol: str, interval: str) -> list[Candle]:
        return list(self._history[(symbol, interval)])

    def ingest(self, tick: Tick) -> None:
        for interval in self._intervals:
            self._ingest_one(tick, interval)

    def _ingest_one(self, tick: Tick, interval: str) -> None:
        bucket_start = self._floor(tick.ts, interval)
        key = (tick.symbol, interval)
        b = self._buckets.get(key)

        if b is None or b.bucket_start != bucket_start:
            if b is not None:
                # Bucket closed — emit candle.
                candle = Candle(
                    symbol=tick.symbol,
                    interval=interval,
                    open=b.open,
                    high=b.high,
                    low=b.low,
                    close=b.close,
                    volume=b.volume,
                    ts=b.bucket_start,
                )
                self._history[key].append(candle)
                for cb in self._listeners:
                    try:
                        cb(candle)
                    except Exception:
                        pass
            self._buckets[key] = _Bucket(
                open=tick.ltp,
                high=tick.ltp,
                low=tick.ltp,
                close=tick.ltp,
                volume=tick.volume,
                bucket_start=bucket_start,
            )
        else:
            b.high = max(b.high, tick.ltp)
            b.low = min(b.low, tick.ltp)
            b.close = tick.ltp
            b.volume = tick.volume  # cumulative day volume from feed

    def _floor(self, ts: datetime, interval: str) -> datetime:
        delta = INTERVAL_MAP[interval]
        seconds = int(delta.total_seconds())
        epoch = ts.replace(microsecond=0)
        floored = epoch - timedelta(
            seconds=epoch.second + (epoch.minute * 60) % seconds
        )
        # Simpler floor for minute-based intervals:
        minutes = int(delta.total_seconds() // 60)
        floored = ts.replace(second=0, microsecond=0)
        floored = floored - timedelta(minutes=floored.minute % minutes)
        return floored
