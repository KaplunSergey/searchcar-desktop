"""add durable scan queue ownership and recovery metadata

Revision ID: 0012
Revises: 0011
"""

from alembic import op
import sqlalchemy as sa


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scan_runs", sa.Column("worker_id", sa.String(64)))
    op.add_column(
        "scan_runs",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "scan_runs",
        sa.Column("started_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "scan_runs",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "scan_runs",
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )

    op.execute(
        sa.text(
            """
            UPDATE scan_runs
            SET status = 'INTERRUPTED',
                worker_id = NULL,
                finished_at = COALESCE(finished_at, NOW()),
                error = COALESCE(
                    error,
                    'Application stopped before the scan finished'
                )
            WHERE status IN ('RUNNING', 'CANCEL_REQUESTED')
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE project_scan_runs
            SET status = 'INTERRUPTED',
                error_code = COALESCE(error_code, 'APP_INTERRUPTED')
            WHERE status IN ('QUEUED', 'RUNNING')
              AND scan_run_id IN (
                  SELECT id FROM scan_runs WHERE status = 'INTERRUPTED'
              )
            """
        )
    )
    op.create_index("ix_scan_runs_worker_id", "scan_runs", ["worker_id"])
    op.create_index(
        "ix_scan_runs_queue_order",
        "scan_runs",
        ["status", "created_at", "id"],
    )
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX uq_scan_runs_single_active_worker
            ON scan_runs ((1))
            WHERE status IN ('RUNNING', 'CANCEL_REQUESTED')
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX uq_scan_runs_single_active_worker"))
    op.drop_index("ix_scan_runs_queue_order", table_name="scan_runs")
    op.drop_index("ix_scan_runs_worker_id", table_name="scan_runs")
    op.drop_column("scan_runs", "finished_at")
    op.drop_column("scan_runs", "heartbeat_at")
    op.drop_column("scan_runs", "started_at")
    op.drop_column("scan_runs", "attempt_count")
    op.drop_column("scan_runs", "worker_id")
