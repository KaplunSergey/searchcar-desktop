"""add project search pagination mode

Revision ID: 0007
Revises: 0006
"""

from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "search_page_mode",
            sa.String(length=16),
            nullable=False,
            server_default="FIRST_PAGE",
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "search_page_mode")
