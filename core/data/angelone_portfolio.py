"""Angel One SmartAPI live portfolio client. DATA-ONLY — never used for orders.

SECURITY CONSTRAINT (mirrors angelone_history.py):
  Angel One credentials in this codebase are used EXCLUSIVELY for market data
  (live positions, account RMS, profile). They are NEVER used for placing,
  modifying, or cancelling orders. Order execution always goes through
  PaperBroker (in-memory) until a deliberate, separately-credentialed live broker
  integration is added in a future phase.

  Order-placing methods on SmartConnect are neutralized immediately after login
  via `_refuse_order_call`. Any attempt to call them raises RuntimeError.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("agent.ao_portfolio")


@dataclass
class LivePosition:
    symbol: str
    product_type: str   # "DELIVERY" | "INTRADAY"
    qty: int
    avg_price: float
    ltp: float
    pnl: float
    day_change_pct: float


@dataclass
class AccountSummary:
    net_value: float
    available_cash: float
    used_margin: float
    total_pnl_today: float
    positions: list[LivePosition] = field(default_factory=list)


def fetch_live_portfolio(
    api_key: str, client_code: str, password: str, totp_secret: str
) -> Optional[AccountSummary]:
    """Fetch live portfolio from Angel One. Returns None on any failure. Never raises.

    Read-only endpoints used:
      - position()     — open positions with LTP and unrealised P&L
      - getRMS()       — account risk-management / margin summary

    Order-placing SDK methods (placeOrder, modifyOrder, cancelOrder,
    placeOrderFullResponse) are neutralized immediately after login; calling
    them would raise RuntimeError rather than transmit a real order.
    """
    try:
        import pyotp
        from SmartApi import SmartConnect

        obj = SmartConnect(api_key=api_key)

        # DATA-ONLY guard: neutralize all order-placing methods right away,
        # before any credential exchange, so they can never be called.
        _neutralize_order_methods(obj)

        totp = pyotp.TOTP(totp_secret).now()
        data = obj.generateSession(client_code, password, totp)
        if not data or data.get("status") is False:
            log.warning("ao_portfolio: login failed — %s", data.get("message") if data else "no response")
            return None

        log.info("ao_portfolio: logged in (%s) — DATA-ONLY mode", client_code)

        # ---- positions (read-only) ----
        pos_data = obj.position()
        positions: list[LivePosition] = []
        if pos_data and pos_data.get("status") and pos_data.get("data"):
            for p in (pos_data["data"] or []):
                try:
                    positions.append(LivePosition(
                        symbol=p.get("tradingsymbol", ""),
                        product_type=p.get("producttype", ""),
                        qty=int(p.get("netqty", 0)),
                        avg_price=float(p.get("averageprice", 0)),
                        ltp=float(p.get("ltp", 0)),
                        pnl=float(p.get("unrealised", 0)),
                        day_change_pct=float(p.get("daychangepercentage", 0)),
                    ))
                except Exception as exc:
                    log.debug("ao_portfolio: skipping malformed position row: %s", exc)

        # ---- account / margin summary (read-only) ----
        net_val = available = used = 0.0
        rms_data = obj.rmsLimit()
        if rms_data and rms_data.get("status") and rms_data.get("data"):
            d = rms_data["data"]
            net_val = float(d.get("net", 0))
            available = float(d.get("availablecash", 0))
            used = float(d.get("utilisedamount", 0))

        total_pnl = sum(p.pnl for p in positions)
        return AccountSummary(
            net_value=net_val,
            available_cash=available,
            used_margin=used,
            total_pnl_today=total_pnl,
            positions=positions,
        )

    except Exception as e:
        log.warning("ao_portfolio: fetch failed: %s", e)
        return None


# ---------- internal helpers ----------

_ORDER_METHODS = ("placeOrder", "modifyOrder", "cancelOrder", "placeOrderFullResponse")


def _neutralize_order_methods(smart_api_obj) -> None:
    """Replace SDK order methods with a guard that raises RuntimeError if called.

    Mirrors the pattern in AngelOneHistory._assert_no_order_methods so both
    data clients enforce the same DATA-ONLY contract.
    """
    for name in _ORDER_METHODS:
        if hasattr(smart_api_obj, name):
            setattr(smart_api_obj, name, _refuse_order_call)


def _refuse_order_call(*_args, **_kwargs):
    raise RuntimeError(
        "Angel One credentials in this codebase are configured DATA-ONLY. "
        "Order placement is intentionally disabled. "
        "If you need live order execution, add a separate broker integration "
        "with its own credentials."
    )
