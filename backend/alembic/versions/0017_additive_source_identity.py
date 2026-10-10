"""add source identity without replacing legacy Encar constraints

Revision ID: 0017
Revises: 0016
"""

from alembic import op
import sqlalchemy as sa


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("projects", "cars", "car_aliases"):
        op.add_column(table, sa.Column(
            "source_key", sa.String(32), nullable=False, server_default="encar"
        ))
    op.add_column("cars", sa.Column(
        "source_listing_id", sa.String(128), nullable=False, server_default=""
    ))
    op.execute(sa.text("""
        UPDATE cars SET source_listing_id = canonical_encar_id
        WHERE source_key = 'encar' AND source_listing_id = ''
    """))


def downgrade() -> None:
    op.drop_column("cars", "source_listing_id")
    for table in ("car_aliases", "cars", "projects"):
        op.drop_column(table, "source_key")
