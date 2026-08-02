from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import Connection, Engine, inspect, text


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


MIGRATIONS = (
    SQLiteMigration(1, "current_web_schema_baseline", _baseline),
    SQLiteMigration(2, "durable_single_worker_scan_queue", _durable_scan_queue),
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
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
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
            connection.commit()
        except Exception:
            connection.rollback()
            raise
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
