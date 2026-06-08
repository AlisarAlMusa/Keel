"""Development seed data (US4).

Populates two tenants, each with a realistic catalog: >=20 courses connected by
prerequisite chains, corequisites, scheduled sections, program requirements, and
two students with transcripts. Each tenant's catalog text is uploaded to MinIO
so later RAG phases can index it.

DSN resolution (so it runs both inside compose and from the host):
- ``SEED_DATABASE_URL`` if set (host/CI), else ``Settings.database_url`` with the
  DB password resolved from Vault (compose path).

MinIO upload is best-effort: creds come from ``MINIO_ACCESS_KEY``/
``MINIO_SECRET_KEY`` env, else Vault. If neither is reachable, the DB seed still
completes and a warning is logged (compose run gets full coverage).

Idempotency (FR-022): if the tenants already exist, the script refuses unless
``SEED_RESET=1``, in which case it deletes them (cascade) and reseeds. It never
writes partial/inconsistent data.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from keel.config import get_settings
from keel.infra import db as db_infra
from keel.infra import storage as storage_infra
from keel.infra.orm import (
    Corequisite,
    Course,
    Prerequisite,
    ProgramRequirement,
    Section,
    Student,
    StudentTranscript,
    Tenant,
)
from keel.logging import configure_logging, get_logger

log = get_logger(__name__)

# --- Catalog template (applied per tenant) --------------------------------

# 24 courses with prerequisite chains across CS / MATH / coreq lab.
_COURSES: list[dict[str, Any]] = [
    {
        "code": "CS101",
        "name": "Intro to Programming",
        "credits": 3,
        "difficulty": 2,
        "terms": ["fall", "spring"],
        "desc": "Foundations of programming in Python.",
    },
    {
        "code": "CS102",
        "name": "Data Structures",
        "credits": 3,
        "difficulty": 3,
        "terms": ["fall", "spring"],
        "desc": "Lists, trees, graphs, and complexity.",
    },
    {
        "code": "CS201",
        "name": "Algorithms",
        "credits": 3,
        "difficulty": 4,
        "terms": ["fall"],
        "desc": "Design and analysis of algorithms.",
    },
    {
        "code": "CS202",
        "name": "Computer Systems",
        "credits": 4,
        "difficulty": 4,
        "terms": ["spring"],
        "desc": "Architecture, memory, and the OS interface.",
    },
    {
        "code": "CS210",
        "name": "Discrete Math for CS",
        "credits": 3,
        "difficulty": 3,
        "terms": ["fall", "spring"],
        "desc": "Logic, sets, combinatorics, proofs.",
    },
    {
        "code": "CS301",
        "name": "Databases",
        "credits": 3,
        "difficulty": 3,
        "terms": ["fall"],
        "desc": "Relational modeling, SQL, transactions.",
    },
    {
        "code": "CS302",
        "name": "Operating Systems",
        "credits": 4,
        "difficulty": 5,
        "terms": ["spring"],
        "desc": "Processes, scheduling, concurrency.",
    },
    {
        "code": "CS310",
        "name": "Computer Networks",
        "credits": 3,
        "difficulty": 4,
        "terms": ["fall"],
        "desc": "Layered protocols and the Internet.",
    },
    {
        "code": "CS320",
        "name": "Software Engineering",
        "credits": 3,
        "difficulty": 3,
        "terms": ["spring"],
        "desc": "Design, testing, and team workflows.",
    },
    {
        "code": "CS330",
        "name": "Machine Learning",
        "credits": 3,
        "difficulty": 5,
        "terms": ["fall"],
        "desc": "Supervised and unsupervised learning.",
    },
    {
        "code": "CS401",
        "name": "Distributed Systems",
        "credits": 3,
        "difficulty": 5,
        "terms": ["fall"],
        "desc": "Consistency, replication, consensus.",
    },
    {
        "code": "CS402",
        "name": "Compilers",
        "credits": 4,
        "difficulty": 5,
        "terms": ["spring"],
        "desc": "Lexing, parsing, code generation.",
    },
    {
        "code": "CS410",
        "name": "Computer Security",
        "credits": 3,
        "difficulty": 4,
        "terms": ["fall"],
        "desc": "Threats, cryptography, secure design.",
    },
    {
        "code": "CS420",
        "name": "Capstone Project",
        "credits": 4,
        "difficulty": 4,
        "terms": ["spring"],
        "desc": "Team-built software capstone.",
    },
    {
        "code": "MATH101",
        "name": "Calculus I",
        "credits": 4,
        "difficulty": 3,
        "terms": ["fall", "spring"],
        "desc": "Limits, derivatives, integrals.",
    },
    {
        "code": "MATH102",
        "name": "Calculus II",
        "credits": 4,
        "difficulty": 4,
        "terms": ["spring"],
        "desc": "Series, techniques of integration.",
    },
    {
        "code": "MATH201",
        "name": "Linear Algebra",
        "credits": 3,
        "difficulty": 3,
        "terms": ["fall"],
        "desc": "Vectors, matrices, eigenvalues.",
    },
    {
        "code": "MATH210",
        "name": "Probability & Statistics",
        "credits": 3,
        "difficulty": 4,
        "terms": ["spring"],
        "desc": "Distributions, inference, estimation.",
    },
    {
        "code": "PHYS201",
        "name": "Physics I",
        "credits": 4,
        "difficulty": 4,
        "terms": ["fall"],
        "desc": "Mechanics and motion.",
    },
    {
        "code": "PHYS201L",
        "name": "Physics I Lab",
        "credits": 1,
        "difficulty": 2,
        "terms": ["fall"],
        "desc": "Companion lab for Physics I.",
    },
    {
        "code": "ENG101",
        "name": "Technical Writing",
        "credits": 3,
        "difficulty": 2,
        "terms": ["fall", "spring"],
        "desc": "Clear written communication.",
    },
    {
        "code": "ECON101",
        "name": "Microeconomics",
        "credits": 3,
        "difficulty": 2,
        "terms": ["fall", "spring"],
        "desc": "Markets, incentives, pricing.",
    },
    {
        "code": "CS340",
        "name": "Human-Computer Interaction",
        "credits": 3,
        "difficulty": 3,
        "terms": ["spring"],
        "desc": "Usability and interface design.",
    },
    {
        "code": "CS350",
        "name": "Cloud Computing",
        "credits": 3,
        "difficulty": 4,
        "terms": ["fall"],
        "desc": "Virtualization, containers, scaling.",
    },
]

# Prerequisite edges (course requires prereq).
_PREREQS: list[tuple[str, str, float | None]] = [
    ("CS102", "CS101", None),
    ("CS201", "CS102", 2.0),
    ("CS201", "CS210", None),
    ("CS202", "CS102", None),
    ("CS301", "CS102", None),
    ("CS302", "CS202", 2.0),
    ("CS310", "CS202", None),
    ("CS320", "CS102", None),
    ("CS330", "MATH201", None),
    ("CS330", "MATH210", None),
    ("CS401", "CS302", None),
    ("CS402", "CS201", None),
    ("CS410", "CS310", None),
    ("CS420", "CS320", None),
    ("CS350", "CS310", None),
    ("MATH102", "MATH101", None),
    ("MATH201", "MATH101", None),
    ("MATH210", "MATH102", None),
    ("CS210", "MATH101", None),
]

# Corequisite edges (must be taken same term or earlier).
_COREQS: list[tuple[str, str]] = [
    ("PHYS201", "PHYS201L"),
]

_PROGRAM_REQS: list[dict[str, Any]] = [
    {
        "group": "CS Core",
        "credits": 21,
        "courses": ["CS101", "CS102", "CS201", "CS202", "CS301", "CS302", "CS320"],
    },
    {"group": "Math", "credits": 11, "courses": ["MATH101", "MATH102", "MATH201", "MATH210"]},
    {
        "group": "CS Electives",
        "credits": 9,
        "courses": ["CS310", "CS330", "CS340", "CS350", "CS401", "CS402", "CS410"],
    },
    {"group": "General Education", "credits": 6, "courses": ["ENG101", "ECON101"]},
]

# Two tenants.
_TENANTS: list[dict[str, str]] = [
    {"slug": "northane", "name": "Northane University", "program": "BSCS"},
    {"slug": "summit", "name": "Summit College", "program": "BSCS"},
]

_TERM_SLOTS = {
    # simple non-conflicting meeting patterns by index
    0: [
        {"day": "mon", "start_min": 540, "end_min": 615},
        {"day": "wed", "start_min": 540, "end_min": 615},
    ],
    1: [
        {"day": "tue", "start_min": 600, "end_min": 675},
        {"day": "thu", "start_min": 600, "end_min": 675},
    ],
    2: [
        {"day": "mon", "start_min": 660, "end_min": 735},
        {"day": "wed", "start_min": 660, "end_min": 735},
    ],
}


def _catalog_text(tenant_name: str) -> str:
    lines = [f"{tenant_name} — Course Catalog", ""]
    for c in _COURSES:
        lines.append(f"{c['code']} — {c['name']} ({c['credits']} cr): {c['desc']}")
    return "\n".join(lines)


async def _tenant_exists(session: AsyncSession, slug: str) -> bool:
    result = await session.execute(select(Tenant.id).where(Tenant.slug == slug))
    return result.first() is not None


async def _seed_tenant(session: AsyncSession, tenant_id: UUID, tenant_cfg: dict[str, Any]) -> None:
    """Insert all tenant-owned rows. Caller has set app.tenant_id for RLS."""
    program = tenant_cfg["program"]

    # Courses
    for c in _COURSES:
        session.add(
            Course(
                tenant_id=tenant_id,
                code=c["code"],
                name=c["name"],
                credits=c["credits"],
                difficulty=c["difficulty"],
                offered_terms=c["terms"],
                description=c["desc"],
            )
        )

    # Prerequisites / corequisites
    for course_code, requires, min_grade in _PREREQS:
        session.add(
            Prerequisite(
                tenant_id=tenant_id,
                course_code=course_code,
                requires_code=requires,
                min_grade=min_grade,
            )
        )
    for course_code, coreq in _COREQS:
        session.add(Corequisite(tenant_id=tenant_id, course_code=course_code, coreq_code=coreq))

    # Sections — one section per course in its first offered term, year 2026.
    for idx, c in enumerate(_COURSES):
        term = c["terms"][0]
        session.add(
            Section(
                tenant_id=tenant_id,
                course_code=c["code"],
                term=term,
                year=2026,
                slots=_TERM_SLOTS[idx % 3],
                capacity=30,
                enrolled=idx % 5,
            )
        )

    # Program requirements
    for req in _PROGRAM_REQS:
        session.add(
            ProgramRequirement(
                tenant_id=tenant_id,
                program_code=program,
                group_name=req["group"],
                required_credits=req["credits"],
                eligible_course_codes=req["courses"],
            )
        )

    # Two students with transcripts.
    soph_id = uuid4()
    junior_id = uuid4()
    session.add(
        Student(
            id=soph_id,
            tenant_id=tenant_id,
            program_code=program,
            max_credits_per_term=18,
            current_term="fall",
            current_year=2026,
        )
    )
    session.add(
        Student(
            id=junior_id,
            tenant_id=tenant_id,
            program_code=program,
            max_credits_per_term=18,
            current_term="spring",
            current_year=2026,
        )
    )

    soph_done = [
        ("CS101", "fall", 2025, 3.7),
        ("MATH101", "fall", 2025, 3.3),
        ("ENG101", "spring", 2025, 4.0),
        ("CS102", "spring", 2025, 3.0),
    ]
    junior_done = soph_done + [
        ("CS210", "fall", 2025, 3.3),
        ("MATH201", "fall", 2025, 2.7),
        ("CS201", "spring", 2025, 3.0),
        ("CS202", "spring", 2025, 2.3),
    ]
    for sid, entries in ((soph_id, soph_done), (junior_id, junior_done)):
        for code, term, year, grade in entries:
            session.add(
                StudentTranscript(
                    tenant_id=tenant_id,
                    student_id=sid,
                    course_code=code,
                    term=term,
                    year=year,
                    grade=grade,
                    passed=True,
                )
            )


def _resolve_dsn() -> str:
    override = os.environ.get("SEED_DATABASE_URL")
    if override:
        return override
    settings = get_settings()
    # Compose path: resolve the DB password from Vault.
    from keel.infra.vault import VaultConfig, load_secrets

    secrets = load_secrets(
        VaultConfig(
            addr=settings.vault_addr,
            token=settings.vault_token,
            kv_mount=settings.vault_kv_mount,
            secret_path=settings.vault_secret_path,
        )
    )
    return settings.database_url.replace(":placeholder@", f":{secrets['db_password']}@", 1)


async def _upload_catalogs(tenant_names: dict[str, str]) -> None:
    """Best-effort upload of catalog text to MinIO."""
    settings = get_settings()
    access = os.environ.get("MINIO_ACCESS_KEY")
    secret = os.environ.get("MINIO_SECRET_KEY")
    if not (access and secret):
        try:
            from keel.infra.vault import VaultConfig, load_secrets

            sec = load_secrets(
                VaultConfig(
                    addr=settings.vault_addr,
                    token=settings.vault_token,
                    kv_mount=settings.vault_kv_mount,
                    secret_path=settings.vault_secret_path,
                )
            )
            access, secret = sec["minio_access_key"], sec["minio_secret_key"]
        except Exception as exc:  # noqa: BLE001
            log.warning("minio_creds_unavailable_skipping_upload", error=type(exc).__name__)
            return
    try:
        client = storage_infra.create_s3_client(
            endpoint=settings.minio_endpoint, access_key=access, secret_key=secret
        )
        storage_infra.ensure_bucket(client, settings.minio_bucket)
        for slug, name in tenant_names.items():
            storage_infra.put_text(
                client, settings.minio_bucket, f"{slug}/catalog.txt", _catalog_text(name)
            )
        log.info("catalog_uploaded", count=len(tenant_names))
    except Exception as exc:  # noqa: BLE001
        log.warning("minio_upload_failed", error=type(exc).__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging(service="keel-seed", level=settings.keel_log_level)
    reset = os.environ.get("SEED_RESET") == "1"

    dsn = _resolve_dsn()
    engine = db_infra.create_engine(dsn)
    session_factory = db_infra.create_session_factory(engine)

    try:
        # Idempotency check (tenants table is not RLS-protected).
        async with session_factory() as session:
            async with session.begin():
                existing = [
                    s for s in (t["slug"] for t in _TENANTS) if await _tenant_exists(session, s)
                ]
            if existing and not reset:
                log.info("seed_skipped_already_present", tenants=existing)
                print(f"Tenants already seeded: {existing}. Set SEED_RESET=1 to wipe and reseed.")
                return
            if existing and reset:
                async with session.begin():
                    await session.execute(
                        delete(Tenant).where(Tenant.slug.in_([t["slug"] for t in _TENANTS]))
                    )
                log.info("seed_reset_deleted", tenants=existing)

        # Insert tenants (no RLS), then per-tenant data with tenant context set.
        tenant_ids: dict[str, UUID] = {}
        async with session_factory() as session:
            async with session.begin():
                for t in _TENANTS:
                    tid = uuid4()
                    tenant_ids[t["slug"]] = tid
                    session.add(Tenant(id=tid, slug=t["slug"], name=t["name"]))

        for t in _TENANTS:
            tid = tenant_ids[t["slug"]]
            async with session_factory() as session:
                async with session.begin():
                    await session.execute(
                        text("SELECT set_config('app.tenant_id', :tid, true)"),
                        {"tid": str(tid)},
                    )
                    await _seed_tenant(session, tid, t)

        # Report counts per tenant.
        for t in _TENANTS:
            tid = tenant_ids[t["slug"]]
            async with session_factory() as session:
                async with session.begin():
                    await session.execute(
                        text("SELECT set_config('app.tenant_id', :tid, true)"),
                        {"tid": str(tid)},
                    )
                    n_courses = await session.scalar(select(func.count()).select_from(Course))
                    n_students = await session.scalar(select(func.count()).select_from(Student))
            log.info("seed_tenant_done", slug=t["slug"], courses=n_courses, students=n_students)

        await _upload_catalogs({t["slug"]: t["name"] for t in _TENANTS})
        log.info("seed_complete", tenants=len(_TENANTS))
        print(f"Seeded {len(_TENANTS)} tenants, {len(_COURSES)} courses each.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
