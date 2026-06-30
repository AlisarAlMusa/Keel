"""add sections.instructor (nullable)

Revision ID: 0013_section_instructor
Revises: 0012_drop_portal_definer
Create Date: 2026-06-25

Phase 5 (registration-section-flow): the agentic section-selection step shows the
student real section options (days/times, instructor, seats). This adds a nullable
``instructor`` column to ``sections`` so a section can carry its teaching staff.

Nullable → no backfill required and existing rows stay valid. RLS is unaffected
(same table, same tenant_id policy). Instructor names are synthetic/seeded for the
demo (see DATA.md).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_section_instructor"
down_revision: str | None = "0012_drop_portal_definer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sections", sa.Column("instructor", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sections", "instructor")
