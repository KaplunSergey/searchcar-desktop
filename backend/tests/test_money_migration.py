import runpy
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app import sqlite_migrations
from app.database import create_database_engine
from app.desktop_backup import export_backup, restore_backup
from app.models import Car, CarSnapshot, PriceHistory
from app.money import money_values_out
from test_source_identity_migration import legacy_database
from test_money_values import quote


def old_database(tmp_path, monkeypatch):
    engine = legacy_database(tmp_path)
    with monkeypatch.context() as context:
        context.setattr(sqlite_migrations, "MIGRATIONS", sqlite_migrations.MIGRATIONS[:9])
        sqlite_migrations.migrate_sqlite(engine)
    # A real v9 DB predates the new table (latest ORM baseline may create it).
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE IF EXISTS car_money_values")
    with Session(engine) as db:
        db.add_all([
            PriceHistory(id=71, car_id=29, payload={"price_krw": 350000, "offer_type": "RENT"}),
            PriceHistory(id=72, car_id=29, payload={"price_krw": 340000}),
            Car(id=30, canonical_encar_id="42791283", url="https://fem.encar.com/cars/detail/42791283",
                current_price=26000000, details={"offer_type": "SALE", "price_krw": 26000000}),
            Car(id=31, canonical_encar_id="42791284", url="https://fem.encar.com/cars/detail/42791284",
                current_price=480000, details={"offer_type": "LEASE", "price_krw": 480000}),
            Car(id=32, canonical_encar_id="42791285", url="https://fem.encar.com/cars/detail/42791285",
                current_price=None, details={"offer_type": "SALE"}),
        ])
        db.commit()
    return engine


def test_v10_backfills_minor_units_and_history_without_guessing_or_changing_ids(tmp_path, monkeypatch):
    engine = old_database(tmp_path, monkeypatch)
    # This is a historical v10 migration test, not a latest-version assertion.
    monkeypatch.setattr(sqlite_migrations, "MIGRATIONS", sqlite_migrations.MIGRATIONS[:10])
    assert sqlite_migrations.migrate_sqlite(engine) == 10
    with Session(engine) as db:
        assert money_values_out(db.get(Car, 29), db) == [quote(360000, "RENT_MONTHLY")]
        assert money_values_out(db.get(Car, 30), db) == [quote(26000000)]
        assert money_values_out(db.get(Car, 31), db) == [quote(480000, "LEASE_MONTHLY")]
        assert money_values_out(db.get(Car, 32), db) == [quote(None, state="UNKNOWN")]
        assert db.get(CarSnapshot, 51).payload["money_values"] == [quote(360000, "RENT_MONTHLY")]
        assert db.get(PriceHistory, 71).payload == {
            "price_krw": 350000, "offer_type": "RENT", "money_values": [quote(350000, "RENT_MONTHLY")]}
        # No historical offer type on a monthly listing: don't infer SALE or today's RENT.
        assert db.get(PriceHistory, 72).payload == {
            "price_krw": 340000, "money_values": [quote(None, "UNKNOWN", state="UNKNOWN")]}
        assert db.get(Car, 29).current_price == 360000
        assert db.get(Car, 29).comment == "Keep me"
        assert len(list(db.scalars(select(PriceHistory)))) == 2
    assert sqlite_migrations.migrate_sqlite(engine) == 10
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_money_migration_failure_rolls_back_schema_and_payloads(tmp_path, monkeypatch):
    engine = old_database(tmp_path, monkeypatch)
    with engine.begin() as connection:
        connection.exec_driver_sql("""CREATE TRIGGER reject_money_history BEFORE UPDATE ON price_history
            BEGIN SELECT RAISE(ABORT, 'money_test_failure'); END""")
    with pytest.raises(Exception, match="money_test_failure"):
        sqlite_migrations.migrate_sqlite(engine)
    with engine.connect() as connection:
        assert "car_money_values" not in inspect(connection).get_table_names()
        assert connection.exec_driver_sql("SELECT MAX(version) FROM desktop_schema_migrations").scalar_one() == 9
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    with Session(engine) as db:
        assert "money_values" not in db.get(CarSnapshot, 51).payload
        assert db.get(PriceHistory, 71).payload == {"price_krw": 350000, "offer_type": "RENT"}


def test_money_backup_restore_preserves_typed_quotes(tmp_path, monkeypatch):
    engine = old_database(tmp_path, monkeypatch)
    sqlite_migrations.migrate_sqlite(engine)
    storage = tmp_path / "storage"
    storage.mkdir()
    archive = tmp_path / "money.searchcar-backup"
    export_backup(Path(engine.url.database), storage, archive)
    target = tmp_path / "restored.sqlite3"
    restore_backup(archive, target, tmp_path / "restored-storage", tmp_path / "backups")
    restored = create_database_engine(f"sqlite+pysqlite:///{target}")
    with Session(restored) as db:
        assert money_values_out(db.get(Car, 29), db) == [quote(360000, "RENT_MONTHLY")]
        assert db.get(PriceHistory, 71).payload["money_values"] == [quote(350000, "RENT_MONTHLY")]


