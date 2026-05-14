"""Strategy interface. Strategies emit signals; orchestrator turns signals into orders."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd

from core.types import Regime, Signal


class IStrategy(ABC):
    name: str = "base"
    regimes: list[Regime] = []

    def supports(self, regime: Regime) -> bool:
        return regime in self.regimes

    @abstractmethod
    def evaluate(self, symbol: str, candles: pd.DataFrame, regime: Regime) -> Optional[Signal]:
        """Return a Signal or None given recent candles for this symbol."""
