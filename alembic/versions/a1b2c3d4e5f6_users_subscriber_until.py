"""users_subscriber_until — GH #100 monetization arc: Ko-fi entitlement horizon column

Revision ID: a1b2c3d4e5f6
Revises: f871fd59415e
Create Date: 2026-07-25 00:00:00.000000

Rule 11 note: this migration only ADDS a nullable column to users; it alters nothing
existing, so the pre-migration-schema rehearsal pressure that applies to
migration-gating tools (dedup, requeue) is nil here — no query against an existing
model changes shape.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f871fd59415e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("subscriber_until", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "subscriber_until")
