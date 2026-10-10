"""scope identity to source; preserve internal IDs and Encar compatibility

Revision ID: 0018
Revises: 0017
"""

from alembic import op
import sqlalchemy as sa

from app.sqlite_migrations import SOURCE_IDENTITY_CHECKS, SOURCE_SCOPED_UNIQUES, _scoped_source_identity


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        # Desktop uses its versioned runner, backup, write lock and FK guard.
        # An Alembic SQLite caller must disable FKs before its transaction too.
        _scoped_source_identity(connection)
        if connection.exec_driver_sql("PRAGMA foreign_key_check").first():
            raise RuntimeError("source_identity_migration_foreign_key_violation")
        return
    op.drop_constraint("uq_projects_owner_name_key", "projects", type_="unique")
    op.drop_constraint("uq_projects_owner_search_url", "projects", type_="unique")
    op.drop_constraint("cars_canonical_encar_id_key", "cars", type_="unique")
    op.alter_column("cars", "canonical_encar_id", existing_type=sa.String(40), nullable=True)
    op.alter_column("cars", "source_listing_id", existing_type=sa.String(128), server_default=None)
    op.drop_constraint("car_aliases_alias_id_key", "car_aliases", type_="unique")
    op.drop_constraint("car_aliases_car_id_fkey", "car_aliases", type_="foreignkey")
    for table, constraints in SOURCE_SCOPED_UNIQUES.items():
        for name, columns in constraints:
            op.create_unique_constraint(name, table, columns)
    for table, constraints in SOURCE_IDENTITY_CHECKS.items():
        for name, sql in constraints:
            op.create_check_constraint(name, table, sql)
    op.create_foreign_key(
        "fk_car_aliases_car_source", "car_aliases", "cars",
        ["car_id", "source_key"], ["id", "source_key"], ondelete="CASCADE"
    )


def downgrade() -> None:
    # Restoring old global constraints can merge or invalidate auction records.
    raise RuntimeError("source_identity_downgrade_requires_verified_pre_migration_backup")
