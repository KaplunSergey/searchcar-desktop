"""store the last status observed by each project

Revision ID: 0009
Revises: 0008
"""

from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_cars",
        sa.Column(
            "last_observed_status",
            sa.String(length=40),
            nullable=True,
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE project_cars AS relation
            SET last_observed_status = CASE
                WHEN car.status = 'SOLD'
                     AND relation.last_observed_at IS NOT NULL
                     AND relation.last_observed_at >= car.updated_at
                    THEN 'SOLD'
                WHEN car.status = 'SOLD'
                    THEN 'UPDATED'
                ELSE car.status
            END
            FROM cars AS car
            WHERE relation.car_id = car.id
            """
        )
    )


def downgrade() -> None:
    op.drop_column("project_cars", "last_observed_status")
