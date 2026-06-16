# ruff: noqa: E501
"""Development seed data — Phase 2 reseed.

DB: 2 tenants · 44 courses · 3 programs · per-tenant sections + students.
MinIO: uploads {slug}/catalog.md and {slug}/policies.md (source from data/rag-corpus/);
       deletes stale {slug}/catalog.txt.
RAG: calls the ingestion pipeline to populate rag_chunks (best-effort; skipped if
     Cohere is unreachable so DB seed still completes).

Idempotency (FR-022): if tenants already exist, skip unless SEED_RESET=1 which
deletes them (cascade) and reseeds. Never writes partial/inconsistent data.

Run:
    SEED_RESET=1 uv run python scripts/seed.py          # inside compose / with Vault
    SEED_DATABASE_URL=postgresql+asyncpg://... uv run python scripts/seed.py  # host
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import cohere
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from keel.config import get_settings
from keel.infra import database as db_infra
from keel.infra import storage as storage_infra
from keel.infra.database.models import (
    Corequisite,
    Course,
    Prerequisite,
    Program,
    ProgramRequirement,
    Section,
    Student,
    StudentTranscript,
    Tenant,
)
from keel.logging import configure_logging, get_logger
from keel.services.ingestion import ingest_file

log = get_logger(__name__)

_REPO_ROOT = Path(__file__).parent.parent
_CORPUS_DIR = _REPO_ROOT / "data" / "rag-corpus"

# ---------------------------------------------------------------------------
# Catalog — 44 courses (24 original + 7 DS + 13 Chem)
# ---------------------------------------------------------------------------

_COURSES: list[dict[str, Any]] = [
    # --- Original 24 ---
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
        "terms": ["fall", "spring"],
        "desc": "Supervised and unsupervised learning.",
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
    # --- New DS (7) ---
    {
        "code": "DS210",
        "name": "Data Science Fundamentals",
        "credits": 3,
        "difficulty": 3,
        "terms": ["fall", "spring"],
        "desc": "Data wrangling, EDA, and visualization.",
    },
    {
        "code": "DS301",
        "name": "Statistical Learning",
        "credits": 3,
        "difficulty": 4,
        "terms": ["fall"],
        "desc": "Regression, classification, and model selection.",
    },
    {
        "code": "DS310",
        "name": "Data Engineering",
        "credits": 3,
        "difficulty": 4,
        "terms": ["fall"],
        "desc": "Pipelines, warehousing, and batch/stream processing.",
    },
    {
        "code": "DS320",
        "name": "Applied Machine Learning",
        "credits": 3,
        "difficulty": 4,
        "terms": ["spring"],
        "desc": "Feature engineering, ensemble methods, deployment.",
    },
    {
        "code": "DS340",
        "name": "Natural Language Processing",
        "credits": 3,
        "difficulty": 5,
        "terms": ["spring"],
        "desc": "Text classification, embeddings, LLMs.",
    },
    {
        "code": "DS350",
        "name": "Deep Learning",
        "credits": 3,
        "difficulty": 5,
        "terms": ["fall"],
        "desc": "Neural networks, CNNs, RNNs, and transformers.",
    },
    {
        "code": "DS401",
        "name": "Data Science Capstone",
        "credits": 4,
        "difficulty": 4,
        "terms": ["spring"],
        "desc": "End-to-end data science project.",
    },
    # --- New Chem (13) ---
    {
        "code": "CHEM101",
        "name": "General Chemistry I",
        "credits": 4,
        "difficulty": 3,
        "terms": ["fall", "spring"],
        "desc": "Atomic structure, bonding, and stoichiometry.",
    },
    {
        "code": "CHEM101L",
        "name": "General Chemistry I Lab",
        "credits": 1,
        "difficulty": 2,
        "terms": ["fall", "spring"],
        "desc": "Lab companion for General Chemistry I.",
    },
    {
        "code": "CHEM102",
        "name": "General Chemistry II",
        "credits": 4,
        "difficulty": 4,
        "terms": ["fall", "spring"],
        "desc": "Equilibrium, kinetics, and thermodynamics.",
    },
    {
        "code": "CHEM201",
        "name": "Organic Chemistry I",
        "credits": 4,
        "difficulty": 5,
        "terms": ["fall"],
        "desc": "Structure, bonding, and reactions of organic compounds.",
    },
    {
        "code": "CHEM201L",
        "name": "Organic Chemistry I Lab",
        "credits": 1,
        "difficulty": 3,
        "terms": ["fall"],
        "desc": "Lab companion for Organic Chemistry I.",
    },
    {
        "code": "CHEM202",
        "name": "Organic Chemistry II",
        "credits": 4,
        "difficulty": 5,
        "terms": ["spring"],
        "desc": "Aromatic compounds, carbonyl reactions, synthesis.",
    },
    {
        "code": "CHEM301",
        "name": "Analytical Chemistry",
        "credits": 3,
        "difficulty": 4,
        "terms": ["fall"],
        "desc": "Quantitative analysis, spectroscopy, chromatography.",
    },
    {
        "code": "CHEM310",
        "name": "Physical Chemistry",
        "credits": 3,
        "difficulty": 5,
        "terms": ["fall"],
        "desc": "Thermodynamics, kinetics, and quantum mechanics.",
    },
    {
        "code": "CHEM311",
        "name": "Physical Chemistry II",
        "credits": 3,
        "difficulty": 5,
        "terms": ["spring"],
        "desc": "Statistical mechanics and spectroscopy.",
    },
    {
        "code": "CHEM320",
        "name": "Biochemistry",
        "credits": 3,
        "difficulty": 4,
        "terms": ["spring"],
        "desc": "Biomolecules, metabolism, and enzyme kinetics.",
    },
    {
        "code": "CHEM330",
        "name": "Inorganic Chemistry",
        "credits": 3,
        "difficulty": 4,
        "terms": ["spring"],
        "desc": "Coordination compounds, organometallics.",
    },
    {
        "code": "CHEM410",
        "name": "Advanced Analytical Chemistry",
        "credits": 3,
        "difficulty": 5,
        "terms": ["fall"],
        "desc": "NMR, MS, and advanced separation techniques.",
    },
    {
        "code": "CHEM420",
        "name": "Chemistry Capstone Research",
        "credits": 4,
        "difficulty": 4,
        "terms": ["spring"],
        "desc": "Original research project under faculty supervision.",
    },
]

# ---------------------------------------------------------------------------
# Prereqs (course ← requires)
# ---------------------------------------------------------------------------

_PREREQS: list[tuple[str, str, float | None]] = [
    # Original CS
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
    ("CS340", "CS102", None),
    ("CS350", "CS310", None),
    ("CS401", "CS302", None),
    ("CS402", "CS201", None),
    ("CS410", "CS310", None),
    ("CS420", "CS320", None),
    # Original MATH
    ("CS210", "MATH101", None),
    ("MATH102", "MATH101", None),
    ("MATH201", "MATH101", None),
    ("MATH210", "MATH102", None),
    # DS
    ("DS210", "CS101", None),
    ("DS301", "MATH210", None),
    ("DS301", "DS210", None),
    ("DS310", "DS210", None),
    ("DS320", "CS301", None),
    ("DS320", "DS210", None),
    ("DS340", "CS330", None),
    ("DS350", "CS330", None),
    ("DS350", "MATH201", None),
    ("DS401", "DS301", None),
    ("DS401", "DS320", None),
    # Chem
    ("CHEM102", "CHEM101", None),
    ("CHEM201", "CHEM102", None),
    ("CHEM202", "CHEM201", None),
    ("CHEM301", "CHEM102", None),
    ("CHEM310", "CHEM102", None),
    ("CHEM310", "MATH102", None),
    ("CHEM310", "PHYS201", None),
    ("CHEM311", "CHEM310", None),
    ("CHEM320", "CHEM201", None),
    ("CHEM330", "CHEM202", None),
    ("CHEM410", "CHEM301", None),
    ("CHEM420", "CHEM301", None),
    ("CHEM420", "CHEM310", None),
]

_COREQS: list[tuple[str, str]] = [
    ("PHYS201", "PHYS201L"),
    ("CHEM101", "CHEM101L"),
    ("CHEM201", "CHEM201L"),
]

# ---------------------------------------------------------------------------
# Programs
# ---------------------------------------------------------------------------

_PROGRAMS: list[dict[str, Any]] = [
    {
        "code": "BSCS",
        "name": "Bachelor of Science in Computer Science",
        "degree_type": "BS",
        "total_credits": 120,
        "description": "Core CS degree with algorithms, systems, and electives.",
    },
    {
        "code": "BSDS",
        "name": "Bachelor of Science in Data Science",
        "degree_type": "BS",
        "total_credits": 120,
        "description": "Interdisciplinary degree combining CS, statistics, and data engineering.",
    },
    {
        "code": "BSCHEM",
        "name": "Bachelor of Science in Chemistry",
        "degree_type": "BS",
        "total_credits": 120,
        "description": "Classical chemistry degree with research capstone.",
    },
]

_PROGRAM_REQS: dict[str, list[dict[str, Any]]] = {
    "BSCS": [
        {
            "group": "CS Core",
            "credits": 21,
            "courses": ["CS101", "CS102", "CS201", "CS202", "CS301", "CS302", "CS320"],
        },
        {"group": "Capstone", "credits": 4, "courses": ["CS420"]},
        {
            "group": "CS Electives",
            "credits": 9,
            "courses": ["CS310", "CS330", "CS340", "CS350", "CS401", "CS402", "CS410"],
        },
        {"group": "Math", "credits": 14, "courses": ["MATH101", "MATH102", "MATH201", "MATH210"]},
        {"group": "Science", "credits": 5, "courses": ["PHYS201", "PHYS201L"]},
        {"group": "General Education", "credits": 6, "courses": ["ENG101", "ECON101"]},
    ],
    "BSDS": [
        {
            "group": "DS Core",
            "credits": 16,
            "courses": ["DS210", "DS301", "DS320", "DS401", "CS301"],
        },
        {"group": "Methods", "credits": 6, "courses": ["CS330", "DS310"]},
        {"group": "DS Electives", "credits": 6, "courses": ["DS340", "DS350", "CS350"]},
        {
            "group": "Math & Stats",
            "credits": 14,
            "courses": ["MATH101", "MATH102", "MATH201", "MATH210"],
        },
        {"group": "Programming", "credits": 6, "courses": ["CS101", "CS102"]},
        {"group": "General Education", "credits": 6, "courses": ["ENG101", "ECON101"]},
    ],
    "BSCHEM": [
        {
            "group": "Chem Core",
            "credits": 22,
            "courses": ["CHEM101", "CHEM102", "CHEM201", "CHEM202", "CHEM301", "CHEM310"],
        },
        {"group": "Labs", "credits": 2, "courses": ["CHEM101L", "CHEM201L"]},
        {
            "group": "Chem Electives",
            "credits": 9,
            "courses": ["CHEM311", "CHEM320", "CHEM330", "CHEM410"],
        },
        {"group": "Capstone", "credits": 4, "courses": ["CHEM420"]},
        {
            "group": "Supporting Science",
            "credits": 13,
            "courses": ["MATH101", "MATH102", "PHYS201", "PHYS201L"],
        },
        {"group": "General Education", "credits": 6, "courses": ["ENG101", "ECON101"]},
    ],
}

# ---------------------------------------------------------------------------
# Tenants
# ---------------------------------------------------------------------------

_TENANTS: list[dict[str, str]] = [
    {"slug": "northane", "name": "Northane University"},
    {"slug": "summit", "name": "Summit College"},
]

# ---------------------------------------------------------------------------
# Per-tenant section overrides (§9.5)
# ---------------------------------------------------------------------------

_SECTION_OVERRIDES: dict[str, dict[str, dict[str, Any]]] = {
    "northane": {
        "CS301": {"enrolled": 30},  # full — waitlist demo
        "CS330": {"term": "fall"},
        "DS210": {"term": "fall"},
        "CHEM101": {"term": "fall"},
    },
    "summit": {
        "DS210": {"enrolled": 30, "term": "spring"},  # full — waitlist demo
        "CS330": {"term": "spring"},
        "CHEM101": {"term": "spring"},
    },
}

# Non-conflicting meeting patterns (cycle through 5 slots by course index).
_TERM_SLOTS: list[list[dict[str, int | str]]] = [
    [
        {"day": "mon", "start_min": 540, "end_min": 615},
        {"day": "wed", "start_min": 540, "end_min": 615},
    ],
    [
        {"day": "tue", "start_min": 600, "end_min": 675},
        {"day": "thu", "start_min": 600, "end_min": 675},
    ],
    [
        {"day": "mon", "start_min": 660, "end_min": 735},
        {"day": "wed", "start_min": 660, "end_min": 735},
    ],
    [
        {"day": "tue", "start_min": 480, "end_min": 555},
        {"day": "thu", "start_min": 480, "end_min": 555},
    ],
    [
        {"day": "mon", "start_min": 780, "end_min": 855},
        {"day": "fri", "start_min": 780, "end_min": 855},
    ],
]

# ---------------------------------------------------------------------------
# Students (§9.6)
# Transcript entry: (course_code, term, year, grade, passed)
# ---------------------------------------------------------------------------

_STUDENTS: dict[str, list[dict[str, Any]]] = {
    "northane": [
        {
            "label": "N1",
            "program_code": "BSCS",
            "current_term": "fall",
            "current_year": 2026,
            "has_hold": False,
            "hold_reason": None,
            "transcript": [
                ("CS101", "fall", 2025, 3.7, True),
                ("MATH101", "fall", 2025, 3.3, True),
                ("ENG101", "fall", 2025, 4.0, True),
                ("CS102", "spring", 2025, 3.0, True),
            ],
        },
        {
            "label": "N2",
            "program_code": "BSCS",
            "current_term": "fall",
            "current_year": 2026,
            "has_hold": False,
            "hold_reason": None,
            "transcript": [
                ("CS101", "fall", 2024, 3.5, True),
                ("MATH101", "fall", 2024, 3.7, True),
                ("ENG101", "fall", 2024, 3.7, True),
                ("CS102", "spring", 2024, 3.3, True),
                ("MATH102", "spring", 2024, 3.3, True),
                ("CS210", "fall", 2025, 3.3, True),
                ("MATH201", "fall", 2025, 3.0, True),
                ("CS201", "spring", 2025, 3.0, True),
                ("MATH210", "spring", 2025, 3.3, True),
            ],
        },
        {
            "label": "N3",
            "program_code": "BSCS",
            "current_term": "fall",
            "current_year": 2026,
            "has_hold": False,
            "hold_reason": None,
            "transcript": [
                ("CS101", "fall", 2025, 2.0, True),
                ("MATH101", "fall", 2025, 1.0, False),  # FAILED
                ("ENG101", "fall", 2025, 2.3, True),
                ("CS102", "spring", 2025, 0.7, False),  # FAILED
                ("CS210", "spring", 2025, 1.7, True),
            ],
        },
    ],
    "summit": [
        {
            "label": "S1",
            "program_code": "BSCS",
            "current_term": "fall",
            "current_year": 2026,
            "has_hold": False,
            "hold_reason": None,
            "transcript": [
                ("CS101", "fall", 2025, 1.7, True),
                ("MATH101", "fall", 2025, 2.0, True),
                ("ENG101", "fall", 2025, 2.3, True),
                ("CS102", "spring", 2025, 1.3, True),
            ],
        },
        {
            "label": "S2",
            "program_code": "BSCS",
            "current_term": "spring",
            "current_year": 2026,
            "has_hold": False,
            "hold_reason": None,
            "transcript": [
                ("CS101", "fall", 2022, 3.7, True),
                ("MATH101", "fall", 2022, 3.7, True),
                ("ENG101", "fall", 2022, 4.0, True),
                ("PHYS201", "fall", 2022, 3.0, True),
                ("PHYS201L", "fall", 2022, 4.0, True),
                ("CS102", "spring", 2022, 3.3, True),
                ("MATH102", "spring", 2022, 3.3, True),
                ("ECON101", "spring", 2022, 3.7, True),
                ("CS201", "fall", 2023, 3.0, True),
                ("CS210", "fall", 2023, 3.7, True),
                ("MATH201", "fall", 2023, 3.0, True),
                ("CS202", "spring", 2023, 3.3, True),
                ("CS301", "spring", 2023, 3.3, True),
                ("MATH210", "spring", 2023, 3.3, True),
                ("CS302", "fall", 2024, 3.0, True),
                ("CS310", "fall", 2024, 3.3, True),
                ("CS320", "spring", 2024, 3.7, True),
                ("CS330", "spring", 2024, 3.0, True),
            ],
        },
        {
            "label": "S3",
            "program_code": "BSCS",
            "current_term": "fall",
            "current_year": 2026,
            "has_hold": True,
            "hold_reason": "unpaid balance",
            "transcript": [
                ("CS101", "fall", 2025, 3.0, True),
                ("MATH101", "fall", 2025, 3.0, True),
                ("CS102", "spring", 2025, 2.7, True),
            ],
        },
    ],
}

# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


async def _tenant_exists(session: AsyncSession, slug: str) -> bool:
    result = await session.execute(select(Tenant.id).where(Tenant.slug == slug))
    return result.first() is not None


async def _seed_tenant(
    session: AsyncSession,
    tenant_id: UUID,
    slug: str,
) -> None:
    """Insert all tenant-owned rows for one tenant. RLS context must already be set."""

    # Courses.
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

    # Prerequisites / corequisites.
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

    # Programs.
    program_ids: dict[str, UUID] = {}
    for p in _PROGRAMS:
        pid = uuid4()
        program_ids[p["code"]] = pid
        session.add(
            Program(
                id=pid,
                tenant_id=tenant_id,
                code=p["code"],
                name=p["name"],
                degree_type=p["degree_type"],
                total_credits_required=p["total_credits"],
                description=p["description"],
            )
        )

    # Program requirements (flush first so programs have PKs).
    await session.flush()
    for prog_code, reqs in _PROGRAM_REQS.items():
        pid = program_ids[prog_code]
        for req in reqs:
            session.add(
                ProgramRequirement(
                    tenant_id=tenant_id,
                    program_code=prog_code,
                    program_id=pid,
                    group_name=req["group"],
                    required_credits=req["credits"],
                    eligible_course_codes=req["courses"],
                )
            )

    # Sections (per-tenant overrides applied).
    overrides = _SECTION_OVERRIDES.get(slug, {})
    code_to_idx = {c["code"]: i for i, c in enumerate(_COURSES)}
    for c in _COURSES:
        base_term = c["terms"][0]
        base_enrolled = code_to_idx[c["code"]] % 5
        ovr = overrides.get(c["code"], {})
        term = ovr.get("term", base_term)
        enrolled = ovr.get("enrolled", base_enrolled)
        session.add(
            Section(
                tenant_id=tenant_id,
                course_code=c["code"],
                term=term,
                year=2026,
                slots=_TERM_SLOTS[code_to_idx[c["code"]] % len(_TERM_SLOTS)],
                capacity=30,
                enrolled=enrolled,
            )
        )

    # Students + transcripts.
    for s in _STUDENTS[slug]:
        sid = uuid4()
        prog_code = s["program_code"]
        session.add(
            Student(
                id=sid,
                tenant_id=tenant_id,
                program_code=prog_code,
                program_id=program_ids.get(prog_code),
                max_credits_per_term=18,
                current_term=s["current_term"],
                current_year=s["current_year"],
                has_hold=s["has_hold"],
                hold_reason=s["hold_reason"],
            )
        )
        for code, term, year, grade, passed in s["transcript"]:
            session.add(
                StudentTranscript(
                    tenant_id=tenant_id,
                    student_id=sid,
                    course_code=code,
                    term=term,
                    year=year,
                    grade=grade,
                    passed=passed,
                )
            )


# ---------------------------------------------------------------------------
# MinIO uploads
# ---------------------------------------------------------------------------


async def _upload_corpus(
    tenant_slugs: list[str],
    access: str,
    secret: str,
) -> dict[str, UUID | None]:
    """Upload catalog.md + policies.md for each tenant; delete stale catalog.txt.
    Returns {slug: tenant_id} for the ingestion step (None if upload failed).
    """
    settings = get_settings()
    try:
        client = storage_infra.create_s3_client(
            endpoint=settings.minio_endpoint, access_key=access, secret_key=secret
        )
        storage_infra.ensure_bucket(client, settings.minio_bucket)
        for slug in tenant_slugs:
            for kind in ("catalog", "policies"):
                src = _CORPUS_DIR / f"{slug}_{kind}.md"
                dst_key = f"{slug}/{kind}.md"
                if src.exists():
                    storage_infra.put_text(client, settings.minio_bucket, dst_key, src.read_text())
                    log.info("corpus_uploaded", key=dst_key)
                else:
                    log.warning("corpus_file_missing", path=str(src))
            # Delete stale catalog.txt if present.
            storage_infra.delete_object(client, settings.minio_bucket, f"{slug}/catalog.txt")
        log.info("corpus_upload_done", count=len(tenant_slugs))
        return {}
    except Exception as exc:  # noqa: BLE001
        log.warning("corpus_upload_failed", error=type(exc).__name__)
        return {}


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


async def _ingest_corpus(
    tenant_ids: dict[str, UUID],
    minio_access: str,
    minio_secret: str,
    cohere_api_key: str,
    session_factory: Any,
) -> None:
    """Embed and upsert corpus chunks for all tenants (best-effort)."""
    if not cohere_api_key:
        log.warning("ingest_skipped_no_cohere_key")
        return
    settings = get_settings()
    try:
        co = cohere.AsyncClientV2(api_key=cohere_api_key)
        s3 = storage_infra.create_s3_client(
            endpoint=settings.minio_endpoint,
            access_key=minio_access,
            secret_key=minio_secret,
        )
        for slug, tid in tenant_ids.items():
            async with session_factory() as session:
                async with session.begin():
                    await session.execute(
                        text("SELECT set_config('app.tenant_id', :tid, true)"),
                        {"tid": str(tid)},
                    )
                    for kind, chunk_type in (("catalog", "course"), ("policies", "policy")):
                        source = f"{slug}/{kind}.md"
                        await ingest_file(
                            tenant_id=tid,
                            source=source,
                            chunk_type=chunk_type,
                            s3_client=s3,
                            bucket=settings.minio_bucket,
                            cohere_client=co,
                            embed_model=settings.embed_model,
                            session=session,
                        )
        log.info("ingest_complete", tenants=list(tenant_ids.keys()))
    except Exception as exc:  # noqa: BLE001
        log.warning("ingest_failed", error=type(exc).__name__, detail=str(exc))


# ---------------------------------------------------------------------------
# Secrets resolution
# ---------------------------------------------------------------------------


def _resolve_secrets() -> dict[str, str]:
    """Return dict with dsn, minio_access, minio_secret, cohere_api_key.

    SEED_DATABASE_URL overrides the DSN (host/CI direct path).
    All other secrets come from Vault if available, else env vars.
    """
    settings = get_settings()
    result: dict[str, str] = {}

    dsn_override = os.environ.get("SEED_DATABASE_URL")
    if dsn_override:
        result["dsn"] = dsn_override
    else:
        from keel.infra.vault import VaultConfig, load_secrets

        secrets = load_secrets(
            VaultConfig(
                addr=settings.vault_addr,
                token=settings.vault_token,
                kv_mount=settings.vault_kv_mount,
                secret_path=settings.vault_secret_path,
            )
        )
        result["dsn"] = settings.database_url.replace(
            ":placeholder@", f":{secrets['db_password']}@", 1
        )
        result["minio_access"] = secrets.get("minio_access_key", "")
        result["minio_secret"] = secrets.get("minio_secret_key", "")
        result["cohere_api_key"] = secrets.get("cohere_api_key", "")

    # Env-var overrides (for host runs where Vault isn't reachable).
    if not result.get("minio_access"):
        result["minio_access"] = os.environ.get("MINIO_ACCESS_KEY", "")
    if not result.get("minio_secret"):
        result["minio_secret"] = os.environ.get("MINIO_SECRET_KEY", "")
    if not result.get("cohere_api_key"):
        result["cohere_api_key"] = os.environ.get("COHERE_API_KEY", "")

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    settings = get_settings()
    configure_logging(service="keel-seed", level=settings.keel_log_level)
    reset = os.environ.get("SEED_RESET") == "1"

    secrets = _resolve_secrets()
    dsn = secrets["dsn"]
    engine = db_infra.create_engine(dsn)
    session_factory = db_infra.create_session_factory(engine)

    try:
        # Idempotency check.
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
            async with session_factory() as session:
                async with session.begin():
                    await session.execute(
                        delete(Tenant).where(Tenant.slug.in_([t["slug"] for t in _TENANTS]))
                    )
            log.info("seed_reset_deleted", tenants=existing)

        # Insert tenants (no RLS).
        tenant_ids: dict[str, UUID] = {}
        async with session_factory() as session:
            async with session.begin():
                for t in _TENANTS:
                    tid = uuid4()
                    tenant_ids[t["slug"]] = tid
                    session.add(Tenant(id=tid, slug=t["slug"], name=t["name"]))

        # Insert all tenant-owned data.
        for t in _TENANTS:
            tid = tenant_ids[t["slug"]]
            async with session_factory() as session:
                async with session.begin():
                    await session.execute(
                        text("SELECT set_config('app.tenant_id', :tid, true)"),
                        {"tid": str(tid)},
                    )
                    await _seed_tenant(session, tid, t["slug"])
            log.info("seed_tenant_done", slug=t["slug"])

        # Verify counts.
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
                    n_programs = await session.scalar(select(func.count()).select_from(Program))
            log.info(
                "seed_counts",
                slug=t["slug"],
                courses=n_courses,
                students=n_students,
                programs=n_programs,
            )

        # MinIO corpus upload (best-effort).
        if secrets.get("minio_access"):
            await _upload_corpus(
                [t["slug"] for t in _TENANTS],
                secrets["minio_access"],
                secrets["minio_secret"],
            )
        else:
            log.warning("minio_creds_unavailable_skipping_upload")

        # RAG ingestion (best-effort).
        await _ingest_corpus(
            tenant_ids,
            secrets.get("minio_access", ""),
            secrets.get("minio_secret", ""),
            secrets.get("cohere_api_key", ""),
            session_factory,
        )

        log.info(
            "seed_complete",
            tenants=len(_TENANTS),
            courses=len(_COURSES),
        )
        print(
            f"Seeded {len(_TENANTS)} tenants × {len(_COURSES)} courses × {len(_PROGRAMS)} programs."
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
