import runpy
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateIndex, CreateTable

from app import auction_migration, sqlite_migrations
from app.database import create_database_engine
from app.desktop_backup import export_backup, restore_backup, validate_backup
from app.models import AuctionObservation, AuctionRound, AuctionScheduleVersion, Car, ProjectCar
from app.postgres_converter import convert_to_backup
from test_auction_rounds import NOW, observe, schedule
from test_source_identity_migration import legacy_database


TABLES = ("auction_rounds", "auction_schedule_versions", "auction_observations")


def v10_database(tmp_path, monkeypatch):
    engine = legacy_database(tmp_path)
    with monkeypatch.context() as context:
        context.setattr(sqlite_migrations, "MIGRATIONS", sqlite_migrations.MIGRATIONS[:10])
        sqlite_migrations.migrate_sqlite(engine)
    with engine.begin() as connection:
        # legacy_database uses the latest ORM for unrelated tables. A deployed
        # v10 database has none of these new tables.
        for name in reversed(TABLES):
            connection.exec_driver_sql(f'DROP TABLE IF EXISTS "{name}"')
    return engine


def legacy_rows(engine):
    from app.database import Base

    with engine.connect() as connection:
        # Compare logical values, not physical column order or SQLite's optional
        # datetime fractional suffix/JSON-null representation after conversion.
        return {name: connection.execute(select(*[column for column in Base.metadata.tables[name].columns
            if column.name not in ('search_config', 'search_identity')]).order_by(
            *Base.metadata.tables[name].primary_key.columns)).mappings().all()
            for name in ("cars", "car_snapshots", "price_history", "projects", "project_cars", "car_aliases")}


def test_v11_is_additive_idempotent_and_does_not_invent_encar_rounds(tmp_path, monkeypatch):
    engine = v10_database(tmp_path, monkeypatch)
    before = legacy_rows(engine)
    assert sqlite_migrations.migrate_sqlite(engine) == sqlite_migrations.MIGRATIONS[-1].version
    assert sqlite_migrations.migrate_sqlite(engine) == sqlite_migrations.MIGRATIONS[-1].version
    assert legacy_rows(engine) == before
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        for name in TABLES:
            assert connection.exec_driver_sql(f'SELECT COUNT(*) FROM "{name}"').scalar_one() == 0


def test_failed_v11_rolls_back_all_new_tables_and_keeps_v10_data(tmp_path, monkeypatch):
    engine = v10_database(tmp_path, monkeypatch)
    before = legacy_rows(engine)
    original = auction_migration.migrate_auctions_v1

    def failed(connection):
        original(connection)
        raise RuntimeError("simulated_auction_migration_failure")

    with monkeypatch.context() as context:
        context.setattr(auction_migration, "migrate_auctions_v1", failed)
        with pytest.raises(RuntimeError, match="simulated_auction_migration_failure"):
            sqlite_migrations.migrate_sqlite(engine)
    assert legacy_rows(engine) == before
    with engine.connect() as connection:
        assert not set(TABLES) & set(inspect(connection).get_table_names())
        assert connection.exec_driver_sql("SELECT MAX(version) FROM desktop_schema_migrations").scalar_one() == 10
    assert sqlite_migrations.migrate_sqlite(engine) == sqlite_migrations.MIGRATIONS[-1].version


def test_alembic_additive_upgrade_and_guarded_downgrade(tmp_path, monkeypatch):
    engine = v10_database(tmp_path, monkeypatch)
    before = legacy_rows(engine)
    migration = runpy.run_path(str(Path(__file__).parents[1] / "alembic/versions/0020_auction_rounds.py"))
    assert migration["down_revision"] == "0019"
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        migration["upgrade"]()
    assert legacy_rows(engine) == before
    with engine.connect() as connection:
        assert set(TABLES) <= set(inspect(connection).get_table_names())
    with pytest.raises(RuntimeError, match="verified_pre_migration_backup"):
        migration["downgrade"]()


def test_frozen_schema_matches_orm_and_compiles_for_postgres():
    from sqlalchemy import create_engine
    from app.database import Base

    frozen = create_engine("sqlite://")
    mapped = create_engine("sqlite://")
    with frozen.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE cars (id INTEGER PRIMARY KEY)")
        auction_migration.migrate_auctions_v1(connection)
    Base.metadata.create_all(mapped)
    for name in TABLES:
        left, right = inspect(frozen), inspect(mapped)
        assert [{**column, "type": str(column["type"])} for column in left.get_columns(name)] == [
            {**column, "type": str(column["type"])} for column in right.get_columns(name)]
        assert left.get_foreign_keys(name) == right.get_foreign_keys(name)
        assert left.get_check_constraints(name) == right.get_check_constraints(name)
        assert left.get_unique_constraints(name) == right.get_unique_constraints(name)
    dialect = postgresql.dialect()
    tables = auction_migration.auction_tables_v1()
    ddl = "\n".join(str(CreateTable(table).compile(dialect=dialect)) for table in tables)
    assert "TIMESTAMP WITH TIME ZONE" in ddl
    assert "fk_auction_observation_schedule" in ddl
    assert "ON DELETE CASCADE" in ddl
    index = next(iter(tables[0].indexes))
    assert "WHERE is_current IS true" in str(CreateIndex(index).compile(dialect=dialect))


