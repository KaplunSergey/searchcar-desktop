"""remove repeated sold entries from historical scan reports

Revision ID: 0010
Revises: 0009
"""

from alembic import op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            WITH ranked_sold AS (
                SELECT
                    scan.id AS scan_id,
                    report.ordinality,
                    row_number() OVER (
                        PARTITION BY
                            report.item->>'project_id',
                            report.item->>'car_id'
                        ORDER BY
                            scan.created_at,
                            scan.id,
                            report.ordinality
                    ) AS occurrence
                FROM scan_runs AS scan
                CROSS JOIN LATERAL json_array_elements(
                    COALESCE(scan.payload->'report', '[]'::json)
                ) WITH ORDINALITY AS report(item, ordinality)
                WHERE report.item->>'change' = 'SOLD'
            ),
            duplicate_items AS (
                SELECT scan_id, ordinality
                FROM ranked_sold
                WHERE occurrence > 1
            ),
            rebuilt_reports AS (
                SELECT
                    scan.id AS scan_id,
                    COALESCE(
                        json_agg(report.item ORDER BY report.ordinality)
                            FILTER (WHERE duplicate.ordinality IS NULL),
                        '[]'::json
                    ) AS report
                FROM scan_runs AS scan
                CROSS JOIN LATERAL json_array_elements(
                    COALESCE(scan.payload->'report', '[]'::json)
                ) WITH ORDINALITY AS report(item, ordinality)
                LEFT JOIN duplicate_items AS duplicate
                    ON duplicate.scan_id = scan.id
                   AND duplicate.ordinality = report.ordinality
                WHERE scan.id IN (
                    SELECT DISTINCT scan_id
                    FROM duplicate_items
                )
                GROUP BY scan.id
            )
            UPDATE scan_runs AS scan
            SET payload = jsonb_set(
                scan.payload::jsonb,
                '{report}',
                rebuilt.report::jsonb
            )::json
            FROM rebuilt_reports AS rebuilt
            WHERE scan.id = rebuilt.scan_id
            """
        )
    )


def downgrade() -> None:
    # Duplicate report rows are intentionally not recreated.
    pass
