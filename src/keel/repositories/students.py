"""Student-centric repository (layer 2).

Owns the SQL that the read-only agent tools (audit_degree, propose_plan,
gpa_estimate, …) previously issued inline to assemble a student's planning
context. Per CLAUDE.md §5, all DB access lives in the repository layer; the tools
now call these methods instead of running ``sa.text(...)`` themselves.

Repositories are grouped by domain entity (students, sections, catalog, …), not
by operation type. The SQL here is moved verbatim from the tool layer — same
query text, same parameters, same result shape — so behavior is unchanged. Each
repository is bound to one ``(session, tenant_id)`` and the session must already
have ``app.tenant_id`` set (open it via ``tenant_session``).
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from keel.repositories.base import TenantScopedRepository


class StudentRepository(TenantScopedRepository):
    """Reads/writes centered on one student.

    ``load_context`` returns the raw-row envelope (transcript, catalog, prereqs,
    coreqs, program, requirements) that ``_build_engine_objects`` converts into
    typed engine objects. It stays a plain ``dict`` of rows — the row→engine-object
    mapping is a separate (pure) concern and is not done here.
    """

    async def load_context(self, student_id: str) -> dict[str, Any]:
        """Load transcript, program, and catalog for one student.

        Returns ``{}`` when the student does not exist for this tenant.
        """
        tid = str(self._tenant_id)
        row = await self._session.execute(
            sa.text("""
                SELECT s.program_id, s.has_hold, s.hold_reason,
                       s.current_term, s.current_year,
                       u.display_name AS student_name, u.email AS student_email
                FROM students s
                LEFT JOIN users u ON u.id = s.user_id
                WHERE s.id = :sid AND s.tenant_id = :tid
            """),
            {"sid": student_id, "tid": tid},
        )
        student = row.mappings().first()
        if student is None:
            return {}

        # Transcript (table is student_transcript per migration 0001)
        tx = await self._session.execute(
            sa.text("""
                SELECT course_code, grade, passed, term, year
                FROM student_transcript
                WHERE student_id = :sid AND tenant_id = :tid
            """),
            {"sid": student_id, "tid": tid},
        )
        transcript_rows = tx.mappings().all()

        # Courses (difficulty exists in 0001; capacity is on sections, not courses)
        cr = await self._session.execute(
            sa.text("""
                SELECT c.code, c.name, c.credits, c.offered_terms, c.difficulty
                FROM courses c
                WHERE c.tenant_id = :tid
            """),
            {"tid": tid},
        )
        course_rows = cr.mappings().all()

        # Prereqs (column is requires_code per migration 0001)
        pr = await self._session.execute(
            sa.text("""
                SELECT course_code, requires_code
                FROM prerequisites
                WHERE tenant_id = :tid
            """),
            {"tid": tid},
        )
        prereq_rows = pr.mappings().all()

        # Coreqs
        coq = await self._session.execute(
            sa.text("""
                SELECT course_code, coreq_code
                FROM corequisites
                WHERE tenant_id = :tid
            """),
            {"tid": tid},
        )
        coreq_rows = coq.mappings().all()

        # Program + requirements
        prog_row = None
        req_rows: list[Any] = []
        if student["program_id"]:
            p = await self._session.execute(
                sa.text(
                    "SELECT id, code, name, total_credits_required, tenant_id "
                    "FROM programs WHERE id = :pid AND tenant_id = :tid"
                ),
                {"pid": student["program_id"], "tid": tid},
            )
            prog_row = p.mappings().first()
            if prog_row:
                rq = await self._session.execute(
                    sa.text(
                        "SELECT group_name, required_credits, eligible_course_codes "
                        "FROM program_requirements "
                        "WHERE program_id = :pid AND tenant_id = :tid"
                    ),
                    {"pid": student["program_id"], "tid": tid},
                )
                req_rows = list(rq.mappings().all())

        return {
            "student": dict(student),
            "student_id_str": student_id,
            "tenant_id_str": tid,
            "transcript_rows": [dict(r) for r in transcript_rows],
            "course_rows": [dict(r) for r in course_rows],
            "prereq_rows": [dict(r) for r in prereq_rows],
            "coreq_rows": [dict(r) for r in coreq_rows],
            "program_row": dict(prog_row) if prog_row else None,
            "req_rows": [dict(r) for r in req_rows],
        }

    async def get_account_info(self, student_id: str) -> dict[str, Any] | None:
        """Account facts for one student joined to their program, or ``None``."""
        row = await self._session.execute(
            sa.text(
                "SELECT s.current_term, s.current_year, s.has_hold, s.hold_reason, "
                "p.code AS program_code, p.name AS program_name "
                "FROM students s LEFT JOIN programs p ON p.id = s.program_id "
                "WHERE s.id = :sid AND s.tenant_id = :tid"
            ),
            {"sid": student_id, "tid": str(self._tenant_id)},
        )
        r = row.mappings().first()
        return dict(r) if r else None

    async def get_current_term_year(self, student_id: str) -> dict[str, Any] | None:
        """The student's current registration term/year row, or ``None``."""
        row = await self._session.execute(
            sa.text(
                "SELECT current_term, current_year FROM students "
                "WHERE id = :sid AND tenant_id = :tid"
            ),
            {"sid": student_id, "tid": str(self._tenant_id)},
        )
        r = row.mappings().first()
        return dict(r) if r else None

    async def get_transcript_aggregate(self, student_id: str) -> dict[str, Any]:
        """Completed-credits / GPA / failed-count rollup over the transcript."""
        agg = await self._session.execute(
            sa.text(
                "SELECT COALESCE(SUM(c.credits) FILTER (WHERE tr.passed), 0) AS cc, "
                "ROUND(AVG(tr.grade) FILTER (WHERE tr.grade IS NOT NULL), 2) AS gpa, "
                "COUNT(*) FILTER (WHERE NOT tr.passed) AS failed "
                "FROM student_transcript tr "
                "LEFT JOIN courses c ON c.code = tr.course_code "
                "AND c.tenant_id = tr.tenant_id "
                "WHERE tr.student_id = :sid AND tr.tenant_id = :tid"
            ),
            {"sid": student_id, "tid": str(self._tenant_id)},
        )
        a_row = agg.mappings().first()
        return dict(a_row) if a_row else {}
