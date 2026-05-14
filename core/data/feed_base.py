"""Live data feed contract. All feed implementations (Angel One, polling fallback) implement this."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from core.types import Tick


class ILiveFeed(ABC):
    """Subscribe-and-callback live tick feed.

    The contract is intentionally minimal so we can swap broker WebSocket
    implementations without touching downstream consumers.
    """

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def subscribe(self, symbols: list[str]) -> None: ...

    @abstractmethod
    def on_tick(self, callback: Callable[[Tick], None]) -> None: ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def last_tick_age_seconds(self) -> float:
        """Seconds since the last tick was received (any symbol). Used by stale-feed guardrail."""
