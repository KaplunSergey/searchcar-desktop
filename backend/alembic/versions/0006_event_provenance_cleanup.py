"""invalidate legacy condition events from known identity collisions

Revision ID: 0006
Revises: 0005
"""

from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


TARGET_IDS = (
    "42404253",
    "42292620",
    "42093545",
    "42172299",
    "40586061",
    "42075422",
    "40917098",
    "42048138",
    "41368994",
    "42047323",
    "41306876",
    "41713365",
    "42335151",
)


def upgrade() -> None:
    target_values = ", ".join(f"('{value}')" for value in TARGET_IDS)
    op.get_bind().execute(
        sa.text(
            f"""
            WITH targets(encar_id) AS (VALUES {target_values})
            UPDATE car_events AS event
            SET integrity_status = 'INVALIDATED',
                integrity_reason = 'LEGACY_IDENTITY_COMPARISON_UNTRUSTED'
            FROM cars AS car, targets
            WHERE event.car_id = car.id
              AND car.canonical_encar_id = targets.encar_id
              AND event.kind IN (
                  'ACCIDENT_INFO_CHANGED',
                  'MILEAGE_CHANGED'
              )
              AND event.payload ->> 'scan_run_id' IS NULL
            """
        )
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
            UPDATE car_events
            SET integrity_status = 'VALID',
                integrity_reason = NULL
            WHERE integrity_reason =
                'LEGACY_IDENTITY_COMPARISON_UNTRUSTED'
              AND kind IN (
                  'ACCIDENT_INFO_CHANGED',
                  'MILEAGE_CHANGED'
              )
            """
        )
    )
