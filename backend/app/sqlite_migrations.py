from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import Callable
from uuid import uuid4

from sqlalchemy import CheckConstraint, Connection, Engine, ForeignKeyConstraint, MetaData, Table, UniqueConstraint, inspect, text
from sqlalchemy.schema import CreateTable


MigrationAction = Callable[[Connection], None]


@dataclass(frozen=True)
class SQLiteMigration:
    version: int
    name: str
    action: MigrationAction

    @property
    def checksum(self) -> str:
        identity = f"searchcar-sqlite:{self.version}:{self.name}:v1"
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _baseline(connection: Connection) -> None:
    # Importing models registers every mapped table on Base.metadata.
    from . import models  # noqa: F401
    from .database import Base

    Base.metadata.create_all(connection)


def _add_column_if_missing(
    connection: Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    columns = {item["name"] for item in inspect(connection).get_columns(table)}
    if column not in columns:
        connection.exec_driver_sql(
            f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}'
        )


def _durable_scan_queue(connection: Connection) -> None:
    _add_column_if_missing(connection, "scan_runs", "worker_id", "VARCHAR(64)")
    _add_column_if_missing(
        connection,
        "scan_runs",
        "attempt_count",
        "INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(connection, "scan_runs", "started_at", "DATETIME")
    _add_column_if_missing(connection, "scan_runs", "heartbeat_at", "DATETIME")
    _add_column_if_missing(connection, "scan_runs", "finished_at", "DATETIME")

    # A database created by the early desktop prototype may have been closed
    # while a run was active. Resolve that state before installing the unique
    # single-worker guard.
    connection.execute(
        text(
            """
            UPDATE scan_runs
            SET status = 'INTERRUPTED',
                worker_id = NULL,
                finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP),
                error = COALESCE(error, 'Application stopped before the scan finished')
            WHERE status IN ('RUNNING', 'CANCEL_REQUESTED')
            """
        )
    )
    connection.execute(
        text(
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
    connection.exec_driver_sql(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_scan_runs_single_active_worker
        ON scan_runs ((1))
        WHERE status IN ('RUNNING', 'CANCEL_REQUESTED')
        """
    )
    connection.exec_driver_sql(
        """
        CREATE INDEX IF NOT EXISTS ix_scan_runs_queue_order
        ON scan_runs (status, created_at, id)
        """
    )
    connection.exec_driver_sql(
        """
        CREATE INDEX IF NOT EXISTS ix_scan_runs_worker_id
        ON scan_runs (worker_id)
        """
    )


def _desktop_scheduler_controls(connection: Connection) -> None:
    _add_column_if_missing(
        connection,
        "scheduler_settings",
        "paused",
        "BOOLEAN NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        connection,
        "scheduler_settings",
        "catch_up_enabled",
        "BOOLEAN NOT NULL DEFAULT 1",
    )


def _scheduler_completion_checkpoint(connection: Connection) -> None:
    _add_column_if_missing(
        connection,
        "scheduler_settings",
        "last_completed_run_at",
        "DATETIME",
    )


def _project_price_filters(connection: Connection) -> None:
    _add_column_if_missing(
        connection, "projects", "price_filter_mode", "VARCHAR(16) NOT NULL DEFAULT 'LINK'"
    )


def _search_performance_mode(connection: Connection) -> None:
    _add_column_if_missing(
        connection,
        "scheduler_settings",
        "performance_mode",
        "VARCHAR(16) NOT NULL DEFAULT 'FAST'",
    )
    _add_column_if_missing(connection, "projects", "price_min_krw", "BIGINT")
    _add_column_if_missing(connection, "projects", "price_max_krw", "BIGINT")
    _add_column_if_missing(
        connection, "projects", "price_filter_revision", "INTEGER NOT NULL DEFAULT 1"
    )
    _add_column_if_missing(
        connection,
        "projects",
        "price_filter_baseline_revision",
        "INTEGER NOT NULL DEFAULT 0",
    )


def _project_car_new_status(connection: Connection) -> None:
    _add_column_if_missing(
        connection,
        "project_cars",
        "is_new",
        "BOOLEAN NOT NULL DEFAULT 0",
    )
    connection.execute(
        text(
            """
            UPDATE project_cars
            SET is_new = 1
            WHERE car_id IN (SELECT id FROM cars WHERE status = 'NEW')
            """
        )
    )


def _source_identity(connection: Connection) -> None:
    # Additive stage only: retain the legacy uniqueness and all dependent IDs.
    for table in ("projects", "cars", "car_aliases"):
        _add_column_if_missing(
            connection, table, "source_key", "VARCHAR(32) NOT NULL DEFAULT 'encar'"
        )
    _add_column_if_missing(
        connection, "cars", "source_listing_id", "VARCHAR(128) NOT NULL DEFAULT ''"
    )
    connection.execute(
        text("""
            UPDATE cars SET source_listing_id = canonical_encar_id
            WHERE source_key = 'encar' AND source_listing_id = ''
        """)
    )


# Frozen migration-9 rules; do not derive historical migrations from ORM models.
SOURCE_SCOPED_UNIQUES = {
    "projects": (("uq_projects_owner_source_name_key", ("owner_id", "source_key", "name_key")),
                 ("uq_projects_owner_source_search_url", ("owner_id", "source_key", "search_url"))),
    "cars": (("uq_cars_source_listing", ("source_key", "source_listing_id")),
             ("uq_cars_id_source", ("id", "source_key"))),
    "car_aliases": (("uq_car_aliases_source_alias", ("source_key", "alias_id")),),
}
SOURCE_IDENTITY_CHECKS = {
    "projects": (("ck_projects_source_key", "length(source_key) > 0"),),
    "cars": (("ck_cars_source_identity", "length(source_key) > 0 AND length(source_listing_id) > 0"),
             ("ck_cars_legacy_encar_identity", "(source_key = 'encar' AND canonical_encar_id IS NOT NULL AND canonical_encar_id = source_listing_id) OR (source_key != 'encar' AND canonical_encar_id IS NULL)")),
    "car_aliases": (("ck_car_aliases_source_key", "length(source_key) > 0"),),
}
LEGACY_IDENTITY_UNIQUES = {
    "projects": {("owner_id", "name_key"), ("owner_id", "search_url")},
    "cars": {("canonical_encar_id",)},
    "car_aliases": {("alias_id",)},
}


def _rebuild_source_table(connection: Connection, name: str) -> None:
    metadata = MetaData()
    original = Table(name, metadata, autoload_with=connection)
    replacement = original.to_metadata(metadata, name=f"{name}__source_identity")
    for constraint in list(replacement.constraints):
        columns = tuple(column.name for column in constraint.columns)
        if isinstance(constraint, UniqueConstraint) and columns in LEGACY_IDENTITY_UNIQUES[name]:
            replacement.constraints.remove(constraint)
        elif name == "car_aliases" and isinstance(constraint, ForeignKeyConstraint) and columns in {("car_id",), ("car_id", "source_key")}:
            replacement.constraints.remove(constraint)
    existing_names = {constraint.name for constraint in replacement.constraints}
    for constraint_name, columns in SOURCE_SCOPED_UNIQUES[name]:
        if constraint_name not in existing_names:
            replacement.append_constraint(UniqueConstraint(*columns, name=constraint_name))
    for constraint_name, sql in SOURCE_IDENTITY_CHECKS[name]:
        if constraint_name not in existing_names:
            replacement.append_constraint(CheckConstraint(sql, name=constraint_name))
    if name == "cars":
        replacement.c.canonical_encar_id.nullable = True
        replacement.c.source_listing_id.server_default = None
    if name == "car_aliases":
        replacement.append_constraint(ForeignKeyConstraint(
            ["car_id", "source_key"], ["cars.id", "cars.source_key"],
            ondelete="CASCADE", name="fk_car_aliases_car_source"
        ))
    # Preserve explicit indexes/triggers, except obsolete global unique indexes.
    objects = connection.execute(text(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE tbl_name = :table AND type IN ('index', 'trigger') AND sql IS NOT NULL"
    ), {"table": name}).all()
    obsolete_indexes = set()
    quote = connection.dialect.identifier_preparer.quote_identifier
    for index in connection.exec_driver_sql(f"PRAGMA index_list({quote(name)})"):
        columns = tuple(row[2] for row in connection.exec_driver_sql(f"PRAGMA index_info({quote(index[1])})"))
        if index[2] and columns in LEGACY_IDENTITY_UNIQUES[name]:
            obsolete_indexes.add(index[1])
    connection.execute(CreateTable(replacement))
    columns = ", ".join(quote(column.name) for column in original.columns)
    connection.exec_driver_sql(
        f"INSERT INTO {quote(replacement.name)} ({columns}) SELECT {columns} FROM {quote(name)}"
    )
    connection.exec_driver_sql(f"DROP TABLE {quote(name)}")
    connection.exec_driver_sql(f"ALTER TABLE {quote(replacement.name)} RENAME TO {quote(name)}")
    for kind, object_name, sql in objects:
        if kind != "index" or object_name not in obsolete_indexes:
            connection.exec_driver_sql(sql)


def _scoped_source_identity(connection: Connection) -> None:
    if connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one():
        raise RuntimeError("source_rebuild_requires_foreign_keys_off_before_transaction")
    for table in ("projects", "cars", "car_aliases"):
        _rebuild_source_table(connection, table)


def _backup_source_identity(connection: Connection, engine: Engine, *, label='source-identity') -> None:
    database = engine.url.database
    if not database or database == ":memory:" or not inspect(connection).has_table("cars"):
        return
    from .desktop_backup import _online_sqlite_backup

    path = Path(database).resolve()
    size = connection.exec_driver_sql("PRAGMA page_count").scalar_one() * connection.exec_driver_sql("PRAGMA page_size").scalar_one()
    if shutil.disk_usage(path.parent).free < size * 4 + 1024 * 1024:
        raise RuntimeError("source_identity_migration_insufficient_disk_space")
    backup = path.with_name(f"{path.stem}.pre-{label}-{uuid4().hex}.sqlite3")
    backup.touch(mode=0o600, exist_ok=False)
    # A separate reader sees the committed pre-migration state under our write lock.
    _online_sqlite_backup(path, backup)
    with closing(sqlite3.connect(backup)) as saved:
        if saved.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("source_identity_migration_backup_invalid")


def _typed_money_values(connection: Connection) -> None:
    from .money_migration import migrate_money_v1

    migrate_money_v1(connection)


def _auction_rounds(connection: Connection) -> None:
    from .auction_migration import migrate_auctions_v1

    migrate_auctions_v1(connection)


def _project_search_config(connection: Connection) -> None:
    from .search_identity import url_identity_v1
    if connection.exec_driver_sql('PRAGMA foreign_keys').scalar_one():
        raise RuntimeError('project_search_rebuild_requires_foreign_keys_off_before_transaction')
    _add_column_if_missing(connection, 'projects', 'search_config', 'JSON')
    _add_column_if_missing(connection, 'projects', 'search_identity', "VARCHAR(64) NOT NULL DEFAULT ''")
    for row in connection.execute(text("SELECT id, search_url FROM projects WHERE search_identity = ''")):
        connection.execute(text('UPDATE projects SET search_identity=:identity WHERE id=:id'),
                           {'id': row.id, 'identity': url_identity_v1(row.search_url)})
    metadata = MetaData()
    original = Table('projects', metadata, autoload_with=connection)
    replacement = original.to_metadata(metadata, name='projects__search_config')
    obsolete_columns = ('owner_id', 'source_key', 'search_url')
    for constraint in list(replacement.constraints):
        if isinstance(constraint, UniqueConstraint) and tuple(c.name for c in constraint.columns) == obsolete_columns:
            replacement.constraints.remove(constraint)
    if not any(c.name == 'uq_projects_owner_source_search_identity' for c in replacement.constraints):
        replacement.append_constraint(UniqueConstraint('owner_id', 'source_key', 'search_identity', name='uq_projects_owner_source_search_identity'))
    replacement.c.search_identity.server_default = None
    objects = connection.execute(text("SELECT type, name, sql FROM sqlite_master WHERE tbl_name='projects' AND type IN ('index','trigger') AND sql IS NOT NULL")).all()
    quote = connection.dialect.identifier_preparer.quote_identifier
    obsolete_indexes = set()
    for index in connection.exec_driver_sql('PRAGMA index_list("projects")'):
        columns = tuple(row[2] for row in connection.exec_driver_sql(f'PRAGMA index_info({quote(index[1])})'))
        if index[2] and columns == obsolete_columns:
            obsolete_indexes.add(index[1])
    connection.execute(CreateTable(replacement))
    columns = ', '.join(quote(c.name) for c in original.columns)
    connection.exec_driver_sql(f'INSERT INTO projects__search_config ({columns}) SELECT {columns} FROM projects')
    connection.exec_driver_sql('DROP TABLE projects')
    connection.exec_driver_sql('ALTER TABLE projects__search_config RENAME TO projects')
    for kind, name, sql in objects:
        if kind != 'index' or name not in obsolete_indexes:
            connection.exec_driver_sql(sql)


MIGRATIONS = (
    SQLiteMigration(1, "current_web_schema_baseline", _baseline),
    SQLiteMigration(2, "durable_single_worker_scan_queue", _durable_scan_queue),
    SQLiteMigration(3, "desktop_scheduler_controls", _desktop_scheduler_controls),
    SQLiteMigration(4, "scheduler_completion_checkpoint", _scheduler_completion_checkpoint),
    SQLiteMigration(5, "project_price_filters", _project_price_filters),
    SQLiteMigration(6, "search_performance_mode", _search_performance_mode),
    SQLiteMigration(7, "project_car_new_status", _project_car_new_status),
    SQLiteMigration(8, "additive_source_identity", _source_identity),
    SQLiteMigration(9, "source_scoped_identity", _scoped_source_identity),
    SQLiteMigration(10, "typed_money_values", _typed_money_values),
    SQLiteMigration(11, "auction_rounds_and_schedule_history", _auction_rounds),
    SQLiteMigration(12, "project_public_search_config", _project_search_config),
)


def _create_migration_table(connection: Connection) -> None:
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS desktop_schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def migrate_sqlite(engine: Engine) -> int:
    """Apply desktop migrations under a SQLite write lock.

    The write lock makes concurrent application starts serialize before any
    schema changes. Applied migration identities are immutable and checked on
    every launch.
    """

    if engine.dialect.name != "sqlite":
        raise ValueError("desktop_sqlite_migrations_require_sqlite")

    with engine.connect() as connection:
        applied_versions = set(connection.execute(text('SELECT version FROM desktop_schema_migrations')).scalars()) if inspect(connection).has_table('desktop_schema_migrations') else set()
        rebuild_versions = {9, 12} & {migration.version for migration in MIGRATIONS}
        needs_rebuild = bool(rebuild_versions - applied_versions)
        connection.rollback()
        if needs_rebuild:
            # SQLite ignores this PRAGMA inside a transaction. Disable only on
            # this connection, before BEGIN; validate all FKs before committing.
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            _create_migration_table(connection)
            applied = {
                row.version: row
                for row in connection.execute(
                    text(
                        "SELECT version, name, checksum "
                        "FROM desktop_schema_migrations ORDER BY version"
                    )
                )
            }
            if needs_rebuild and rebuild_versions - applied.keys():
                _backup_source_identity(connection, engine, label='source-identity' if 9 not in applied else 'project-search-config')
            for migration in MIGRATIONS:
                existing = applied.get(migration.version)
                if existing:
                    if (
                        existing.name != migration.name
                        or existing.checksum != migration.checksum
                    ):
                        raise RuntimeError(
                            f"sqlite_migration_checksum_mismatch:{migration.version}"
                        )
                    continue
                migration.action(connection)
                connection.execute(
                    text(
                        """
                        INSERT INTO desktop_schema_migrations
                            (version, name, checksum, applied_at)
                        VALUES (:version, :name, :checksum, CURRENT_TIMESTAMP)
                        """
                    ),
                    {
                        "version": migration.version,
                        "name": migration.name,
                        "checksum": migration.checksum,
                    },
                )
            if needs_rebuild and connection.exec_driver_sql("PRAGMA foreign_key_check").first():
                raise RuntimeError("source_identity_migration_foreign_key_violation")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            if needs_rebuild:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    return MIGRATIONS[-1].version


def sqlite_schema_version(engine: Engine) -> int:
    if engine.dialect.name != "sqlite":
        raise ValueError("desktop_sqlite_migrations_require_sqlite")
    with engine.connect() as connection:
        if not inspect(connection).has_table("desktop_schema_migrations"):
            return 0
        return int(
            connection.execute(
                text("SELECT COALESCE(MAX(version), 0) FROM desktop_schema_migrations")
            ).scalar_one()
        )
