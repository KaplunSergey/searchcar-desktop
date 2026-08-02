from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any

from sqlalchemy import MetaData, create_engine, func, inspect, select
from sqlalchemy.engine import Connection, Engine

from .desktop_backup import (
    BackupValidation,
    export_backup,
    prepare_portable_database,
    sha256_file,
    verify_sqlite,
)


CHUNK_SIZE = 500
EXCLUDED_TABLES = {"auth_sessions", "desktop_schema_migrations"}
REQUIRED_SOURCE_TABLES = {"users", "projects", "cars", "project_cars"}


class ConversionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConversionReport:
    source_dialect: str
    backup: BackupValidation
    source_counts: dict[str, int]
    target_counts: dict[str, int]
    skipped_counts: dict[str, int]
    storage_files: int
    storage_bytes: int

    def as_dict(self) -> dict:
        return {
            "status": "converted",
            "source_dialect": self.source_dialect,
            "backup": self.backup.as_dict(),
            "source_counts": self.source_counts,
            "target_counts": self.target_counts,
            "skipped_counts": self.skipped_counts,
            "storage_files": self.storage_files,
            "storage_bytes": self.storage_bytes,
        }


def _source_relative_path(value: str, source_storage_root: Path) -> str | None:
    raw = value.strip().replace("\\", "/")
    if raw.startswith("/storage/"):
        return raw.removeprefix("/storage/")

    root = str(source_storage_root.expanduser().resolve()).replace("\\", "/").rstrip("/")
    if raw == root:
        return ""
    if raw.startswith(f"{root}/"):
        return raw[len(root) + 1 :]

    windows_value = PureWindowsPath(value)
    windows_root = PureWindowsPath(str(source_storage_root))
    if windows_value.drive and windows_root.drive:
        value_parts = [part.casefold() for part in windows_value.parts]
        root_parts = [part.casefold() for part in windows_root.parts]
        if value_parts[: len(root_parts)] == root_parts:
            return Path(*windows_value.parts[len(root_parts) :]).as_posix()
    return None


def _normalize_value(value: Any, source_storage_root: Path) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, dict):
        return {
            key: _normalize_value(item, source_storage_root)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize_value(item, source_storage_root) for item in value]
    if isinstance(value, tuple):
        return tuple(_normalize_value(item, source_storage_root) for item in value)
    if isinstance(value, str):
        relative = _source_relative_path(value, source_storage_root)
        return relative if relative is not None else value
    return value


def _open_consistent_snapshot(engine: Engine) -> tuple[Connection, Any]:
    connection = engine.connect()
    if engine.dialect.name == "postgresql":
        connection = connection.execution_options(isolation_level="REPEATABLE READ")
        transaction = connection.begin()
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
    elif engine.dialect.name == "sqlite":
        transaction = connection.begin()
        connection.exec_driver_sql("PRAGMA query_only=ON")
    else:
        connection.close()
        raise ConversionError(f"unsupported_source_database:{engine.dialect.name}")
    return connection, transaction


def _copy_storage(source_root: Path, target_root: Path) -> tuple[int, int]:
    source_cars = source_root / "cars"
    target_cars = target_root / "cars"
    target_cars.mkdir(parents=True, exist_ok=True)
    if not source_cars.exists():
        return 0, 0

    copied_files = 0
    copied_bytes = 0
    for source in sorted(source_cars.rglob("*")):
        if source.is_symlink():
            raise ConversionError(f"source_storage_symlink_not_allowed:{source}")
        if not source.is_file():
            continue
        relative = source.relative_to(source_cars)
        target = target_cars / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if sha256_file(source) != sha256_file(target):
            raise ConversionError(f"storage_checksum_mismatch:{relative.as_posix()}")
        copied_files += 1
        copied_bytes += target.stat().st_size
    return copied_files, copied_bytes


