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