@pytest.mark.parametrize("mode", ["backup", "converter"])
def test_rounds_observations_and_event_ids_survive_backup_and_read_only_conversion(tmp_path, monkeypatch, mode):
    engine = v10_database(tmp_path, monkeypatch)
    sqlite_migrations.migrate_sqlite(engine)
    with Session(engine, expire_on_commit=False) as db:
        db.add_all([Car(id=1, source_key="copart", source_listing_id="123", url="https://copart.example.test/123"),
                    Car(id=2, source_key="iaa", source_listing_id="123", url="https://iaa.example.test/123")])
        db.flush()
        for car_id in (1, 2):
            observe(db, car_id=car_id, sale_id="sale-A", schedule=schedule())
            observe(db, "check-2", car_id=car_id, minute=1, sale_id="sale-A", schedule=schedule("2026-10-11"))
            observe(db, "check-3", car_id=car_id, minute=2, sale_id="sale-B", schedule=schedule("2026-10-12"))
        db.commit()
    before = legacy_rows(engine)
    with engine.connect() as connection:
        records = {table.name: connection.execute(select(table).order_by(table.c.id)).all()
                   for table in auction_migration.auction_tables_v1()}
    storage = tmp_path / "storage"
    storage.mkdir()
    archive = tmp_path / "rounds.searchcar-backup"
    if mode == "backup":
        export_backup(Path(engine.url.database), storage, archive)
    else:
        report = convert_to_backup(str(engine.url), storage, archive)
        assert report.source_counts == report.target_counts
    assert legacy_rows(engine) == before
    validation = validate_backup(archive)
    assert validation.schema_version == sqlite_migrations.MIGRATIONS[-1].version
    assert [validation.table_counts[name] for name in TABLES] == [4, 6, 6]
    target = tmp_path / "restored.sqlite3"
    restore_backup(archive, target, tmp_path / "restored-storage", tmp_path / "backups")
    restored = create_database_engine(f"sqlite+pysqlite:///{target}")
    assert sqlite_migrations.migrate_sqlite(restored) == sqlite_migrations.MIGRATIONS[-1].version
    assert legacy_rows(restored) == before
    with restored.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        for table in auction_migration.auction_tables_v1():
            assert connection.execute(select(table).order_by(table.c.id)).all() == records[table.name]
    with Session(restored) as db:
        assert db.get(Car, 29).comment == "Keep me"
        assert db.get(ProjectCar, (17, 29)).favorite is True


def test_database_guards_current_round_observation_identity_and_cross_round_links(tmp_path):
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'guards.sqlite3'}")
    sqlite_migrations.migrate_sqlite(engine)
    with Session(engine, expire_on_commit=False) as db:
        db.add(Car(id=1, source_key="copart", source_listing_id="123", url="https://copart.example.test/123"))
        db.flush()
        first = observe(db, sale_id="sale-A", schedule=schedule())
        second = observe(db, "check-2", minute=1, sale_id="sale-B", schedule=schedule())
        db.commit()
        bad_records = [
            AuctionRound(id="wrong-current", car_id=1, evidence_kind="PROVISIONAL", is_current=True,
                         created_at=NOW, last_observed_at=NOW),
            AuctionObservation(id="wrong-round-version", car_id=1, round_id=second.round_id,
                               schedule_version_id=first.schedule_version_id, observation_key="wrong",
                               observed_at=NOW, applied=True, payload={}),
            AuctionObservation(id="wrong-car", car_id=999, round_id=second.round_id,
                               observation_key="wrong", observed_at=NOW, applied=True, payload={}),
            AuctionObservation(id="duplicate-check", car_id=1, round_id=second.round_id,
                               observation_key="check-1", observed_at=NOW, applied=True, payload={}),
        ]
        for bad in bad_records:
            with pytest.raises(IntegrityError), db.begin_nested():
                db.add(bad)
                db.flush()
        # The service participates in its caller's transaction, not its own commit.
        observe(db, "rolled-back", minute=2, sale_id="sale-B", schedule=schedule("2026-10-12"))
        db.rollback()
        assert db.scalar(select(AuctionObservation).where(AuctionObservation.observation_key == "rolled-back")) is None
        assert len(list(db.scalars(select(AuctionScheduleVersion)))) == 2
        db.delete(db.get(Car, 1))
        db.commit()
        for model in (AuctionRound, AuctionScheduleVersion, AuctionObservation):
            assert list(db.scalars(select(model))) == []
