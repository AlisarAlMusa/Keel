"""extend actions.type to cover institutional requests (F1–F4)

Revision ID: 0010_institutional_action_types
Revises: 0009_widget_config_all
Create Date: 2026-06-22

Institutional requests (graduation application, major-change, petition, advisor
escalation) now go through the SAME stage → approve → execute action pattern as
enrollment, so they are gated by explicit student approval and cannot be filed by
the agent alone (closing the F3 ``approved=True`` bypass).

That requires the ``actions`` table to accept the institutional action types. The
0003 baseline ``ck_actions_type`` only allowed enrollment/waitlist; this migration
extends it (the 0003 comment already anticipated these types).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010_institutional_action_types"
down_revision: str | None = "0009_widget_config_all"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_TYPES = (
    "'enrollment','waitlist_join','waitlist_leave',"
    "'graduation','major_change','petition','escalate'"
)
_OLD_TYPES = "'enrollment','waitlist_join','waitlist_leave'"


def upgrade() -> None:
    op.execute("ALTER TABLE actions DROP CONSTRAINT IF EXISTS ck_actions_type")
    op.execute(f"ALTER TABLE actions ADD CONSTRAINT ck_actions_type CHECK (type IN ({_NEW_TYPES}))")


def downgrade() -> None:
    op.execute("ALTER TABLE actions DROP CONSTRAINT IF EXISTS ck_actions_type")
    op.execute(f"ALTER TABLE actions ADD CONSTRAINT ck_actions_type CHECK (type IN ({_OLD_TYPES}))")
