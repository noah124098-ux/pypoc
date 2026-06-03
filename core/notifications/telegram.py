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

    def send_trade_alert(
        self,
        symbol: str,
        side: str,
        strategy: str,
        pnl: float,
        reason: str,
        *,
        regime: Optional[str] = None,
        stop_loss: Optional[float] = None,
        target: Optional[float] = None,
        confidence: Optional[float] = None,
    ) -> bool:
        emoji = "\U0001f7e2" if pnl >= 0 else "\U0001f534"
        pnl_sign = "+" if pnl >= 0 else ""
        lines = [
            f"{emoji} <b>{side} {symbol}</b>",
            f"Strategy: <b>{strategy}</b>",
        ]
        if regime is not None:
            lines.append(f"Regime: <code>{regime}</code>")
        if stop_loss is not None and target is not None:
            lines.append(f"SL: ₹{stop_loss:,.2f}  |  Target: ₹{target:,.2f}")
        elif stop_loss is not None:
            lines.append(f"Stop Loss: ₹{stop_loss:,.2f}")
        elif target is not None:
            lines.append(f"Target: ₹{target:,.2f}")
        if confidence is not None:
            lines.append(f"Confidence: {confidence * 100:.0f}%")
        pnl_str = f"₹{pnl_sign}{pnl:,.0f}"
        lines.append(f"P&amp;L: <b>{pnl_str}</b>")
        lines.append(f"Exit: {reason}")
        return self.send("\n".join(lines))

    def send_daily_summary(self, equity: float, pnl: float, trades: int, regime: str) -> bool:
        emoji = "\U0001f4c8" if pnl >= 0 else "\U0001f4c9"
        msg = f"{emoji} <b>EOD Summary</b>\nEquity: ₹{equity:,.0f}\nDay P&amp;L: ₹{pnl:+,.0f}\nTrades: {trades}\nRegime: {regime}"
        return self.send(msg)

    def send_halt_alert(self, reason: str) -> bool:
        return self.send(f"\U0001f6a8 <b>AGENT HALTED</b>\n{reason}")

    def send_regime_change(self, old_regime: str, new_regime: str, adx: Optional[float] = None, vix: Optional[float] = None) -> bool:
        """Notify when market regime transitions."""
        parts = []
        if adx is not None:
            parts.append(f"ADX={adx:.1f}")
        if vix is not None:
            parts.append(f"VIX={vix:.1f}")
        detail = f" ({', '.join(parts)})" if parts else ""
        msg = f"\U0001f504 <b>Regime Change:</b> <code>{old_regime}</code> → <code>{new_regime}</code>{detail}"
        return self.send(msg)

    def send_gate_refresh(self, sharpe: float, passed: bool) -> bool:
        """Notify after a weekly gate walk-forward run."""
        status = "PASSED" if passed else "FAILED"
        emoji = "✅" if passed else "❌"
        action = "" if passed else " | Run walk-forward to update"
        msg = (
            f"\U0001f4ca <b>Weekly Gate:</b> Sharpe={sharpe:.2f} "
            f"({emoji} {status}){action}"
        )
        return self.send(msg)

    def send_startup(self, mode: str, capital: float) -> bool:
        """Notify when the agent starts up."""
        msg = (
            f"\U0001f680 <b>Agent Started</b>\n"
            f"Mode: <code>{mode}</code>\n"
            f"Capital: ₹{capital:,.0f}"
        )
        return self.send(msg)

    def send_shutdown(self, reason: str) -> bool:
        """Notify when the agent shuts down."""
        msg = f"\U0001f6d1 <b>Agent Stopped</b>\nReason: {reason}"
        return self.send(msg)
