"""Fix active saved-plan uniqueness for is_active.

Revision ID: 0015_grad_plan_active_index
Revises: 0014_waitlist_notified_at
Create Date: 2026-06-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_grad_plan_active_index"
down_revision = "0014_waitlist_notified_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_plans_one_active")
    op.create_index(
        "uq_plans_one_active",
        "plans",
        ["tenant_id", "student_id"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_plans_one_active")
    op.create_index(
        "uq_plans_one_active",
        "plans",
        ["tenant_id", "student_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
