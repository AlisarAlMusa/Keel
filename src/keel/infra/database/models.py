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
    tenant_id: Mapped[UUID] = _tenant_fk()
    email: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    created_at: Mapped[datetime] = _created_at()


class RequestQueue(Base):
    __tablename__ = "request_queue"

    id: Mapped[UUID] = _pk()
    tenant_id: Mapped[UUID] = _tenant_fk()
    student_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
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
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
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
)
