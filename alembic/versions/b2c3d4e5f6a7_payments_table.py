"""payments_table — GH #100 monetization arc: Ko-fi webhook event audit trail

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-26 00:00:00.000000

Rule 11 note: this migration only ADDS a table; it alters nothing existing, so the
pre-migration-schema rehearsal pressure that applies to migration-gating tools (dedup,
requeue) is nil here — no query against an existing model changes shape.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kofi_transaction_id", sa.String(), nullable=False),
        sa.Column("kofi_type", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("tier_name", sa.String(), nullable=True),
        sa.Column("is_subscription_payment", sa.Boolean(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("matched_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("entitlement_days", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["matched_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kofi_transaction_id"),
    )
    op.create_index("ix_payments_email", "payments", ["email"])
    op.create_index("ix_payments_matched_user_id", "payments", ["matched_user_id"])


def downgrade() -> None:
    op.drop_index("ix_payments_matched_user_id", table_name="payments")
    op.drop_index("ix_payments_email", table_name="payments")
    op.drop_table("payments")
