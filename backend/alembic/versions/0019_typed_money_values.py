"""Add typed monetary values without replacing legacy Encar prices.

Revision ID: 0019
Revises: 0018
"""
from alembic import op
from app.money_migration import migrate_money_v1

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    migrate_money_v1(op.get_bind())


def downgrade() -> None:
    raise RuntimeError("money_downgrade_requires_verified_pre_migration_backup")
