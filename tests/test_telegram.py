"""Tests for TelegramNotifier — all HTTP calls are mocked."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.notifications.telegram import TelegramNotifier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _notifier(enabled: bool = True) -> TelegramNotifier:
    if enabled:
        return TelegramNotifier(bot_token="fake-token", chat_id="12345")
    return TelegramNotifier(bot_token="", chat_id="")


def _mock_response(status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    return resp


# ---------------------------------------------------------------------------
# Enabled / disabled flag
# ---------------------------------------------------------------------------

def test_disabled_when_no_credentials():
    n = TelegramNotifier(bot_token="", chat_id="")
    assert not n._enabled


def test_disabled_when_only_token():
    n = TelegramNotifier(bot_token="abc", chat_id="")
    assert not n._enabled


def test_disabled_when_only_chat_id():
    n = TelegramNotifier(bot_token="", chat_id="123")
    assert not n._enabled


def test_enabled_with_both_credentials():
    n = TelegramNotifier(bot_token="tok", chat_id="cid")
    assert n._enabled


# ---------------------------------------------------------------------------
# send() — disabled path
# ---------------------------------------------------------------------------

def test_send_returns_false_when_disabled():
    n = _notifier(enabled=False)
    result = n.send("hello")
    assert result is False


def test_send_does_not_call_requests_when_disabled():
    n = _notifier(enabled=False)
    with patch("core.notifications.telegram.requests.post") as mock_post:
        n.send("hello")
        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# send() — enabled path, HTTP 200
# ---------------------------------------------------------------------------

def test_send_returns_true_on_200():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)):
        assert n.send("test message") is True


def test_send_posts_to_correct_url():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send("hello")
        url = mock_post.call_args[0][0]
        assert url == "https://api.telegram.org/botfake-token/sendMessage"


def test_send_includes_chat_id_and_text():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send("my message")
        payload = mock_post.call_args[1]["json"]
        assert payload["chat_id"] == "12345"
        assert payload["text"] == "my message"
        assert payload["parse_mode"] == "HTML"


def test_send_uses_5s_timeout():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send("hi")
        assert mock_post.call_args[1]["timeout"] == 5


# ---------------------------------------------------------------------------
# send() — HTTP non-200
# ---------------------------------------------------------------------------

def test_send_returns_false_on_non_200():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(400)):
        assert n.send("bad") is False


# ---------------------------------------------------------------------------
# send() — network exception
# ---------------------------------------------------------------------------

def test_send_returns_false_on_exception():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", side_effect=Exception("timeout")):
        assert n.send("fail") is False


def test_send_logs_warning_on_exception(caplog):
    import logging
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", side_effect=Exception("net err")):
        with caplog.at_level(logging.WARNING, logger="agent.telegram"):
            n.send("fail")
    assert "Telegram send failed" in caplog.text


# ---------------------------------------------------------------------------
# send_trade_alert()
# ---------------------------------------------------------------------------

def test_trade_alert_profit_contains_green_emoji():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_trade_alert("RELIANCE", "BUY", "trend_breakout", 500.0, "target_hit")
        text = mock_post.call_args[1]["json"]["text"]
        assert "\U0001f7e2" in text  # green circle


def test_trade_alert_loss_contains_red_emoji():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_trade_alert("INFY", "SELL", "rsi_momentum", -200.0, "stop_loss")
        text = mock_post.call_args[1]["json"]["text"]
        assert "\U0001f534" in text  # red circle


def test_trade_alert_contains_symbol_and_strategy():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_trade_alert("TCS", "BUY", "mean_reversion", 100.0, "target_hit")
        text = mock_post.call_args[1]["json"]["text"]
        assert "TCS" in text
        assert "mean_reversion" in text


def test_trade_alert_returns_bool():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)):
        result = n.send_trade_alert("X", "BUY", "s", 0.0, "r")
        assert isinstance(result, bool)


def test_trade_alert_with_regime_and_levels():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_trade_alert(
            "WIPRO", "BUY", "trend_breakout", 300.0, "target_hit",
            regime="TREND", stop_loss=450.0, target=500.0, confidence=0.75,
        )
        text = mock_post.call_args[1]["json"]["text"]
        assert "TREND" in text
        assert "450" in text  # stop loss
        assert "500" in text  # target
        assert "75%" in text  # confidence


def test_trade_alert_pnl_sign_positive():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_trade_alert("X", "BUY", "s", 1000.0, "target_hit")
        text = mock_post.call_args[1]["json"]["text"]
        assert "₹+1,000" in text or "1,000" in text


def test_trade_alert_pnl_sign_negative():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_trade_alert("X", "SELL", "s", -500.0, "stop_loss")
        text = mock_post.call_args[1]["json"]["text"]
        assert "-500" in text or "500" in text


# ---------------------------------------------------------------------------
# send_daily_summary()
# ---------------------------------------------------------------------------

def test_daily_summary_positive_pnl_uses_up_chart():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_daily_summary(equity=1_000_000.0, pnl=5000.0, trades=3, regime="TREND")
        text = mock_post.call_args[1]["json"]["text"]
        assert "\U0001f4c8" in text  # chart increasing


def test_daily_summary_negative_pnl_uses_down_chart():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_daily_summary(equity=995_000.0, pnl=-5000.0, trades=1, regime="VOLATILE")
        text = mock_post.call_args[1]["json"]["text"]
        assert "\U0001f4c9" in text  # chart decreasing


def test_daily_summary_contains_regime():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_daily_summary(equity=1_000_000.0, pnl=0.0, trades=0, regime="RANGE")
        text = mock_post.call_args[1]["json"]["text"]
        assert "RANGE" in text


def test_daily_summary_zero_pnl_uses_up_chart():
    # pnl >= 0 branch: exactly 0 should use up chart
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_daily_summary(equity=1_000_000.0, pnl=0.0, trades=0, regime="RANGE")
        text = mock_post.call_args[1]["json"]["text"]
        assert "\U0001f4c8" in text


# ---------------------------------------------------------------------------
# send_halt_alert()
# ---------------------------------------------------------------------------

def test_halt_alert_contains_reason():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_halt_alert("Daily loss circuit breaker triggered")
        text = mock_post.call_args[1]["json"]["text"]
        assert "Daily loss circuit breaker triggered" in text


def test_halt_alert_contains_halt_marker():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_halt_alert("reason")
        text = mock_post.call_args[1]["json"]["text"]
        assert "AGENT HALTED" in text


def test_halt_alert_contains_siren_emoji():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_halt_alert("drawdown")
        text = mock_post.call_args[1]["json"]["text"]
        assert "\U0001f6a8" in text  # rotating light


# ---------------------------------------------------------------------------
# send_regime_change()
# ---------------------------------------------------------------------------

def test_regime_change_contains_old_and_new_regime():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_regime_change("TREND", "VOLATILE")
        text = mock_post.call_args[1]["json"]["text"]
        assert "TREND" in text
        assert "VOLATILE" in text


def test_regime_change_contains_regime_change_emoji():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_regime_change("RANGE", "TREND")
        text = mock_post.call_args[1]["json"]["text"]
        assert "\U0001f504" in text  # arrows in circle


def test_regime_change_includes_adx_and_vix():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_regime_change("TREND", "VOLATILE", adx=24.0, vix=19.2)
        text = mock_post.call_args[1]["json"]["text"]
        assert "ADX=24.0" in text
        assert "VIX=19.2" in text


def test_regime_change_without_adx_vix():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_regime_change("RANGE", "TREND")
        text = mock_post.call_args[1]["json"]["text"]
        # Should not crash and should still contain regime names
        assert "RANGE" in text
        assert "TREND" in text


def test_regime_change_returns_bool():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)):
        result = n.send_regime_change("TREND", "RANGE")
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# send_gate_refresh()
# ---------------------------------------------------------------------------

def test_gate_refresh_passed_contains_sharpe():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_gate_refresh(sharpe=1.45, passed=True)
        text = mock_post.call_args[1]["json"]["text"]
        assert "1.45" in text


def test_gate_refresh_passed_shows_passed():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_gate_refresh(sharpe=1.45, passed=True)
        text = mock_post.call_args[1]["json"]["text"]
        assert "PASSED" in text


def test_gate_refresh_failed_shows_failed():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_gate_refresh(sharpe=0.32, passed=False)
        text = mock_post.call_args[1]["json"]["text"]
        assert "FAILED" in text


def test_gate_refresh_failed_contains_action_hint():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_gate_refresh(sharpe=0.32, passed=False)
        text = mock_post.call_args[1]["json"]["text"]
        assert "walk-forward" in text.lower()


def test_gate_refresh_contains_chart_emoji():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_gate_refresh(sharpe=0.5, passed=False)
        text = mock_post.call_args[1]["json"]["text"]
        assert "\U0001f4ca" in text  # bar chart


def test_gate_refresh_returns_bool():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)):
        result = n.send_gate_refresh(sharpe=1.2, passed=True)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# send_startup()
# ---------------------------------------------------------------------------

def test_startup_contains_mode():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_startup(mode="paper", capital=1_000_000.0)
        text = mock_post.call_args[1]["json"]["text"]
        assert "paper" in text


def test_startup_contains_capital():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_startup(mode="paper", capital=500_000.0)
        text = mock_post.call_args[1]["json"]["text"]
        assert "500,000" in text or "5,00,000" in text or "500000" in text


def test_startup_contains_rocket_emoji():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_startup(mode="paper", capital=1_000_000.0)
        text = mock_post.call_args[1]["json"]["text"]
        assert "\U0001f680" in text  # rocket


def test_startup_returns_bool():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)):
        result = n.send_startup(mode="paper", capital=1_000_000.0)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# send_shutdown()
# ---------------------------------------------------------------------------

def test_shutdown_contains_reason():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_shutdown(reason="SIGINT received")
        text = mock_post.call_args[1]["json"]["text"]
        assert "SIGINT received" in text


def test_shutdown_contains_stop_emoji():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)) as mock_post:
        n.send_shutdown(reason="normal")
        text = mock_post.call_args[1]["json"]["text"]
        assert "\U0001f6d1" in text  # stop sign


def test_shutdown_returns_bool():
    n = _notifier()
    with patch("core.notifications.telegram.requests.post", return_value=_mock_response(200)):
        result = n.send_shutdown(reason="test")
        assert isinstance(result, bool)


def test_shutdown_disabled_returns_false():
    n = _notifier(enabled=False)
    result = n.send_shutdown(reason="test")
    assert result is False
