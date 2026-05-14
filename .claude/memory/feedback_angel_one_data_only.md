---
name: feedback-angel-one-data-only
description: Angel One credentials in this project are DATA-ONLY — never use them to place, modify, or cancel orders
metadata:
  type: feedback
---

The Angel One SmartAPI credentials in `.env` are to be used EXCLUSIVELY for market data: candle history (`getCandleData`), instrument master, and live tick WebSocket feed. They MUST NEVER be used for placing, modifying, or cancelling orders.

**Why:** User explicitly requested this split (2026-05-13). Even though their account credentials would technically allow trading, the design separates "what we read" from "what we execute" so a code regression cannot accidentally place real orders. Order execution stays in `PaperBroker` (in-memory) until a deliberate live broker integration with its own separate credentials is added in a future phase.

The constraint was set after the user accidentally pasted SmartAPI credentials in chat (api_key `DynzW489`, secret `6244516a-...`). The exposed key was neutralized via IP whitelist change to `1.1.1.1` on the offending app. New apps for production use must be created separately.

**How to apply:**
- The data-only contract is enforced in code. Both `core/data/angelone_history.py` and `core/data/angelone_feed.py` neutralize `placeOrder`/`modifyOrder`/`cancelOrder`/`placeOrderFullResponse` on the SmartConnect SDK object immediately after login. Any attempt to call them raises `RuntimeError("Angel One credentials in this codebase are configured DATA-ONLY")`.
- Test `test_order_methods_are_neutralized_after_login` in `tests/test_angelone_history.py` guards this contract. CI must always run it.
- If a future phase needs live order execution: do NOT remove these guards. Instead add a separate broker module (e.g. `core/broker/zerodha.py` or a second Angel One app with separate credentials) and route orders through that. The guards on the data-feed object stay in place.
- Never log the credential values. Never echo them back to the user.

See [[project-nse-trading-agent]].
