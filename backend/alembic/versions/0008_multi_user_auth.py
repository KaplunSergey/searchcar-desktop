"""add multi-user authentication and tenant ownership

Revision ID: 0008
Revises: 0007
"""

from alembic import op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("username_key", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="USER"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="ACTIVE"),
        sa.Column("project_limit", sa.Integer(), nullable=True, server_default="1"),
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("role IN ('ADMIN', 'USER')", name="ck_users_role"),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'BLOCKED', 'PENDING_APPROVAL')",
            name="ck_users_status",
        ),
        sa.CheckConstraint(
            "project_limit IS NULL OR project_limit >= 0",
            name="ck_users_project_limit",
        ),
        sa.UniqueConstraint("username_key", name="uq_users_username_key"),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO users (
                username,
                username_key,
                password_hash,
                role,
                status,
                project_limit,
                must_change_password
            )
            VALUES (
                'Serhii',
                'serhii',
                '!bootstrap-required',
                'ADMIN',
                'ACTIVE',
                NULL,
                true
            )
            """
        )
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("user_agent_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_token_hash", "auth_sessions", ["token_hash"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])

    op.create_table(
        "auth_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username_key", sa.String(length=64), nullable=False),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "success",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_auth_attempts_username_key", "auth_attempts", ["username_key"])
    op.create_index("ix_auth_attempts_ip_hash", "auth_attempts", ["ip_hash"])
    op.create_index("ix_auth_attempts_created_at", "auth_attempts", ["created_at"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "target_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=True),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False, server_default="SUCCESS"),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_audit_logs_actor_user_id", "audit_logs", ["actor_user_id"])
    op.create_index("ix_audit_logs_target_user_id", "audit_logs", ["target_user_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    op.add_column("projects", sa.Column("owner_id", sa.Integer(), nullable=True))
    op.add_column("scan_runs", sa.Column("owner_id", sa.Integer(), nullable=True))
    op.add_column("scheduler_settings", sa.Column("user_id", sa.Integer(), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE projects
            SET owner_id = (SELECT id FROM users WHERE username_key = 'serhii')
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE scan_runs
            SET owner_id = (SELECT id FROM users WHERE username_key = 'serhii')
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE scheduler_settings
            SET user_id = (SELECT id FROM users WHERE username_key = 'serhii')
            """
        )
    )
    op.alter_column("projects", "owner_id", nullable=False)
    op.alter_column("scan_runs", "owner_id", nullable=False)
    op.alter_column("scheduler_settings", "user_id", nullable=False)
    op.create_foreign_key(
        "fk_projects_owner_id_users",
        "projects",
        "users",
        ["owner_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_scan_runs_owner_id_users",
        "scan_runs",
        "users",
        ["owner_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_scheduler_settings_user_id_users",
        "scheduler_settings",
        "users",
        ["user_id"],
        ["id"],
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])
    op.create_index("ix_scan_runs_owner_id", "scan_runs", ["owner_id"])
    op.create_index(
        "ix_scheduler_settings_user_id",
        "scheduler_settings",
        ["user_id"],
        unique=True,
    )

    op.drop_constraint("projects_name_key_key", "projects", type_="unique")
    op.drop_constraint("projects_search_url_key", "projects", type_="unique")
    op.create_unique_constraint(
        "uq_projects_owner_name_key",
        "projects",
        ["owner_id", "name_key"],
    )
    op.create_unique_constraint(
        "uq_projects_owner_search_url",
        "projects",
        ["owner_id", "search_url"],
    )

    op.create_table(
        "user_car_states",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "car_id",
            sa.Integer(),
            sa.ForeignKey("cars.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column(
            "excluded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("comment_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "rating IS NULL OR rating BETWEEN 1 AND 5",
            name="ck_user_car_states_rating",
        ),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO user_car_states (
                user_id,
                car_id,
                comment,
                rating,
                excluded,
                comment_updated_at
            )
            SELECT
                admin.id,
                car.id,
                car.comment,
                car.rating,
                car.excluded,
                CASE WHEN car.comment IS NOT NULL THEN car.updated_at ELSE NULL END
            FROM cars AS car
            CROSS JOIN users AS admin
            WHERE admin.username_key = 'serhii'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE cars
            SET excluded = false
            WHERE integrity_status = 'VALID'
            """
        )
    )

    op.add_column(
        "project_cars",
        sa.Column("last_observed_snapshot_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "project_cars",
        sa.Column("last_observed_price", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "project_cars",
        sa.Column("last_observed_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "project_cars",
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_project_cars_last_snapshot",
        "project_cars",
        "car_snapshots",
        ["last_observed_snapshot_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        sa.text(
            """
            UPDATE project_cars AS relation
            SET last_observed_price = car.current_price,
                last_observed_fingerprint = car.fingerprint,
                last_observed_at = COALESCE(relation.last_seen_at, car.updated_at),
                last_observed_snapshot_id = (
                    SELECT snapshot.id
                    FROM car_snapshots AS snapshot
                    WHERE snapshot.car_id = car.id
                      AND snapshot.integrity_status = 'VALID'
                    ORDER BY snapshot.created_at DESC, snapshot.id DESC
                    LIMIT 1
                )
            FROM cars AS car
            WHERE relation.car_id = car.id
            """
        )
    )

    op.add_column("car_events", sa.Column("user_id", sa.Integer(), nullable=True))
    op.add_column("car_events", sa.Column("project_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_car_events_user_id_users",
        "car_events",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_car_events_project_id_projects",
        "car_events",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_car_events_user_id", "car_events", ["user_id"])
    op.create_index("ix_car_events_project_id", "car_events", ["project_id"])
    op.execute(
        sa.text(
            """
            UPDATE car_events
            SET user_id = (SELECT id FROM users WHERE username_key = 'serhii')
            WHERE kind IN (
                'USER_VIEWED',
                'FAVORITE_ADDED',
                'FAVORITE_REMOVED',
                'COMMENT_UPDATED',
                'RATING_UPDATED',
                'USER_EXCLUDED',
                'REMOVED_FROM_PROJECT'
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE car_events AS event
            SET project_id = (event.payload ->> 'project_id')::integer
            WHERE event.payload ->> 'project_id' ~ '^[0-9]+$'
              AND EXISTS (
                  SELECT 1
                  FROM projects
                  WHERE projects.id = (event.payload ->> 'project_id')::integer
              )
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_car_events_project_id", table_name="car_events")
    op.drop_index("ix_car_events_user_id", table_name="car_events")
    op.drop_constraint("fk_car_events_project_id_projects", "car_events", type_="foreignkey")
    op.drop_constraint("fk_car_events_user_id_users", "car_events", type_="foreignkey")
    op.drop_column("car_events", "project_id")
    op.drop_column("car_events", "user_id")

    op.drop_constraint("fk_project_cars_last_snapshot", "project_cars", type_="foreignkey")
    op.drop_column("project_cars", "last_observed_at")
    op.drop_column("project_cars", "last_observed_fingerprint")
    op.drop_column("project_cars", "last_observed_price")
    op.drop_column("project_cars", "last_observed_snapshot_id")
    op.drop_table("user_car_states")

    bootstrap_id = sa.text("(SELECT id FROM users WHERE username_key = 'serhii')")
    op.execute(
        sa.text(
            """
            DELETE FROM projects
            WHERE owner_id <> (SELECT id FROM users WHERE username_key = 'serhii')
            """
        )
    )
    op.drop_constraint("uq_projects_owner_search_url", "projects", type_="unique")
    op.drop_constraint("uq_projects_owner_name_key", "projects", type_="unique")
    op.create_unique_constraint("projects_name_key_key", "projects", ["name_key"])
    op.create_unique_constraint("projects_search_url_key", "projects", ["search_url"])

    op.drop_index("ix_scheduler_settings_user_id", table_name="scheduler_settings")
    op.drop_index("ix_scan_runs_owner_id", table_name="scan_runs")
    op.drop_index("ix_projects_owner_id", table_name="projects")
    op.drop_constraint(
        "fk_scheduler_settings_user_id_users",
        "scheduler_settings",
        type_="foreignkey",
    )
    op.drop_constraint("fk_scan_runs_owner_id_users", "scan_runs", type_="foreignkey")
    op.drop_constraint("fk_projects_owner_id_users", "projects", type_="foreignkey")
    op.drop_column("scheduler_settings", "user_id")
    op.drop_column("scan_runs", "owner_id")
    op.drop_column("projects", "owner_id")

    op.drop_table("audit_logs")
    op.drop_table("auth_attempts")
    op.drop_table("auth_sessions")
    op.drop_table("users")
