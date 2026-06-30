"""add waitlist.notified_at (notify-once for seat-open alerts)

Revision ID: 0014_waitlist_notified_at
Revises: 0013_section_instructor
Create Date: 2026-06-28

The capacity-sync worker's notify-only path used to re-emit a ``seat_open_notify``
on EVERY scheduled run for the same waiting student (no state change), flooding the
inbox. This adds a nullable ``notified_at`` timestamp so a waitlisted student is
notified about an open seat exactly once: the worker sets it on first notice and
skips any row that already has it.

Nullable → no backfill required and existing rows stay valid. RLS is unaffected
(same table, same tenant_id policy).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_waitlist_notified_at"
down_revision: str | None = "0013_section_instructor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "waitlist",
        sa.Column("notified_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("waitlist", "notified_at")
