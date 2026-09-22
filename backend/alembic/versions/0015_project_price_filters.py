"""add project price filters

Revision ID: 0015
Revises: 0014
"""

from alembic import op
import sqlalchemy as sa


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("price_filter_mode", sa.String(length=16), nullable=False, server_default="LINK"),
    )
    op.add_column("projects", sa.Column("price_min_krw", sa.BigInteger(), nullable=True))
    op.add_column("projects", sa.Column("price_max_krw", sa.BigInteger(), nullable=True))
    op.add_column(
        "projects",
        sa.Column("price_filter_revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "projects",
        sa.Column(
            "price_filter_baseline_revision",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "price_filter_baseline_revision")
    op.drop_column("projects", "price_filter_revision")
    op.drop_column("projects", "price_max_krw")
    op.drop_column("projects", "price_min_krw")
    op.drop_column("projects", "price_filter_mode")
