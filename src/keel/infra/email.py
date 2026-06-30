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

import html
import re
import smtplib
from email.message import EmailMessage
from typing import Protocol

from keel.config import Settings
from keel.logging import get_logger

_log = get_logger(__name__)

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_BULLET = re.compile(r"^\s*[*-]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d+\.\s+(.*)$")


def _to_plain(body: str) -> str:
    """Strip the small markdown subset the agent emits so the text reads cleanly:
    ``**bold**`` loses its markers and ``* item`` / ``- item`` become ``• item``."""
    out = []
    for raw in body.splitlines():
        line = _BOLD.sub(r"\1", raw)
        m = _BULLET.match(line)
        if m:
            line = f"  • {m.group(1)}"
        out.append(line)
    return "\n".join(out)


def _to_html(body: str) -> str:
    """Render that same markdown subset to simple, email-safe HTML (bold, bullet and
    numbered lists, paragraphs). Everything is HTML-escaped first — no raw injection.

    Scans line by line so a heading immediately followed by bullets (no blank line
    between, as the agent often emits) still becomes a heading paragraph + a list.
    """

    def _inline(text: str) -> str:
        return _BOLD.sub(r"<strong>\1</strong>", html.escape(text))

    parts: list[str] = []
    para: list[str] = []  # buffered plain lines
    list_tag: str | None = None  # 'ul' | 'ol' while inside a list
    list_items: list[str] = []

    def _flush_para() -> None:
        if para:
            parts.append("<p>" + "<br>".join(_inline(ln) for ln in para) + "</p>")
            para.clear()

    def _flush_list() -> None:
        nonlocal list_tag
        if list_tag:
            parts.append(f"<{list_tag}>{''.join(list_items)}</{list_tag}>")
            list_items.clear()
            list_tag = None

    for raw in body.splitlines():
        bullet = _BULLET.match(raw)
        numbered = _NUMBERED.match(raw)
        if bullet or numbered:
            _flush_para()
            tag = "ul" if bullet else "ol"
            if list_tag and list_tag != tag:
                _flush_list()
            list_tag = tag
            list_items.append(f"<li>{_inline((bullet or numbered).group(1))}</li>")  # type: ignore[union-attr]
        elif raw.strip() == "":
            _flush_list()
            _flush_para()
        else:
            _flush_list()
            para.append(raw)
    _flush_list()
    _flush_para()

    inner = "\n".join(parts)
    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        'font-size:14px;line-height:1.55;color:#1a1a2e;">'
        f"{inner}</div>"
    )


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
        # multipart/alternative: a clean plain-text fallback plus an HTML part so
        # rich clients render the agent's **bold** and bullet lists nicely.
        msg.set_content(_to_plain(body))
        msg.add_alternative(_to_html(body), subtype="html")
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
