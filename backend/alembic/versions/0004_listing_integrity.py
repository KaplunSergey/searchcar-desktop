"""separate project search state and repair unsafe tracking identities

Revision ID: 0004
Revises: 0003
"""

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_cars",
        sa.Column(
            "search_status",
            sa.String(length=30),
            nullable=False,
            server_default="FOUND",
        ),
    )
    connection = op.get_bind()

    # A user-disabled relation is a tombstone. It must never be revived by a
    # later scan, and globally excluded cars must stop all project tracking.
    connection.execute(
        sa.text(
            """
            UPDATE project_cars
            SET search_status = 'REMOVED_FROM_PROJECT'
            WHERE tracking_enabled = false
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE project_cars AS pc
            SET tracking_enabled = false,
                favorite = false,
                search_status = 'REMOVED_FROM_PROJECT'
            FROM cars AS c
            WHERE pc.car_id = c.id
              AND c.excluded = true
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE project_cars
            SET search_status = 'NOT_FOUND_IN_SEARCH'
            WHERE tracking_enabled = true
              AND consecutive_missing_scans > 0
            """
        )
    )

    # An Encar id cannot be both a canonical listing and an alias of another
    # listing. These collisions caused cross-car comparisons and false prices.
    connection.execute(
        sa.text(
            """
            DELETE FROM car_aliases AS alias
            USING cars AS canonical
            WHERE alias.alias_id = canonical.canonical_encar_id
            """
        )
    )

    # Search misses used to promote a car to SOLD after three runs. Preserve
    # only SOLD states backed by an explicit Encar sold/deleted page.
    connection.execute(
        sa.text(
            """
            UPDATE cars AS car
            SET status = 'UPDATED'
            WHERE car.status IN (
                'SOLD',
                'UNAVAILABLE',
                'NOT_FOUND_IN_SEARCH',
                'RELISTED'
            )
              AND NOT EXISTS (
                  SELECT 1
                  FROM car_events AS event
                  WHERE event.car_id = car.id
                    AND event.kind = 'SOLD'
                    AND event.payload ->> 'reason' =
                        'encar_sold_or_deleted_page'
              )
            """
        )
    )

    op.alter_column("project_cars", "search_status", server_default=None)


def downgrade() -> None:
    op.drop_column("project_cars", "search_status")
