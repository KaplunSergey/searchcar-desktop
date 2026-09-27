"""store transient new status per project

Revision ID: 0016
Revises: 0015
"""

from alembic import op
import sqlalchemy as sa


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_cars",
        sa.Column(
            "is_new",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE project_cars
            SET is_new = TRUE
            WHERE car_id IN (SELECT id FROM cars WHERE status = 'NEW')
            """
        )
    )


def downgrade() -> None:
    op.drop_column("project_cars", "is_new")
