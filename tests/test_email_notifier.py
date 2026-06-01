"""Tests for EmailNotifier using mocked smtplib.SMTP_SSL."""
import smtplib
from unittest.mock import MagicMock, patch

import pytest

from core.notifications.email_notifier import EmailNotifier


SMTP_KWARGS = dict(
    smtp_host="smtp.example.com",
    smtp_port=465,
    smtp_user="user@example.com",
    smtp_password="secret",
    email_from="user@example.com",
    email_to="recipient@example.com",
)


def _make_notifier(**overrides):
    kwargs = {**SMTP_KWARGS, **overrides}
    return EmailNotifier(**kwargs)


class TestEmailNotifierEnabled:
    def test_enabled_when_all_fields_set(self):
        n = _make_notifier()
        assert n._enabled is True

    def test_disabled_when_host_empty(self):
        n = _make_notifier(smtp_host="")
        assert n._enabled is False

    def test_disabled_when_user_empty(self):
        n = _make_notifier(smtp_user="")
        assert n._enabled is False

    def test_disabled_when_from_empty(self):
        n = _make_notifier(email_from="")
        assert n._enabled is False

    def test_disabled_when_to_empty(self):
        n = _make_notifier(email_to="")
        assert n._enabled is False


class TestSendWhenDisabled:
    def test_send_eod_report_returns_false_when_disabled(self):
        n = _make_notifier(smtp_host="")
        result = n.send_eod_report(equity=100000, pnl=500, trades=3)
        assert result is False

    def test_send_halt_alert_returns_false_when_disabled(self):
        n = _make_notifier(smtp_host="")
        result = n.send_halt_alert("daily loss circuit")
        assert result is False


class TestSendEodReport:
    @patch("core.notifications.email_notifier.smtplib.SMTP_SSL")
    def test_sends_and_returns_true(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        n = _make_notifier()
        result = n.send_eod_report(equity=500000, pnl=1500, trades=5)

        assert result is True
        mock_server.login.assert_called_once_with("user@example.com", "secret")
        mock_server.sendmail.assert_called_once()
        call_args = mock_server.sendmail.call_args[0]
        assert call_args[0] == "user@example.com"
        assert call_args[1] == "recipient@example.com"

    @patch("core.notifications.email_notifier.smtplib.SMTP_SSL")
    def test_subject_contains_pnl(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        n = _make_notifier()
        n.send_eod_report(equity=500000, pnl=-2000, trades=2)

        raw_msg = mock_server.sendmail.call_args[0][2]
        # Decode the full MIME message so base64-encoded parts are readable
        import email, quopri, base64 as _b64
        parsed = email.message_from_string(raw_msg)
        body_text = ""
        for part in parsed.walk():
            payload = part.get_payload(decode=True)
            if payload:
                body_text += payload.decode("utf-8", errors="replace")
        subject_header = parsed["Subject"]
        # Subject header may be RFC2047-encoded; decode it
        from email.header import decode_header
        decoded_parts = decode_header(subject_header)
        subject_str = "".join(
            (p.decode(enc or "utf-8") if isinstance(p, bytes) else p)
            for p, enc in decoded_parts
        )
        assert "EOD Report" in subject_str
        assert "-2,000" in body_text

    @patch("core.notifications.email_notifier.smtplib.SMTP_SSL")
    def test_review_summary_included_when_provided(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        n = _make_notifier()
        n.send_eod_report(equity=500000, pnl=100, trades=1, review_summary="Looks good.")

        import email
        raw_msg = mock_server.sendmail.call_args[0][2]
        parsed = email.message_from_string(raw_msg)
        body_text = ""
        for part in parsed.walk():
            payload = part.get_payload(decode=True)
            if payload:
                body_text += payload.decode("utf-8", errors="replace")
        assert "AI Review" in body_text
        assert "Looks good." in body_text

    @patch("core.notifications.email_notifier.smtplib.SMTP_SSL")
    def test_review_summary_omitted_when_empty(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        n = _make_notifier()
        n.send_eod_report(equity=500000, pnl=100, trades=1)

        import email
        raw_msg = mock_server.sendmail.call_args[0][2]
        parsed = email.message_from_string(raw_msg)
        body_text = ""
        for part in parsed.walk():
            payload = part.get_payload(decode=True)
            if payload:
                body_text += payload.decode("utf-8", errors="replace")
        assert "AI Review" not in body_text

    @patch("core.notifications.email_notifier.smtplib.SMTP_SSL")
    def test_returns_false_on_smtp_exception(self, mock_smtp_cls):
        mock_smtp_cls.side_effect = ConnectionRefusedError("refused")

        n = _make_notifier()
        result = n.send_eod_report(equity=500000, pnl=100, trades=1)

        assert result is False


class TestSendHaltAlert:
    @patch("core.notifications.email_notifier.smtplib.SMTP_SSL")
    def test_sends_halt_and_returns_true(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        n = _make_notifier()
        result = n.send_halt_alert("drawdown circuit breaker")

        assert result is True
        mock_server.sendmail.assert_called_once()
        raw_msg = mock_server.sendmail.call_args[0][2]
        assert "drawdown circuit breaker" in raw_msg
        assert "HALT" in raw_msg

    @patch("core.notifications.email_notifier.smtplib.SMTP_SSL")
    def test_returns_false_on_login_failure(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad creds")
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        n = _make_notifier()
        result = n.send_halt_alert("test halt")

        assert result is False


class TestSendUsesCorrectSmtpSettings:
    @patch("core.notifications.email_notifier.ssl.create_default_context")
    @patch("core.notifications.email_notifier.smtplib.SMTP_SSL")
    def test_smtp_ssl_called_with_host_and_port(self, mock_smtp_cls, mock_ssl_ctx):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ssl_ctx.return_value = MagicMock()

        n = _make_notifier(smtp_host="mail.myisp.com", smtp_port=465)
        n.send_halt_alert("test")

        mock_smtp_cls.assert_called_once_with(
            "mail.myisp.com", 465, context=mock_ssl_ctx.return_value
        )
