"""Add auction rounds, schedule versions and observations.

Revision ID: 0020
Revises: 0019
"""
from alembic import op
from app.auction_migration import migrate_auctions_v1

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    migrate_auctions_v1(op.get_bind())


def downgrade() -> None:
    raise RuntimeError("auction_downgrade_requires_verified_pre_migration_backup")
