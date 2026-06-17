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
        engine = db_infra.create_engine(dsn)
        session_factory = db_infra.create_session_factory(engine)
        enqueued = 0
        try:
            async with session_factory() as session:
                rows = await session.execute(
                    sa.text(
                        "SELECT id, tenant_id, event_type, payload, attempts "
                        "FROM outbox "
                        "WHERE processed = false AND attempts < :max_attempts "
                        "ORDER BY created_at "
                        "LIMIT 100"
                    ),
                    {"max_attempts": _MAX_OUTBOX_ATTEMPTS},
                )
                outbox_rows = rows.mappings().all()

            redis_conn = Redis.from_url(settings.redis_url)
            q = Queue("keel", connection=redis_conn)

            for row in outbox_rows:
                q.enqueue(
                    send_outbox_event_job,
                    kwargs={
                        "outbox_id": str(row["id"]),
                        "event_type": str(row["event_type"] or row.get("kind", "unknown")),
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

    async def _run() -> dict[str, str]:
        engine = db_infra.create_engine(dsn)
        session_factory = db_infra.create_session_factory(engine)
        try:
            async with session_factory() as session:
                # Dedup: if already processed, skip.
                row = await session.execute(
                    sa.text("SELECT processed FROM outbox WHERE id = :oid"),
                    {"oid": outbox_id},
                )
                r = row.mappings().first()
                if not r or r["processed"]:
                    return {"status": "already_processed", "outbox_id": outbox_id}

                # Increment attempt counter.
                await session.execute(
                    sa.text("UPDATE outbox SET attempts = attempts + 1 WHERE id = :oid"),
                    {"oid": outbox_id},
                )
                await session.commit()

            # Perform the side effect (email send).
            _send_email(event_type=event_type, payload=payload)

            # Mark processed AFTER success (never at enqueue time).
            async with session_factory() as session:
                await session.execute(
                    sa.text(
                        "UPDATE outbox SET processed = true, published_at = :now WHERE id = :oid"
                    ),
                    {"oid": outbox_id, "now": datetime.now(UTC)},
                )
                await session.commit()

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


def _send_email(*, event_type: str, payload: dict[str, Any]) -> None:
    """Send an email for the given event type.

    Phase 3: logs the intent; wires to SMTP in Phase 5 when admin console exposes
    SMTP config.  The outbox row is the guarantee — no dual-write.
    """
    student_id = payload.get("student_id", "unknown")
    section_id = payload.get("section_id", payload.get("section_ids", ""))

    _TEMPLATES = {
        "enrollment_confirmation": f"You are enrolled. Section: {section_id}.",
        "waitlist_joined": f"You joined the waitlist. Position: {payload.get('position')}.",
        "seat_open_notify": f"A seat opened in section {section_id}. Register within the window.",
        "seat_filled_confirmation": f"You were auto-enrolled in section {section_id}.",
        "seat_fill_failed": (
            f"Could not auto-enroll you in section {section_id}: "
            f"{payload.get('reason', 'eligibility changed')}."
        ),
        "waitlist_left": f"Removed from waitlist for section {section_id}.",
    }
    body = _TEMPLATES.get(event_type, f"Event: {event_type}")

    _log.info(
        "worker.email.would_send",
        event_type=event_type,
        student_id=student_id,
        body_preview=body[:80],
    )
    # TODO: wire SMTP (Phase 5) — `smtplib.SMTP(host).sendmail(from, to, body)`


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
            # Load sections with spare capacity across all tenants.
            async with session_factory() as session:
                sections = await session.execute(
                    sa.text(
                        "SELECT id, tenant_id, capacity, enrolled "
                        "FROM sections WHERE enrolled < capacity "
                        "LIMIT 200"
                    )
                )
                section_rows = sections.mappings().all()

            for sec in section_rows:
                section_id = str(sec["id"])
                tenant_id = UUID(str(sec["tenant_id"]))

                # Load the front of the waitlist for this section.
                async with session_factory() as session:
                    wl = await session.execute(
                        sa.text(
                            "SELECT id, student_id, auto_enroll FROM waitlist "
                            "WHERE section_id = :secid AND tenant_id = :tid "
                            "AND status = 'waiting' "
                            "ORDER BY position ASC LIMIT 1"
                        ),
                        {"secid": section_id, "tid": str(tenant_id)},
                    )
                    wl_row = wl.mappings().first()

                if not wl_row:
                    continue

                waitlist_id = UUID(str(wl_row["id"]))
                student_id = UUID(str(wl_row["student_id"]))
                auto_enroll = bool(wl_row["auto_enroll"])

                if not auto_enroll:
                    # Just notify; no write.
                    async with session_factory() as session:
                        from keel.services.actions import outbox_write

                        await outbox_write(
                            session,
                            tenant_id=tenant_id,
                            event_type="seat_open_notify",
                            payload={
                                "student_id": str(student_id),
                                "section_id": section_id,
                                "waitlist_id": str(waitlist_id),
                            },
                        )
                        await session.commit()
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
                    from keel.infra.database.session import tenant_session
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
                else:
                    # Mark failed; notify; the seat is not wasted — next iteration
                    # will pick up #2 (their position is lower now that #1 failed).
                    async with session_factory() as session:
                        await session.execute(
                            sa.text(
                                "UPDATE waitlist SET status = 'failed' "
                                "WHERE id = :wid AND tenant_id = :tid"
                            ),
                            {"wid": str(waitlist_id), "tid": str(tenant_id)},
                        )
                        from keel.services.actions import outbox_write

                        await outbox_write(
                            session,
                            tenant_id=tenant_id,
                            event_type="seat_fill_failed",
                            payload={
                                "student_id": str(student_id),
                                "section_id": section_id,
                                "waitlist_id": str(waitlist_id),
                                "reason": reason,
                            },
                        )
                        await session.commit()
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
            async with session_factory() as session:
                rows = await session.execute(
                    sa.text("SELECT DISTINCT tenant_id FROM actions WHERE status = 'pending'")
                )
                tenant_ids = [UUID(str(r[0])) for r in rows.fetchall()]

            from keel.infra.database.session import tenant_session
            from keel.services.actions import ActionRepo

            for tenant_id in tenant_ids:
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
