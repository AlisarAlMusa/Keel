"""Pluggable email sender for outbox notifications (G4).

The outbox row is the delivery guarantee; this module is only the *transport*.
Two backends:

  * ``LoggingEmailSender`` (default) — logs the intent, sends nothing. This is the
    safe demo behaviour: no real mail leaves the box.
  * ``SMTPEmailSender`` — sends via ``smtplib`` when ``keel_smtp_enabled`` is set
    and a host is configured.

``get_email_sender(settings)`` picks the backend from config, so enabling real
delivery is a config change, not a code change. The worker calls ``send()``; a
transport failure raises so RQ retries (the outbox stays unprocessed until a send
succeeds — at-least-once, no dual-write).
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Protocol

from keel.config import Settings
from keel.logging import get_logger

_log = get_logger(__name__)


class EmailSender(Protocol):
    def send(self, *, to: str | None, subject: str, body: str) -> None: ...


class LoggingEmailSender:
    """Simulation backend: record the (simulated) send, no real mail leaves the box.

    This is the demo default — email is ON, but with no real SMTP configured the
    send is simulated and logged (recipient + body preview) so you can SEE that a
    Keel action produced a notification without sending real mail.
    """

    def send(self, *, to: str | None, subject: str, body: str) -> None:
        _log.info(
            "email.simulated_send",
            to=to or "<unresolved>",
            subject=subject,
            body_preview=body[:80],
        )


class SMTPEmailSender:
    """Real SMTP transport. Only constructed when SMTP is enabled in config."""

    def __init__(self, settings: Settings) -> None:
        self._host = settings.keel_smtp_host
        self._port = settings.keel_smtp_port
        self._user = settings.keel_smtp_user
        self._password = settings.keel_smtp_password
        self._starttls = settings.keel_smtp_starttls
        self._from = settings.keel_email_from

    def send(self, *, to: str | None, subject: str, body: str) -> None:
        if not to:
            # Honest failure rather than a silent no-op: with SMTP enabled, a
            # missing recipient is a wiring gap (populate the outbox payload's
            # 'email', or resolve it before send), not something to swallow.
            _log.warning("email.no_recipient", subject=subject)
            return
        msg = EmailMessage()
        msg["From"] = self._from
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(self._host, self._port, timeout=10) as smtp:
            if self._starttls:
                smtp.starttls()
            if self._user:
                smtp.login(self._user, self._password)
            smtp.send_message(msg)
        _log.info("email.sent", to=to, subject=subject)


def get_email_sender(settings: Settings) -> EmailSender:
    """Return the configured sender — SMTP when enabled+hostful, else logging."""
    if settings.keel_smtp_enabled and settings.keel_smtp_host:
        return SMTPEmailSender(settings)
    return LoggingEmailSender()
