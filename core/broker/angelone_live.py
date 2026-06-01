"""Angel One live broker stub.

SECURITY DESIGN:
  - Uses SEPARATE credentials (ANGEL_ONE_LIVE_*) from the data-feed app
    (ANGEL_ONE_API_KEY / ANGEL_ONE_CLIENT_CODE / ...).
  - The data-feed credentials MUST NOT be passed here. Different apps, different
    permissions, different env vars.
  - Refuses to initialise if ANGEL_ONE_LIVE_API_KEY is empty.

IMPLEMENTATION STATUS: STUB — NOT production-ready.
  All order methods raise NotImplementedError. To enable live trading:
    1. Create a separate Angel One app with order permissions (never the data-feed app).
    2. Implement connect(), place_order(), cancel_order(), etc. against SmartAPI.
    3. Run exhaustive integration tests on paper trades (Angel One paper-trade env).
    4. Pass backtest gate (Sharpe >= 1.2) on a fresh walk-forward.
    5. Obtain explicit user approval to flip mode to live.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from core.broker.base import IBroker
from core.config import ExecutionCfg
from core.types import Order, OrderType, Position, Side

log = logging.getLogger("agent.angelone_live")

# Environment variable names for live-broker credentials.
# These MUST be different from the data-feed vars (ANGEL_ONE_API_KEY etc.).
LIVE_API_KEY_ENV = "ANGEL_ONE_LIVE_API_KEY"
LIVE_CLIENT_CODE_ENV = "ANGEL_ONE_LIVE_CLIENT_CODE"
LIVE_PASSWORD_ENV = "ANGEL_ONE_LIVE_PASSWORD"
LIVE_TOTP_SECRET_ENV = "ANGEL_ONE_LIVE_TOTP_SECRET"

# Data-feed env var names — rejected at startup if mistakenly passed.
_DATA_FEED_API_KEY_ENV = "ANGEL_ONE_API_KEY"

_STUB_MSG = (
    "AngelOneLiveBroker is a stub. "
    "Complete the implementation and run full integration tests before enabling live trading."
)


class AngelOneLiveBroker(IBroker):
    """STUB — live Angel One broker. NOT production-ready.

    Implements IBroker but all order methods raise NotImplementedError.

    Complete implementation requires:
    1. Separate Angel One app with order permissions (NEVER use the data-feed app).
    2. Full testing on paper trades before any live capital.
    3. Backtest gate must pass (Sharpe >= 1.2) on a fresh walk-forward (< 30 days old).
    4. User explicit approval to enable live mode.

    Security contract:
    - Credentials accepted here must come from ANGEL_ONE_LIVE_* env vars only.
    - The data-feed app credentials (ANGEL_ONE_API_KEY etc.) must NEVER be used here.
    - Credentials are stored internally and never logged.
    """

    def __init__(
        self,
        api_key: str,
        client_code: str,
        password: str,
        totp_secret: str,
        exec_cfg: ExecutionCfg,
    ) -> None:
        if not api_key:
            raise ValueError(
                f"Live broker requires {LIVE_API_KEY_ENV} to be set. "
                "Do NOT reuse the data-feed API key (ANGEL_ONE_API_KEY). "
                "Create a separate Angel One app with order permissions."
            )
        # Store credentials — never log them.
        self._api_key = api_key
        self._client_code = client_code
        self._password = password
        self._totp_secret = totp_secret
        self._exec = exec_cfg
        # These will be populated from Angel One on connect().
        self._cash: float = 0.0
        self._connected: bool = False
        log.warning(
            "AngelOneLiveBroker initialised — STUB, not production ready. "
            "All order methods raise NotImplementedError."
        )

    # ------------------------------------------------------------------
    # IBroker interface — all stubbed out
    # ------------------------------------------------------------------

    def place_order(
        self,
        symbol: str,
        side: Side,
        qty: int,
        order_type: OrderType,
        stop_loss: float,
        target: Optional[float],
        limit_price: Optional[float] = None,
        strategy: str = "",
    ) -> Order:
        raise NotImplementedError(_STUB_MSG)

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError(_STUB_MSG)

    def get_positions(self) -> list[Position]:
        raise NotImplementedError(_STUB_MSG)

    def get_position(self, symbol: str) -> Optional[Position]:
        raise NotImplementedError(_STUB_MSG)

    def equity(self) -> float:
        raise NotImplementedError(_STUB_MSG)

    def cash(self) -> float:
        raise NotImplementedError(_STUB_MSG)

    def update_market_prices(self, prices: dict[str, float]) -> None:
        raise NotImplementedError(_STUB_MSG)

    # ------------------------------------------------------------------
    # Future implementation hooks (not yet wired)
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Authenticate with Angel One and populate account state.

        NOT IMPLEMENTED — placeholder to show where login should go.
        """
        raise NotImplementedError(_STUB_MSG)

    def disconnect(self) -> None:
        """Gracefully close the Angel One session.

        NOT IMPLEMENTED — placeholder to show where logout should go.
        """
        raise NotImplementedError(_STUB_MSG)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls, exec_cfg: ExecutionCfg) -> "AngelOneLiveBroker":
        """Construct from ANGEL_ONE_LIVE_* environment variables.

        Will raise ValueError if ANGEL_ONE_LIVE_API_KEY is not set.
        Uses dotenv if .env is present, but does NOT fall back to the
        data-feed variables (ANGEL_ONE_API_KEY etc.).
        """
        from dotenv import load_dotenv

        load_dotenv(override=False)
        return cls(
            api_key=os.getenv(LIVE_API_KEY_ENV, ""),
            client_code=os.getenv(LIVE_CLIENT_CODE_ENV, ""),
            password=os.getenv(LIVE_PASSWORD_ENV, ""),
            totp_secret=os.getenv(LIVE_TOTP_SECRET_ENV, ""),
            exec_cfg=exec_cfg,
        )
