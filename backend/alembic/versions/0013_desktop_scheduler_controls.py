"""add desktop scheduler pause and wake catch-up controls

Revision ID: 0013
Revises: 0012
"""

from alembic import op
import sqlalchemy as sa


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scheduler_settings",
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "scheduler_settings",
        sa.Column(
            "catch_up_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("scheduler_settings", "catch_up_enabled")
    op.drop_column("scheduler_settings", "paused")
