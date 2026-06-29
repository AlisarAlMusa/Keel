"""Phase 3 RQ jobs and scheduled worker handlers.

One worker process hosts all handlers (spec §4):
  - outbox_publisher_job     : poll unprocessed outbox rows → enqueue email RQ jobs.
  - send_outbox_event_job    : send one email (called by publisher; retries via RQ).
  - capacity_sync_job        : seat-fill loop — pick waitlist #1 on open seat.
  - expiry_sweep_job         : expire stale pending actions after TTL.

Outbox + RQ composed (spec §4 "Outbox + RQ — two problems, composed"):
  Outbox = owed-work ledger (consistency, no dual-write).
  RQ     = execution engine (retry, backoff, concurrency, isolation).
  Publisher only enqueues. processed=True set AFTER success. Consumer dedupes on outbox.id.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa

from keel.config import get_settings
from keel.infra.database import engine as db_infra
from keel.infra.email import get_email_sender
from keel.infra.vault import VaultConfig, load_secrets
from keel.logging import get_logger

_log = get_logger(__name__)

_ACTION_TTL_HOURS = 24  # pending actions expire after this
_MAX_OUTBOX_ATTEMPTS = 5


# ---------------------------------------------------------------------------
# Shared bootstrap (each job is sync — sets up its own event loop)
# ---------------------------------------------------------------------------


def _bootstrap() -> tuple[Any, str]:
    """Load secrets + DSN.  Returns (settings, dsn)."""
    settings = get_settings()
    vault_cfg = VaultConfig(
        addr=settings.vault_addr,
        token=settings.vault_token,
        kv_mount=settings.vault_kv_mount,
        secret_path=settings.vault_secret_path,
    )
    secrets = load_secrets(vault_cfg)
    dsn = settings.database_url.replace(":placeholder@", f":{secrets['db_password']}@", 1)
    return settings, dsn


async def _active_tenant_ids(session_factory: Any) -> list[UUID]:
    """Enumerate active tenants from the non-RLS ``tenants`` table.

    The app role (``keel_app``) is NOBYPASSRLS and every tenant-owned table is
    FORCE ROW LEVEL SECURITY, so a worker scan that does NOT set ``app.tenant_id``
    matches the policy ``tenant_id = NULL`` and returns ZERO rows. Cross-tenant
    background jobs must therefore enumerate tenants here (``tenants`` carries no
    RLS) and then process each tenant's rows inside a ``tenant_session`` so RLS is
    satisfied. This is the fix for the silently-dead worker tier.
    """
    async with session_factory() as session:
        rows = await session.execute(
            sa.text("SELECT id FROM tenants WHERE status = 'active' ORDER BY created_at")
        )
        return [UUID(str(r[0])) for r in rows.fetchall()]


# ---------------------------------------------------------------------------
# Outbox publisher — poll → enqueue RQ email jobs
# ---------------------------------------------------------------------------


def outbox_publisher_job() -> dict[str, int]:
    """Sync RQ scheduled job: poll unprocessed outbox rows → enqueue email jobs.

    processed=True is set by send_outbox_event_job AFTER success.
    This job only enqueues — never sets processed itself.
    At-least-once: consumer (send_outbox_event_job) dedupes on outbox.id.
    """
    from redis import Redis
    from rq import Queue

    settings, dsn = _bootstrap()

    async def _run() -> dict[str, int]:
        from keel.infra.database.session import tenant_session

        engine = db_infra.create_engine(dsn)
        session_factory = db_infra.create_session_factory(engine)
        enqueued = 0
        try:
            # Enumerate tenants (non-RLS table), then scan each tenant's outbox
            # under its RLS context — an unscoped scan returns zero rows.
            outbox_rows: list[dict[str, Any]] = []
            for tid in await _active_tenant_ids(session_factory):
                async with tenant_session(session_factory, tid) as session:
                    rows = await session.execute(
                        sa.text(
                            "SELECT id, tenant_id, event_type, kind, payload, attempts "
                            "FROM outbox "
                            "WHERE processed = false AND attempts < :max_attempts "
                            "ORDER BY created_at "
                            "LIMIT 100"
                        ),
                        {"max_attempts": _MAX_OUTBOX_ATTEMPTS},
                    )
                    outbox_rows.extend(dict(r) for r in rows.mappings().all())

            redis_conn = Redis.from_url(settings.redis_url)
            q = Queue("keel", connection=redis_conn)

            for row in outbox_rows:
                q.enqueue(
                    send_outbox_event_job,
                    kwargs={
                        "outbox_id": str(row["id"]),
                        "event_type": str(row["event_type"] or row.get("kind") or "unknown"),
                        "payload": dict(row["payload"]),
                        "tenant_id_str": str(row["tenant_id"]),
                    },
                )
                enqueued += 1

        finally:
            await engine.dispose()

        return {"enqueued": enqueued}

    result = asyncio.run(_run())
    _log.info("worker.outbox_publisher.done", **result)
    return result


# ---------------------------------------------------------------------------
# Send one outbox event (email) — called by publisher; dedupes on outbox.id
# ---------------------------------------------------------------------------


def send_outbox_event_job(
    *,
    outbox_id: str,
    event_type: str,
    payload: dict[str, Any],
    tenant_id_str: str,
) -> dict[str, str]:
    """Send one outbox event (email stub) and mark processed=True on success.

    Idempotent: if already processed, skip.
    Structured logging — PII redacted (student_id logged, email address not).
    """
    settings, dsn = _bootstrap()
    tenant_uuid = UUID(tenant_id_str)

    async def _run() -> dict[str, str]:
        from keel.infra.database.session import tenant_session

        engine = db_infra.create_engine(dsn)
        session_factory = db_infra.create_session_factory(engine)
        try:
            # tenant_session sets app.tenant_id so RLS lets us see the outbox row.
            async with tenant_session(session_factory, tenant_uuid) as session:
                # Dedup: if already processed, skip.
                row = await session.execute(
                    sa.text("SELECT processed FROM outbox WHERE id = :oid"),
                    {"oid": outbox_id},
                )
                r = row.mappings().first()
                if not r or r["processed"]:
                    return {"status": "already_processed", "outbox_id": outbox_id}

                # Increment attempt counter (committed on context-manager exit).
                await session.execute(
                    sa.text("UPDATE outbox SET attempts = attempts + 1 WHERE id = :oid"),
                    {"oid": outbox_id},
                )

            # Perform the side effect (email send).
            _send_email(event_type=event_type, payload=payload)

            # Mark processed AFTER success (never at enqueue time).
            async with tenant_session(session_factory, tenant_uuid) as session:
                await session.execute(
                    sa.text(
                        "UPDATE outbox SET processed = true, published_at = :now WHERE id = :oid"
                    ),
                    {"oid": outbox_id, "now": datetime.now(UTC)},
                )

        finally:
            await engine.dispose()

        return {"status": "sent", "outbox_id": outbox_id, "event_type": event_type}

    result = asyncio.run(_run())
    _log.info(
        "worker.send_outbox_event.done",
        outbox_id=outbox_id,
        event_type=event_type,
        status=result.get("status"),
    )
    return result


# Keel-originated notifications that warrant an email. SIS-domain events the portal
# writes to the outbox — registrar decisions ('request.approved' / 'request.rejected')
# — are deliberately NOT here: that outcome is the university's (SIS) to communicate,
# not Keel's, so Keel never emails for them.
_KEEL_EMAIL_EVENTS: frozenset[str] = frozenset(
    {
        "enrollment_confirmation",
        "waitlist_joined",
        "waitlist_left",
        "seat_open_notify",
        "seat_filled_confirmation",
        "seat_fill_failed",
        "graduation_application",
        "major_change",
        "petition",
        "escalation_email",
    }
)


def _send_email(*, event_type: str, payload: dict[str, Any]) -> None:
    """Build the notification body and dispatch it via the configured sender (G4).

    Only Keel-originated actions email (``_KEEL_EMAIL_EVENTS``); SIS events such as
    registrar approve/reject are skipped here (the outbox row is still marked
    processed by the caller — Keel just doesn't notify for a non-Keel action).

    The transport is pluggable: a logging sender (simulation — logs, sends nothing)
    or a real SMTP sender when ``keel_smtp_enabled`` is set. The outbox row is the
    delivery guarantee — no dual-write. A transport failure propagates so RQ retries.
    """
    settings = get_settings()
    student_id = payload.get("student_id", "unknown")

    # Gate 1: only Keel actions email. SIS / unknown events → no notification.
    if event_type not in _KEEL_EMAIL_EVENTS:
        _log.info("worker.email.skipped_non_keel", event_type=event_type)
        return

    # Gate 2: master switch.
    if not settings.keel_email_enabled:
        _log.info("worker.email.disabled", event_type=event_type)
        return

    # Human-readable details (added to the payload at emit time by the worker /
    # action services). Fall back gracefully when an older payload lacks them.
    req_type = str(payload.get("type", "request"))
    name = str(payload.get("student_name") or "there")
    course_code = str(payload.get("course_code") or "your course")
    course_name = str(payload.get("course_name") or "")
    course = f"{course_code} — {course_name}" if course_name else course_code
    instructor = str(payload.get("instructor") or "the instructor")
    when = str(payload.get("when") or "")
    sec_desc = (
        f"the {when} section with {instructor}"
        if when
        else (f"the section with {instructor}" if instructor else "your section")
    )
    reason = str(payload.get("reason", "your eligibility changed"))

    # A registration confirmation lists EVERY section enrolled in one email (payload
    # carries a "sections" list). Each line is "CODE — Name: the <when> section with <instr>".
    sections = payload.get("sections")
    if isinstance(sections, list) and sections:
        lines = []
        for s in sections:
            code = str(s.get("course_code") or "your course")
            cname = str(s.get("course_name") or "")
            head = f"{code} — {cname}" if cname else code
            s_instr = str(s.get("instructor") or "the instructor")
            s_when = str(s.get("when") or "")
            desc = (
                f"the {s_when} section with {s_instr}"
                if s_when
                else f"the section with {s_instr}"
            )
            lines.append(f"  • {head}: {desc}")
        enrollment_body = (
            f"Hi {name},\n\nYou're enrolled in {len(sections)} course"
            f"{'' if len(sections) == 1 else 's'}:\n"
            + "\n".join(lines)
            + "\n\nThey're on your schedule now.\n\n— Keel"
        )
    else:
        enrollment_body = (
            f"Hi {name},\n\nYou're enrolled in {course} ({sec_desc}). "
            "It's on your schedule now.\n\n— Keel"
        )

    _TEMPLATES = {
        "enrollment_confirmation": enrollment_body,
        "waitlist_joined": (
            f"Hi {name},\n\nYou've joined the waitlist for {course} — {sec_desc} "
            f"(position {payload.get('position', '—')}). We'll let you know the moment a "
            "seat opens.\n\n— Keel"
        ),
        "seat_open_notify": (
            f"Hi {name},\n\nGreat news — a seat just opened in {course}: {sec_desc}. "
            "You can grab it now; just ask me in the chat to register you for it before "
            "it fills again.\n\n— Keel"
        ),
        "seat_filled_confirmation": (
            f"Hi {name},\n\nA seat opened in {course} and we automatically enrolled you in "
            f"{sec_desc}, as you asked. It's on your schedule now.\n\n— Keel"
        ),
        "seat_fill_failed": (
            f"Hi {name},\n\nA seat opened in {course}, but we couldn't auto-enroll you "
            f"because {reason}. Please reach out if you'd like help.\n\n— Keel"
        ),
        "waitlist_left": (
            f"Hi {name},\n\nYou've been removed from the waitlist for {course}.\n\n— Keel"
        ),
        "graduation_application": (
            f"Hi {name},\n\nYour graduation application was submitted via Keel.\n\n— Keel"
        ),
        "major_change": (
            f"Hi {name},\n\nYour major-change request was submitted via Keel.\n\n— Keel"
        ),
        "petition": (
            f"Hi {name},\n\nYour {req_type} petition was submitted via Keel.\n\n— Keel"
        ),
        "escalation_email": (
            f"Hi {name},\n\nYour request was escalated to a human advisor via Keel. "
            "Someone will follow up with you.\n\n— Keel"
        ),
    }
    body = _TEMPLATES.get(event_type, f"Event: {event_type}")

    # Simulation: address every Keel email to the configured demo inbox (we have no
    # real per-student mailboxes). Falls back to the payload's address if unset.
    to_email = settings.keel_email_simulate_to or payload.get("email")

    sender = get_email_sender(settings)
    sender.send(to=to_email, subject=f"Keel: {event_type}", body=body)

    _log.info(
        "worker.email.dispatched",
        event_type=event_type,
        student_id=student_id,
        to=to_email,
        sender=type(sender).__name__,
    )


# ---------------------------------------------------------------------------
# Capacity sync + seat-fill
# ---------------------------------------------------------------------------


def capacity_sync_job() -> dict[str, int]:
    """Scheduled job: find sections where enrolled < capacity and fill from waitlist.

    For each under-capacity section:
      - Take waitlist student #1 (lowest position with status='waiting').
      - If auto_enroll=True: re-run engine verifier → eligible → enroll in one TX;
        not eligible → mark failed + notify, advance to #2.
      - If auto_enroll=False: emit seat_open_notify only.

    Re-verification is mandatory (spec §3.1) — eligibility may have changed since
    the student approved.  Never breaks the user-facing response path.
    """
    settings, dsn = _bootstrap()

    async def _run() -> dict[str, int]:
        engine = db_infra.create_engine(dsn)
        session_factory = db_infra.create_session_factory(engine)
        filled = 0
        notified = 0
        failed = 0

        try:
            from keel.infra.database.session import tenant_session
            from keel.services.actions import notify_context, outbox_write, section_label

            # Gather seat-fill candidates per tenant (RLS-scoped scans). Each item
            # is (tenant_id, section_id, waitlist_id, student_id, auto_enroll, already_notified).
            candidates: list[tuple[UUID, str, UUID, UUID, bool, bool]] = []
            for tenant_id in await _active_tenant_ids(session_factory):
                async with tenant_session(session_factory, tenant_id) as session:
                    sections = await session.execute(
                        sa.text(
                            "SELECT id FROM sections WHERE enrolled < capacity LIMIT 200"
                        )
                    )
                    section_ids = [str(r[0]) for r in sections.fetchall()]
                    for section_id in section_ids:
                        wl = await session.execute(
                            sa.text(
                                "SELECT id, student_id, auto_enroll, notified_at FROM waitlist "
                                "WHERE section_id = :secid AND tenant_id = :tid "
                                "AND status = 'waiting' "
                                "ORDER BY position ASC LIMIT 1"
                            ),
                            {"secid": section_id, "tid": str(tenant_id)},
                        )
                        wl_row = wl.mappings().first()
                        if not wl_row:
                            continue
                        candidates.append(
                            (
                                tenant_id,
                                section_id,
                                UUID(str(wl_row["id"])),
                                UUID(str(wl_row["student_id"])),
                                bool(wl_row["auto_enroll"]),
                                wl_row["notified_at"] is not None,
                            )
                        )

            for (
                tenant_id,
                section_id,
                waitlist_id,
                student_id,
                auto_enroll,
                already_notified,
            ) in candidates:
                if not auto_enroll:
                    # Notify ONCE — skip if this waitlist row was already alerted, else the
                    # job would re-email on every scheduled run (the inbox-flood bug).
                    if already_notified:
                        continue
                    # Just notify; no write. Email (outbox) + in-app chat notification.
                    async with tenant_session(session_factory, tenant_id) as session:
                        ctx = await notify_context(
                            session, section_id=UUID(section_id), student_id=student_id
                        )
                        await outbox_write(
                            session,
                            tenant_id=tenant_id,
                            event_type="seat_open_notify",
                            payload={
                                "student_id": str(student_id),
                                "section_id": section_id,
                                "waitlist_id": str(waitlist_id),
                                **ctx,
                            },
                        )
                        await _inapp_notify(
                            session,
                            tenant_id=tenant_id,
                            student_id=student_id,
                            kind="seat_open",
                            body=(
                                f"A seat just opened in {ctx['course_code']}"
                                + (f" ({ctx['course_name']})" if ctx["course_name"] else "")
                                + f" — {section_label(ctx)}. You can enroll now; "
                                "just ask me to register you for it."
                            ),
                        )
                        # Mark notified so we never alert this row again.
                        await session.execute(
                            sa.text(
                                "UPDATE waitlist SET notified_at = :now "
                                "WHERE id = :wid AND tenant_id = :tid"
                            ),
                            {
                                "now": datetime.now(UTC),
                                "wid": str(waitlist_id),
                                "tid": str(tenant_id),
                            },
                        )
                    notified += 1
                    continue

                # auto_enroll=True: re-verify eligibility FIRST.
                eligible, reason = await _verify_eligibility_for_seat(
                    dsn=dsn,
                    tenant_id=tenant_id,
                    student_id=student_id,
                    section_id=UUID(section_id),
                )

                if eligible:
                    from keel.services.actions.waitlist_service import fulfill_waitlist_tx

                    async with tenant_session(session_factory, tenant_id) as session:
                        await fulfill_waitlist_tx(
                            session,
                            waitlist_id=waitlist_id,
                            tenant_id=tenant_id,
                            student_id=student_id,
                            section_id=UUID(section_id),
                        )
                    filled += 1
                    # In-app chat notification (email is emitted by fulfill_waitlist_tx).
                    async with tenant_session(session_factory, tenant_id) as session:
                        ctx = await notify_context(
                            session, section_id=UUID(section_id), student_id=student_id
                        )
                        await _inapp_notify(
                            session,
                            tenant_id=tenant_id,
                            student_id=student_id,
                            kind="seat_filled",
                            body=(
                                f"Good news — a seat opened in {ctx['course_code']}"
                                + (f" ({ctx['course_name']})" if ctx["course_name"] else "")
                                + f" and I've automatically enrolled you in {section_label(ctx)}. "
                                "It's on your schedule now. 🎉"
                            ),
                        )
                else:
                    # Mark failed; notify; the seat is not wasted — next iteration
                    # will pick up #2 (their position is lower now that #1 failed).
                    async with tenant_session(session_factory, tenant_id) as session:
                        await session.execute(
                            sa.text(
                                "UPDATE waitlist SET status = 'failed' "
                                "WHERE id = :wid AND tenant_id = :tid"
                            ),
                            {"wid": str(waitlist_id), "tid": str(tenant_id)},
                        )
                        ctx = await notify_context(
                            session, section_id=UUID(section_id), student_id=student_id
                        )
                        await outbox_write(
                            session,
                            tenant_id=tenant_id,
                            event_type="seat_fill_failed",
                            payload={
                                "student_id": str(student_id),
                                "section_id": section_id,
                                "waitlist_id": str(waitlist_id),
                                "reason": reason,
                                **ctx,
                            },
                        )
                    failed += 1
                    _log.info(
                        "worker.seat_fill_failed",
                        student_id=str(student_id),
                        section_id=section_id,
                        reason=reason,
                    )

        finally:
            await engine.dispose()

        return {"filled": filled, "notified": notified, "failed": failed}

    result = asyncio.run(_run())
    _log.info("worker.capacity_sync.done", **result)
    return result


async def _section_course_code(session: Any, section_id: str) -> str:
    """Look up a section's course code (for human-readable notification bodies)."""
    row = await session.execute(
        sa.text("SELECT course_code FROM sections WHERE id = :sid"),
        {"sid": section_id},
    )
    code = row.scalar_one_or_none()
    return str(code) if code else "your waitlisted course"




async def _inapp_notify(
    session: Any,
    *,
    tenant_id: UUID,
    student_id: UUID,
    kind: str,
    body: str,
) -> None:
    """Write an in-app notification row (surfaced in the Keel chat widget by polling)."""
    await session.execute(
        sa.text(
            "INSERT INTO notifications (tenant_id, student_id, kind, body) "
            "VALUES (:tid, :sid, :kind, :body)"
        ),
        {"tid": str(tenant_id), "sid": str(student_id), "kind": kind, "body": body},
    )


async def _verify_eligibility_for_seat(
    *,
    dsn: str,
    tenant_id: UUID,
    student_id: UUID,
    section_id: UUID,
) -> tuple[bool, str]:
    """Re-run engine verifier for one student + one section.

    Checks: has_hold, section still has capacity, student not already enrolled.
    Returns (eligible, reason_if_not).
    """
    engine = db_infra.create_engine(dsn)
    session_factory = db_infra.create_session_factory(engine)
    try:
        from keel.infra.database.session import tenant_session

        async with tenant_session(session_factory, tenant_id) as session:
            # Hold check.
            hold_row = await session.execute(
                sa.text(
                    "SELECT has_hold, hold_reason FROM students "
                    "WHERE id = :sid AND tenant_id = :tid"
                ),
                {"sid": str(student_id), "tid": str(tenant_id)},
            )
            student = hold_row.mappings().first()
            if not student:
                return False, "Student not found."
            if student["has_hold"]:
                return False, f"Student has a hold: {student['hold_reason'] or 'unknown'}."

            # Capacity re-check.
            sec_row = await session.execute(
                sa.text(
                    "SELECT capacity, enrolled FROM sections WHERE id = :secid AND tenant_id = :tid"
                ),
                {"secid": str(section_id), "tid": str(tenant_id)},
            )
            sec = sec_row.mappings().first()
            if not sec:
                return False, "Section no longer exists."
            if int(sec["enrolled"]) >= int(sec["capacity"]):
                return False, "Section is now full."

            # Already enrolled check.
            enroll_row = await session.execute(
                sa.text(
                    "SELECT id FROM enrollments "
                    "WHERE student_id = :sid AND section_id = :secid "
                    "AND tenant_id = :tid AND status = 'enrolled'"
                ),
                {"sid": str(student_id), "secid": str(section_id), "tid": str(tenant_id)},
            )
            if enroll_row.scalar_one_or_none():
                return False, "Student is already enrolled in this section."

        return True, ""
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Expiry sweep — expire stale pending actions
# ---------------------------------------------------------------------------


def expiry_sweep_job() -> dict[str, int]:
    """Scheduled job: expire pending actions older than ACTION_TTL_HOURS.

    Discards their suspended LangGraph threads by marking actions expired.
    The LangGraph checkpoint for an expired thread remains in Postgres but is
    effectively dead — the next sweep or a cleanup job can remove it.
    """
    _, dsn = _bootstrap()

    async def _run() -> dict[str, int]:
        engine = db_infra.create_engine(dsn)
        session_factory = db_infra.create_session_factory(engine)
        cutoff = datetime.now(UTC) - timedelta(hours=_ACTION_TTL_HOURS)
        total_expired = 0
        try:
            from keel.infra.database.session import tenant_session
            from keel.services.actions import ActionRepo

            # Enumerate tenants from the non-RLS table, then expire per-tenant
            # under RLS (an unscoped scan of `actions` returns zero rows).
            for tenant_id in await _active_tenant_ids(session_factory):
                async with tenant_session(session_factory, tenant_id) as session:
                    count = await ActionRepo.expire_stale(
                        session, tenant_id=tenant_id, older_than=cutoff
                    )
                    total_expired += count

        finally:
            await engine.dispose()

        return {"expired": total_expired}

    result = asyncio.run(_run())
    _log.info("worker.expiry_sweep.done", **result)
    return result
