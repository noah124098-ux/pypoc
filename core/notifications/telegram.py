import logging
import requests
from typing import Optional

log = logging.getLogger("agent.telegram")


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self._token = bot_token
        self._chat_id = chat_id
        self._enabled = bool(bot_token and chat_id)

    def send(self, message: str) -> bool:
        if not self._enabled:
            return False
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                json={"chat_id": self._chat_id, "text": message, "parse_mode": "HTML"},
                timeout=5,
            )
            return r.status_code == 200
        except Exception as e:
            log.warning("Telegram send failed: %s", e)
            return False

    def send_trade_alert(self, symbol: str, side: str, strategy: str, pnl: float, reason: str) -> bool:
        emoji = "\U0001f7e2" if pnl > 0 else "\U0001f534"
        msg = f"{emoji} <b>{side} {symbol}</b>\nStrategy: {strategy}\nP&L: ₹{pnl:,.0f}\nExit: {reason}"
        return self.send(msg)

    def send_daily_summary(self, equity: float, pnl: float, trades: int, regime: str) -> bool:
        emoji = "\U0001f4c8" if pnl >= 0 else "\U0001f4c9"
        msg = f"{emoji} <b>EOD Summary</b>\nEquity: ₹{equity:,.0f}\nDay P&L: ₹{pnl:+,.0f}\nTrades: {trades}\nRegime: {regime}"
        return self.send(msg)

    def send_halt_alert(self, reason: str) -> bool:
        return self.send(f"\U0001f6a8 <b>AGENT HALTED</b>\n{reason}")
