"""SQLAlchemy ORM models — 16 baseline + Phase 2 additions.

These are SEPARATE from the domain value objects in ``domain.models``.
Repositories map ORM rows -> domain objects and never leak ORM instances above
the repository layer. ``Base.metadata`` is what Alembic's ``env.py`` targets.

Enums are stored as TEXT with CHECK constraints (see the migration) to avoid
Postgres ENUM migration friction; the ORM keeps them as plain ``str``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

_NOW = text("now()")
_GEN_UUID = text("gen_random_uuid()")


class Base(DeclarativeBase):
    """Declarative base; ``metadata`` is the Alembic autogenerate target."""


def _pk() -> Mapped[UUID]:
    return mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=_GEN_UUID)


def _tenant_fk() -> Mapped[UUID]:
    return mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=_NOW)


class Tenant(Base):
    """The tenant registry. NOT tenant-owned — no RLS."""

    __tablename__ = "tenants"

    id: Mapped[UUID] = _pk()
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    widget_origin_allowlist: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = _created_at()


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = _pk()
    # Nullable: platform_operator has no tenant scope (migration 0006)
    tenant_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    # roles: 'admin' | 'student' | 'tenant_admin' | 'platform_operator'
    role: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set for tenant_admin and platform_operator only (bcrypt hash)
    hashed_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()


# ---------------------------------------------------------------------------
# Phase 2 addition — must be declared before Student and ProgramRequirement
# because they FK into it.
# ---------------------------------------------------------------------------


class Program(Base):
    __tablename__ = "programs"

    id: Mapped[UUID] = _pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    degree_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'BS'"))
    total_credits_required: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("120")
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class Student(Base):
    __tablename__ = "students"

    id: Mapped[UUID] = _pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    program_code: Mapped[str] = mapped_column(Text, nullable=False)
    program_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("programs.id", ondelete="SET NULL"), nullable=True
    )
    max_credits_per_term: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("18")
    )
    current_term: Mapped[str] = mapped_column(Text, nullable=False)
    current_year: Mapped[int] = mapped_column(Integer, nullable=False)
    has_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    hold_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[UUID] = _pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False)
    offered_terms: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()


class Prerequisite(Base):
    __tablename__ = "prerequisites"

    id: Mapped[UUID] = _pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    course_code: Mapped[str] = mapped_column(Text, nullable=False)
    requires_code: Mapped[str] = mapped_column(Text, nullable=False)
    min_grade: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)


class Corequisite(Base):
    __tablename__ = "corequisites"

    id: Mapped[UUID] = _pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    course_code: Mapped[str] = mapped_column(Text, nullable=False)
    coreq_code: Mapped[str] = mapped_column(Text, nullable=False)


class Section(Base):
    __tablename__ = "sections"

    id: Mapped[UUID] = _pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    course_code: Mapped[str] = mapped_column(Text, nullable=False)
    term: Mapped[str] = mapped_column(Text, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    slots: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    enrolled: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = _created_at()


class ProgramRequirement(Base):
    __tablename__ = "program_requirements"

    id: Mapped[UUID] = _pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    program_code: Mapped[str] = mapped_column(Text, nullable=False)
    program_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("programs.id", ondelete="SET NULL"), nullable=True
    )
    group_name: Mapped[str] = mapped_column(Text, nullable=False)
    required_credits: Mapped[int] = mapped_column(Integer, nullable=False)
    eligible_course_codes: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )


class StudentTranscript(Base):
    __tablename__ = "student_transcript"

    id: Mapped[UUID] = _pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    student_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False
    )
    course_code: Mapped[str] = mapped_column(Text, nullable=False)
    term: Mapped[str] = mapped_column(Text, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    grade: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[UUID] = _pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    student_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    plan_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    validated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    # Phase 3: one-active-plan (partial unique index in migration) + catalog change detection.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    catalog_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()


class Enrollment(Base):
    __tablename__ = "enrollments"

    id: Mapped[UUID] = _pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    student_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False
    )
    section_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sections.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'enrolled'"))
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    # Phase 5: provenance — 'keel' | 'manual' | 'sis'. Drives the "via Keel" badge.
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'sis'"))
    created_at: Mapped[datetime] = _created_at()


class Waitlist(Base):
    __tablename__ = "waitlist"

    id: Mapped[UUID] = _pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    student_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False
    )
    section_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sections.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    # Phase 3 MVP: delegated-consent auto-enroll flag + lifecycle status.
    auto_enroll: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # waiting | fulfilled | failed | left
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'waiting'"))
    created_at: Mapped[datetime] = _created_at()


class RequestQueue(Base):
    __tablename__ = "request_queue"

    id: Mapped[UUID] = _pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    student_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    # Phase 4: the entity the request targets (program_id, course_code, …) + idempotency.
    target: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    created_at: Mapped[datetime] = _created_at()
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class Outbox(Base):
    __tablename__ = "outbox"

    id: Mapped[UUID] = _pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    # Phase 3: canonical event_type name; processed/attempts for publisher dedup.
    event_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = _created_at()


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[UUID] = _tenant_fk()
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = _created_at()


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[UUID] = _pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    student_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _created_at()


# ---------------------------------------------------------------------------
# Phase 2 additions — StudentPreference, RagChunk
# ---------------------------------------------------------------------------


class StudentPreference(Base):
    __tablename__ = "student_preferences"

    id: Mapped[UUID] = _pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    student_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False
    )
    response_style: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'detailed'")
    )
    language: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'en'"))
    difficulty_preference: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'any'")
    )
    career_interest: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=_NOW
    )


class RagChunk(Base):
    """One embedded chunk in the RAG corpus.

    ``chunk_id`` is a stable hash so re-ingest is idempotent (upsert on conflict).
    ``embedding`` is a 1024-dim vector (Cohere embed-multilingual-v3.0).
    FTS is handled via a GIN expression index on ``to_tsvector('english', content)``
    created in the migration; no generated column in the ORM avoids type-map friction.
    """

    __tablename__ = "rag_chunks"

    id: Mapped[UUID] = _pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    chunk_id: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str | None] = mapped_column(Text, nullable=True)
    doc: Mapped[str | None] = mapped_column(Text, nullable=True)
    section: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(1024), nullable=True)
    lang: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'en'"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=_NOW
    )


# ---------------------------------------------------------------------------
# Phase 3 additions — Action (the resumable-agent write pattern)
# ---------------------------------------------------------------------------


class Action(Base):
    """One staged write action awaiting human approval (spec §2 / plan.md §1.1).

    Lifecycle: pending → approved → executed | rejected | failed | expired.
    The payload is FROZEN at stage time and written verbatim on approve-resume.
    thread_id is written at stage time and read at approve time — never supplied
    in the approve request (closes the cross-thread resume attack vector).
    """

    __tablename__ = "actions"

    id: Mapped[UUID] = _pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    student_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False
    )
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    # enrollment | waitlist_join | waitlist_leave
    type: Mapped[str] = mapped_column(Text, nullable=False)
    # FROZEN approved payload — execute node reads this, never LLM-supplied args.
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # pending | approved | executed | rejected | failed | expired
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    created_at: Mapped[datetime] = _created_at()
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    audit_ref: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("audit_log.id", ondelete="SET NULL"),
        nullable=True,
    )


class Advisor(Base):
    """F4 escalation routing reference (spec §4) — NOT an auth principal.

    No role, no login, no permissions. The escalate tool resolves a target email
    from this table by program/department; null program = the tenant's catch-all.
    RLS-scoped by tenant_id like every other tenant-owned table.
    """

    __tablename__ = "advisors"

    id: Mapped[UUID] = _pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    program: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()


# ---------------------------------------------------------------------------
# Phase 5 additions — UsageEvent, WidgetConfig
# ---------------------------------------------------------------------------


class UsageEvent(Base):
    """Cost-tracking row written on every LLM/embedding call (spec §6).

    kind: 'llm' | 'embedding'
    cost_estimate: USD approximation based on token counts.
    """

    __tablename__ = "usage_event"

    id: Mapped[UUID] = _pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    cost_estimate: Mapped[Decimal] = mapped_column(
        Numeric(12, 8), nullable=False, server_default=text("0")
    )
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()


class WidgetConfig(Base):
    """Per-tenant Keel widget configuration (spec §3).

    One row per tenant. Safety rails are NOT here — they are hardcoded.
    allowed_origins drives the origin check (spec §1 verify_origin_or_403).
    """

    __tablename__ = "widget_config"

    id: Mapped[UUID] = _pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    persona: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text(
            "'You are Keel, a helpful AI academic advisor. "
            "You help students plan their courses thoughtfully and safely.'"
        ),
    )
    persona_name: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'Keel'"))
    allowed_origins: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    enabled_tools: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


# ---------------------------------------------------------------------------
# Phase 5 addendum — PlatformAudit, PortalUser
# ---------------------------------------------------------------------------


class PlatformAudit(Base):
    """Platform operator action log — NOT RLS-scoped; survives tenant erase.

    Written on every provision / suspend / unsuspend / erase action by the
    platform operator.  target_tenant_id uses ON DELETE SET NULL so the row
    persists even after the tenant is erased.
    """

    __tablename__ = "platform_audit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_tenant_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True
    )
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = _created_at()


class PortalUser(Base):
    """SIS/portal-domain login credentials — NOT a Keel auth principal.

    Holds email + bcrypt password for portal students and registrars.
    Completely separate from Keel's users table; RLS-scoped by tenant_id.
    student_id links to the SIS student record (NULL for registrar).
    """

    __tablename__ = "portal_user"

    id: Mapped[UUID] = _pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)  # 'student' | 'registrar'
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    hashed_password: Mapped[str] = mapped_column(Text, nullable=False)
    student_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("students.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = _created_at()


# Tables that carry tenant-owned data and therefore receive RLS in the migration.
TENANT_OWNED_TABLES: tuple[str, ...] = (
    "users",
    "students",
    "courses",
    "prerequisites",
    "corequisites",
    "sections",
    "program_requirements",
    "student_transcript",
    "plans",
    "enrollments",
    "waitlist",
    "request_queue",
    "outbox",
    "audit_log",
    "notifications",
    # Phase 2 additions
    "programs",
    "student_preferences",
    "rag_chunks",
    # Phase 3 additions
    "actions",
    # Phase 4 additions
    "advisors",
    # Phase 5 additions
    "usage_event",
    "widget_config",
    # Phase 5 addendum
    "portal_user",
)