def test_alembic_money_upgrade_and_unsafe_downgrade_guard(tmp_path, monkeypatch):
    engine = old_database(tmp_path, monkeypatch)
    migration = runpy.run_path(str(Path(__file__).parents[1] / "alembic/versions/0019_typed_money_values.py"))
    assert migration["down_revision"] == "0018"
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration["upgrade"]()
    with Session(engine) as db:
        assert money_values_out(db.get(Car, 29), db) == [quote(360000, "RENT_MONTHLY")]
    with pytest.raises(RuntimeError, match="verified_pre_migration_backup"):
        migration["downgrade"]()


def test_converter_enriches_only_legacy_copy(tmp_path, monkeypatch):
    from app.postgres_converter import convert_to_backup
    engine = old_database(tmp_path, monkeypatch)
    storage = tmp_path / "source-storage"
    storage.mkdir()
    archive = tmp_path / "converted.searchcar-backup"
    report = convert_to_backup(str(engine.url), storage, archive)
    assert report.source_counts == report.target_counts
    with engine.connect() as connection:
        assert "car_money_values" not in inspect(connection).get_table_names()
    target = tmp_path / "converted.sqlite3"
    restore_backup(archive, target, tmp_path / "converted-storage", tmp_path / "backups")
    restored = create_database_engine(f"sqlite+pysqlite:///{target}")
    with Session(restored) as db:
        assert money_values_out(db.get(Car, 29), db) == [quote(360000, "RENT_MONTHLY")]
        assert db.get(PriceHistory, 71).payload["money_values"] == [quote(350000, "RENT_MONTHLY")]


def test_backfill_uses_authoritative_price_not_unverified_search_price(tmp_path, monkeypatch):
    engine = old_database(tmp_path, monkeypatch)
    with Session(engine) as db:
        db.get(Car, 30).details = {"price_krw": 28000000, "price_source": "SEARCH_LIST", "offer_type": "SALE"}
        db.get(Car, 32).details = {"price_krw": 28000000, "price_source": "SEARCH_LIST", "offer_type": "SALE"}
        db.commit()
    sqlite_migrations.migrate_sqlite(engine)
    with Session(engine) as db:
        assert money_values_out(db.get(Car, 30), db) == [quote(26000000)]
        assert money_values_out(db.get(Car, 32), db) == [quote(None, state="UNKNOWN")]


def test_mixed_currency_backup_retains_bids_round_and_multiple_quotes(tmp_path, monkeypatch):
    from app.money import store_money_values
    engine = old_database(tmp_path, monkeypatch)
    sqlite_migrations.migrate_sqlite(engine)
    values = [quote(12345, "CURRENT_BID", "USD", auction_round="round-1"),
              quote(None, "BUY_NOW", "USD", state="ON_REQUEST"),
              quote(1900000, "ESTIMATED_RETAIL", "USD")]
    with Session(engine) as db:
        car = Car(id=40, source_key="copart", source_listing_id="42688672", url="https://copart.example/lot/42688672")
        db.add(car)
        db.flush()
        store_money_values(db, car, values)
        db.commit()
    storage = tmp_path / "mixed-storage"
    storage.mkdir()
    archive = tmp_path / "mixed-money.searchcar-backup"
    export_backup(Path(engine.url.database), storage, archive)
    target = tmp_path / "mixed-restored.sqlite3"
    restore_backup(archive, target, tmp_path / "mixed-restored-storage", tmp_path / "backups")
    restored = create_database_engine(f"sqlite+pysqlite:///{target}")
    with Session(restored) as db:
        assert money_values_out(db.get(Car, 29), db) == [quote(360000, "RENT_MONTHLY")]
        assert money_values_out(db.get(Car, 40), db) == sorted(values, key=lambda value: (value["meaning"], value["currency"]))
        assert len(list(db.scalars(select(PriceHistory).where(PriceHistory.car_id == 40)))) == 3


def test_money_schema_compiles_for_postgres():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable
    from app.money_migration import money_tables_v1
    _cars, table, _history = money_tables_v1()
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "amount_minor BIGINT" in ddl
    assert "PRIMARY KEY (car_id, meaning, currency)" in ddl
    assert "ON DELETE CASCADE" in ddl
    assert "ck_money_amount_state" in ddl