def _copy_database(
    source_connection: Connection,
    target_engine: Engine,
    source_storage_root: Path,
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    from . import models  # noqa: F401
    from .database import Base

    source_inspector = inspect(source_connection)
    available_tables = set(source_inspector.get_table_names())
    missing_required = REQUIRED_SOURCE_TABLES - available_tables
    if missing_required:
        raise ConversionError(
            "source_tables_missing:" + ",".join(sorted(missing_required))
        )
    source_metadata = MetaData()
    tables_to_copy = [
        table.name
        for table in Base.metadata.sorted_tables
        if table.name in available_tables
    ]
    source_metadata.reflect(
        bind=source_connection,
        only=tables_to_copy,
        resolve_fks=False,
    )
    source_tables = source_metadata.tables

    source_counts: dict[str, int] = {}
    target_counts: dict[str, int] = {}
    skipped_counts: dict[str, int] = {}
    with target_engine.begin() as target_connection:
        for target_table in Base.metadata.sorted_tables:
            name = target_table.name
            source_table = source_tables.get(name)
            if source_table is None:
                continue
            source_count = int(
                source_connection.execute(
                    select(func.count()).select_from(source_table)
                ).scalar_one()
            )
            if name in EXCLUDED_TABLES:
                skipped_counts[name] = source_count
                continue

            source_counts[name] = source_count
            shared_columns = [
                column.name
                for column in target_table.columns
                if column.name in source_table.c
            ]
            statement = select(*(source_table.c[column] for column in shared_columns))
            result = source_connection.execution_options(stream_results=True).execute(
                statement
            )
            while True:
                rows = result.fetchmany(CHUNK_SIZE)
                if not rows:
                    break
                payload = [
                    {
                        column: _normalize_value(row._mapping[column], source_storage_root)
                        for column in shared_columns
                    }
                    for row in rows
                ]
                target_connection.execute(target_table.insert(), payload)

    with target_engine.connect() as connection:
        for table_name in source_counts:
            target_table = Base.metadata.tables[table_name]
            target_counts[table_name] = int(
                connection.execute(
                    select(func.count()).select_from(target_table)
                ).scalar_one()
            )
    if source_counts != target_counts:
        raise ConversionError("database_row_counts_mismatch")
    return source_counts, target_counts, skipped_counts


def convert_to_backup(
    source_database_url: str,
    source_storage_root: Path,
    destination: Path,
    *,
    app_version: str = "0.1.0",
) -> ConversionReport:
    """Create a verified desktop backup without mutating the source database."""

    from .database import create_database_engine
    from .sqlite_migrations import migrate_sqlite

    source_storage_root = source_storage_root.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not source_storage_root.is_dir():
        raise ConversionError(f"source_storage_not_found:{source_storage_root}")
    if destination == source_storage_root or source_storage_root in destination.parents:
        raise ConversionError("migration_destination_overlaps_source_storage")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_engine = create_engine(source_database_url, pool_pre_ping=True)
    source_connection: Connection | None = None
    source_transaction = None
    try:
        source_connection, source_transaction = _open_consistent_snapshot(source_engine)
        with tempfile.TemporaryDirectory(
            prefix="searchcar-convert-",
            dir=destination.parent,
        ) as temporary_name:
            temporary = Path(temporary_name)
            target_database = temporary / "data" / "searchcar.sqlite3"
            target_storage = temporary / "storage"
            target_database.parent.mkdir(parents=True, exist_ok=True)
            target_storage.mkdir(parents=True, exist_ok=True)
            target_engine = create_database_engine(
                f"sqlite+pysqlite:///{target_database.as_posix()}"
            )
            try:
                migrate_sqlite(target_engine)
                source_counts, target_counts, skipped_counts = _copy_database(
                    source_connection,
                    target_engine,
                    source_storage_root,
                )
            finally:
                target_engine.dispose()
            prepare_portable_database(target_database)
            verify_sqlite(target_database)
            storage_files, storage_bytes = _copy_storage(
                source_storage_root,
                target_storage,
            )
            backup = export_backup(
                target_database,
                target_storage,
                destination,
                app_version=app_version,
            )
        return ConversionReport(
            source_dialect=source_engine.dialect.name,
            backup=backup,
            source_counts=source_counts,
            target_counts=target_counts,
            skipped_counts=skipped_counts,
            storage_files=storage_files,
            storage_bytes=storage_bytes,
        )
    finally:
        if source_transaction is not None and source_transaction.is_active:
            source_transaction.rollback()
        if source_connection is not None:
            source_connection.close()
        source_engine.dispose()
