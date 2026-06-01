import smtplib
import ssl
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger("agent.email")


class EmailNotifier:
    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_user: str,
        smtp_password: str,
        email_from: str,
        email_to: str,
    ):
        self._host = smtp_host
        self._port = smtp_port
        self._user = smtp_user
        self._pass = smtp_password
        self._from = email_from
        self._to = email_to
        self._enabled = bool(smtp_host and smtp_user and email_from and email_to)

    def _send(self, subject: str, body_html: str) -> bool:
        if not self._enabled:
            return False
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self._from
            msg["To"] = self._to
            msg.attach(MIMEText(body_html, "html"))
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(self._host, self._port, context=ctx) as s:
                s.login(self._user, self._pass)
                s.sendmail(self._from, self._to, msg.as_string())
            return True
        except Exception as e:
            log.warning("Email send failed: %s", e)
            return False

    def send_eod_report(
        self,
        equity: float,
        pnl: float,
        trades: int,
        review_summary: str = "",
    ) -> bool:
        subject = f"NSE Agent EOD Report — P&L ₹{pnl:+,.0f}"
        body = (
            f"<h2>EOD Summary</h2>"
            f"<p>Equity: ₹{equity:,.0f}<br>"
            f"Day P&L: ₹{pnl:+,.0f}<br>"
            f"Trades: {trades}</p>"
        )
        if review_summary:
            body += f"<h3>AI Review</h3><p>{review_summary}</p>"
        return self._send(subject, body)

    def send_halt_alert(self, reason: str) -> bool:
        return self._send(
            f"\U0001f6a8 Agent Halted — {reason}",
            f"<b>HALT REASON:</b> {reason}",
        )
