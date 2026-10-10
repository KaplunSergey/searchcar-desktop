import json
from pathlib import Path
import runpy

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.database import Base, create_database_engine
from app.desktop_backup import export_backup, restore_backup, validate_backup
from app.models import Car, CarAlias, CarEvent, CarImage, CarSnapshot, PriceHistory, Project, ProjectCar, ProjectScanRun, ScanRun, ScheduledProject, SchedulerSetting, User, UserCarState
from app.postgres_converter import convert_to_backup
from app.sqlite_migrations import migrate_sqlite


@pytest.mark.parametrize("schema", ["fresh", "legacy"])
def test_scoped_identity_allows_the_same_ids_and_names_on_different_sites(tmp_path, schema):
    if schema == "legacy":
        engine = legacy_database(tmp_path)
    else:
        engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'scoped.sqlite3'}")
    migrate_sqlite(engine)
    with Session(engine) as db:
        if schema == "fresh":
            db.add(User(id=5, username="Owner", username_key="owner", password_hash="!test"))
            db.flush()
            db.add(Project(id=17, owner_id=5, name="Audi A5", name_key="audi a5",
                           search_url="https://www.encar.com/fc/fc_carsearchlist.do"))
            db.add(Car(id=29, canonical_encar_id="42688672",
                       url="https://fem.encar.com/cars/detail/42688672"))
            db.flush()
            db.add(CarAlias(car_id=29, alias_id="42812345"))
            db.flush()
        source_cars = {}
        for source in ("copart", "iaa"):
            db.add(Project(owner_id=5, source_key=source, name="Audi A5", name_key="audi a5",
                           search_url=f"https://{source}.example.test/search"))
            car = Car(source_key=source, source_listing_id="42688672",
                      url=f"https://{source}.example.test/lot/42688672")
            db.add(car)
            db.flush()
            assert car.canonical_encar_id is None  # No fabricated Encar namespace.
            source_cars[source] = car.id
            db.add(CarAlias(car_id=car.id, source_key=source, alias_id="42812345"))
            db.flush()
        db.commit()
        for duplicate in (
            Project(owner_id=5, source_key="copart", name="Audi A5", name_key="audi a5",
                    search_url="https://copart.example.test/other"),
            Car(source_key="copart", source_listing_id="42688672", url="https://copart.example.test/other"),
            CarAlias(source_key="copart", car_id=source_cars["copart"], alias_id="42812345"),
            Project(owner_id=5, source_key="copart", name="Other", name_key="other",
                    search_url="https://copart.example.test/search"),
        ):
            with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
                with db.begin_nested():
                    db.add(duplicate)
                    db.flush()
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1


def test_alias_cannot_belong_to_a_car_from_another_source(tmp_path):
    engine = legacy_database(tmp_path)
    migrate_sqlite(engine)
    with Session(engine) as db:
        with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
            db.add(CarAlias(car_id=29, source_key="copart", alias_id="99887766"))
            db.flush()


@pytest.mark.parametrize("fields", [
    {"source_listing_id": ""},
    {"source_listing_id": "different-id"},
    {"source_key": "copart", "source_listing_id": "42791283"},
])
def test_invalid_or_fabricated_source_identity_is_rejected(tmp_path, fields):
    engine = legacy_database(tmp_path)
    migrate_sqlite(engine)
    with Session(engine) as db:
        with pytest.raises(IntegrityError, match="CHECK constraint failed"):
            db.add(Car(canonical_encar_id="42791283",
                       url="https://fem.encar.com/cars/detail/42791283", **fields))
            db.flush()


def test_encar_lookup_does_not_resolve_an_alias_from_another_source(tmp_path):
    from app.main import lookup_car

    engine = legacy_database(tmp_path)
    migrate_sqlite(engine)
    with Session(engine) as db:
        project = Project(owner_id=5, source_key="copart", name="Auction", name_key="auction",
                          search_url="https://copart.example.test/search")
        car = Car(source_key="copart", source_listing_id="42791283",
                  url="https://copart.example.test/lot/42791283")
        db.add_all([project, car])
        db.flush()
        db.add(ProjectCar(project_id=project.id, car_id=car.id))
        db.add(CarAlias(source_key="copart", car_id=car.id, alias_id="99887766"))
        db.commit()
        assert lookup_car(q="99887766", current=db.get(User, 5), db=db)["found"] is False


def test_encar_storage_reuses_a_listing_for_two_projects_and_rejects_other_sources(tmp_path):
    from app.services import upsert_detail

    engine = legacy_database(tmp_path)
    migrate_sqlite(engine)
    with Session(engine) as db:
        project = Project(owner_id=5, name="Second", name_key="second",
                          search_url="https://www.encar.com/dc/dc_carsearchlist.do")
        auction = Project(owner_id=5, source_key="copart", name="Auction", name_key="auction",
                          search_url="https://copart.example.test/search")
        db.add_all([project, auction])
        db.flush()
        detail = {**db.get(Car, 29).details, "price_krw": 360000,
                  "price_source": "DETAIL_RENTAL_MONTHLY"}
        assert upsert_detail(db, project, detail).id == 29
        db.flush()
        assert db.get(ProjectCar, (17, 29)).favorite
        assert db.get(ProjectCar, (project.id, 29)) is not None
        with pytest.raises(ValueError, match="Encar storage"):
            upsert_detail(db, auction, detail)


# Frozen pre-MSA-05 tables, not generated from the new ORM definitions.
LEGACY_TABLES = (
    """CREATE TABLE projects (
        id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id),
        name VARCHAR(140) NOT NULL, name_key VARCHAR(140) NOT NULL,
        search_url TEXT NOT NULL, telegram_url TEXT,
        scan_mode VARCHAR(16) NOT NULL, search_page_mode VARCHAR(16) NOT NULL,
        price_filter_mode VARCHAR(16) NOT NULL, price_min_krw BIGINT, price_max_krw BIGINT,
        price_filter_revision INTEGER NOT NULL, price_filter_baseline_revision INTEGER NOT NULL,
        auto_update BOOLEAN NOT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT uq_projects_owner_name_key UNIQUE(owner_id, name_key),
        CONSTRAINT uq_projects_owner_search_url UNIQUE(owner_id, search_url))""",
    """CREATE TABLE cars (
        id INTEGER PRIMARY KEY, canonical_encar_id VARCHAR(40) NOT NULL UNIQUE,
        url TEXT NOT NULL, title TEXT, details JSON, current_price BIGINT,
        status VARCHAR(40) NOT NULL, fingerprint VARCHAR(64), comment TEXT, rating INTEGER,
        integrity_status VARCHAR(30) NOT NULL, integrity_reason TEXT, excluded BOOLEAN NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE car_aliases (
        id INTEGER PRIMARY KEY, car_id INTEGER NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
        alias_id VARCHAR(40) NOT NULL UNIQUE,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
)


def legacy_database(tmp_path):
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'legacy.sqlite3'}")
    with engine.begin() as connection:
        for statement in LEGACY_TABLES:
            connection.exec_driver_sql(statement)
        Base.metadata.create_all(connection)
        connection.execute(User.__table__.insert().values(
            id=5, username="Owner", username_key="owner", password_hash="!test"
        ))
        connection.exec_driver_sql("""INSERT INTO projects (
            id, owner_id, name, name_key, search_url, scan_mode, search_page_mode,
            price_filter_mode, price_filter_revision, price_filter_baseline_revision, auto_update
        ) VALUES (17, 5, 'Audi A5', 'audi a5', 'https://www.encar.com/fc/fc_carsearchlist.do',
                  'FAST', 'ALL_PAGES', 'LINK', 1, 1, 1)""")
        detail = {"canonical_car_id": "42688672", "displayed_car_id": "42812345",
                  "offer_type": "RENT", "rental_monthly_payment_krw": 360000,
                  "main_image_path": "cars/42688672/main-image.jpg"}
        connection.execute(text("""INSERT INTO cars (
            id, canonical_encar_id, url, details, current_price, status, comment, rating,
            integrity_status, excluded
        ) VALUES (29, '42688672', 'https://fem.encar.com/cars/detail/42688672',
                  :detail, 360000, 'UPDATED', 'Keep me', 4, 'VALID', 0)"""), {"detail": json.dumps(detail)})
        connection.exec_driver_sql("INSERT INTO car_aliases(id, car_id, alias_id) VALUES(41, 29, '42812345')")
        connection.execute(ProjectCar.__table__.insert().values(
            project_id=17, car_id=29, favorite=True, viewed=True, tracking_enabled=False
        ))
        connection.execute(CarSnapshot.__table__.insert().values(id=51, car_id=29, payload=detail))
        connection.execute(CarImage.__table__.insert().values(
            id=61, car_id=29, kind="MAIN", payload={"path": detail["main_image_path"], "checksum": "unchanged"}
        ))
    return engine


def assert_source_columns(engine):
    with engine.connect() as connection:
        for table in ("projects", "cars", "car_aliases"):
            assert "source_key" in {item["name"] for item in inspect(connection).get_columns(table)}
        assert "source_listing_id" in {
            item["name"] for item in inspect(connection).get_columns("cars")
        }


def test_legacy_identity_is_backfilled_without_changing_ids_profile_or_files(tmp_path):
    engine = legacy_database(tmp_path)
    migrate_sqlite(engine)
    assert_source_columns(engine)
    with Session(engine) as db:
        project, car, alias = db.get(Project, 17), db.get(Car, 29), db.get(CarAlias, 41)
        assert project.source_key == car.source_key == alias.source_key == "encar"
        assert car.source_listing_id == car.canonical_encar_id == "42688672"
        assert alias.alias_id == "42812345"  # Displayed ID is not the canonical identity.
        assert (car.current_price, car.comment, car.rating) == (360000, "Keep me", 4)
        relation = db.get(ProjectCar, (17, 29))
        assert relation.favorite and relation.viewed and not relation.tracking_enabled
        assert db.get(CarSnapshot, 51).payload["offer_type"] == "RENT"
        assert db.get(CarImage, 61).payload == {"path": "cars/42688672/main-image.jpg", "checksum": "unchanged"}
    # SQLite reflection can omit inline UNIQUE constraints in old CREATE SQL.
    # Check the actual behavior instead: these legacy constraints remain active.
    with Session(engine) as db:
        duplicates = (
            Car(canonical_encar_id="42688672", url="https://fem.encar.com/cars/detail/42688672"),
            CarAlias(car_id=29, alias_id="42812345"),
            Project(owner_id=5, name="Audi A5", name_key="audi a5",
                    search_url="https://www.encar.com/dc/dc_carsearchlist.do"),
        )
        for duplicate in duplicates:
            with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
                with db.begin_nested():
                    db.add(duplicate)
                    db.flush()
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"


def test_identity_migration_is_repeatable_and_new_encar_writes_fill_identity(tmp_path):
    engine = legacy_database(tmp_path)
    first = migrate_sqlite(engine)
    assert_source_columns(engine)
    assert migrate_sqlite(engine) == first
    with Session(engine) as db:
        car = Car(canonical_encar_id="42791283", url="https://fem.encar.com/cars/detail/42791283")
        db.add(car)
        db.flush()
        alias = CarAlias(car_id=car.id, alias_id="42812346")
        db.add(alias)
        db.commit()
        assert car.source_key == alias.source_key == "encar"
        assert car.source_listing_id == "42791283"


def test_fresh_schema_has_the_same_source_fields(tmp_path):
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'fresh.sqlite3'}")
    migrate_sqlite(engine)
    assert_source_columns(engine)


def test_alembic_identity_step_preserves_the_same_legacy_rows(tmp_path):
    engine = legacy_database(tmp_path)
    migration = runpy.run_path(str(
        Path(__file__).resolve().parents[1] / "alembic/versions/0017_additive_source_identity.py"
    ))
    assert migration["revision"] == "0017"
    assert migration["down_revision"] == "0016"
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration["upgrade"]()
    assert_source_columns(engine)
    with Session(engine) as db:
        assert db.get(Car, 29).source_listing_id == "42688672"
        assert db.get(CarAlias, 41).source_key == "encar"
        assert db.get(Project, 17).source_key == "encar"
        assert db.get(CarSnapshot, 51).payload["offer_type"] == "RENT"
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration["downgrade"]()
        assert "source_listing_id" not in {
            item["name"] for item in inspect(connection).get_columns("cars")
        }
        assert connection.exec_driver_sql("SELECT canonical_encar_id FROM cars WHERE id=29").scalar_one() == "42688672"
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_failed_identity_backfill_rolls_back_the_whole_migration(tmp_path):
    engine = legacy_database(tmp_path)
    with engine.begin() as connection:
        connection.exec_driver_sql("""CREATE TRIGGER reject_identity_backfill
            BEFORE UPDATE ON cars BEGIN SELECT RAISE(ABORT, 'test_backfill_failure'); END""")
    with pytest.raises(IntegrityError, match="test_backfill_failure"):
        migrate_sqlite(engine)
    with engine.connect() as connection:
        for table in ("projects", "cars", "car_aliases"):
            assert "source_key" not in {
                item["name"] for item in inspect(connection).get_columns(table)
            }
        assert connection.exec_driver_sql("SELECT canonical_encar_id FROM cars WHERE id=29").scalar_one() == "42688672"
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER reject_identity_backfill")
    migrate_sqlite(engine)
    assert_source_columns(engine)


@pytest.mark.parametrize("mode", ["legacy", "migrated", "converted"])
def test_identity_survives_backup_restore_and_legacy_conversion(tmp_path, mode):
    engine = legacy_database(tmp_path)
    source_path = Path(engine.url.database)
    source_storage = tmp_path / "source-storage"
    relative_image = Path("cars/42688672/main-image.jpg")
    image = source_storage / relative_image
    image.parent.mkdir(parents=True)
    image.write_bytes(b"legacy-main-image")
    if mode == "migrated":
        migrate_sqlite(engine)
    # Conversion uses Core bulk inserts; each missing identity needs its own default.
    if mode == "converted":
        with engine.begin() as connection:
            connection.exec_driver_sql("""INSERT INTO cars (
                id, canonical_encar_id, url, current_price, status, integrity_status, excluded
            ) VALUES (30, '42791283', 'https://fem.encar.com/cars/detail/42791283',
                      15000000, 'NEW', 'VALID', 0)""")
    engine.dispose()
    archive = tmp_path / "identity.searchcar-backup"
    if mode == "converted":
        report = convert_to_backup(str(engine.url), source_storage, archive)
        assert report.source_counts == report.target_counts
        # The read-only converter must not migrate the source database.
        with engine.connect() as connection:
            assert "source_key" not in {
                item["name"] for item in inspect(connection).get_columns("cars")
            }
    else:
        export_backup(source_path, source_storage, archive)
    validation = validate_backup(archive)
    assert validation.schema_version == (0 if mode == "legacy" else 10)
    assert validation.table_counts["cars"] == (2 if mode == "converted" else 1)
    target_path = tmp_path / "restored" / "data.sqlite3"
    target_storage = tmp_path / "restored" / "storage"
    target_path.parent.mkdir()
    restore_backup(archive, target_path, target_storage, tmp_path / "backups")
    restored = create_database_engine(f"sqlite+pysqlite:///{target_path}")
    assert migrate_sqlite(restored) == 10
    with Session(restored) as db:
        car = db.get(Car, 29)
        assert (car.source_key, car.source_listing_id) == ("encar", "42688672")
        assert (car.current_price, car.comment, car.rating) == (360000, "Keep me", 4)
        assert db.get(Project, 17).source_key == "encar"
        assert db.get(CarAlias, 41).alias_id == "42812345"
        assert db.get(CarAlias, 41).source_key == "encar"
        assert db.get(CarSnapshot, 51).payload["offer_type"] == "RENT"
        assert db.get(ProjectCar, (17, 29)).favorite
        assert db.get(CarImage, 61).payload["path"] == relative_image.as_posix()
        if mode == "converted":
            assert db.get(Car, 30).source_listing_id == "42791283"
    assert (target_storage / relative_image).read_bytes() == b"legacy-main-image"
    with restored.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def additive_database(tmp_path, monkeypatch):
    from app import sqlite_migrations

    engine = legacy_database(tmp_path)
    with monkeypatch.context() as context:
        context.setattr(sqlite_migrations, "MIGRATIONS", sqlite_migrations.MIGRATIONS[:8])
        assert migrate_sqlite(engine) == 8
    return engine


def test_rebuild_preserves_all_dependents_indexes_triggers_and_rolls_back_on_failure(tmp_path, monkeypatch):
    from app import sqlite_migrations
    # Freeze this regression at the identity rebuild; money backfill intentionally
    # enriches history payloads in v10 and has its own preservation/rollback tests.
    monkeypatch.setattr(sqlite_migrations, "MIGRATIONS", sqlite_migrations.MIGRATIONS[:9])

    engine = additive_database(tmp_path, monkeypatch)
    with Session(engine) as db:
        db.get(ProjectCar, (17, 29)).last_observed_snapshot_id = 51
        db.add_all([
            PriceHistory(id=71, car_id=29, payload={"price_krw": 360000}),
            CarEvent(id=81, car_id=29, project_id=17, user_id=5, kind="UPDATED"),
            UserCarState(user_id=5, car_id=29, comment="user note", rating=5),
            ScanRun(id=91, owner_id=5, kind="PROJECTS", status="COMPLETED"),
            SchedulerSetting(id=101, user_id=5),
        ])
        db.flush()
        db.add_all([
            ProjectScanRun(id=111, project_id=17, scan_run_id=91, status="COMPLETED"),
            ScheduledProject(scheduler_id=101, project_id=17),
        ])
        db.commit()
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE INDEX custom_car_url ON cars(url)")
        connection.exec_driver_sql("""CREATE TRIGGER car_title_audit AFTER UPDATE OF title ON cars
            BEGIN UPDATE cars SET comment = 'trigger retained' WHERE id = NEW.id; END""")
    tables = ("projects", "cars", "car_aliases", "project_cars", "car_snapshots", "car_events",
              "car_images", "price_history", "user_car_states", "project_scan_runs", "scheduled_projects")
    with engine.connect() as connection:
        before = {table: connection.exec_driver_sql(f'SELECT * FROM "{table}"').all() for table in tables}
    original = sqlite_migrations._rebuild_source_table

    def fail_after_cars(connection, name):
        original(connection, name)
        if name == "cars":
            raise RuntimeError("simulated_rebuild_interruption")

    with monkeypatch.context() as context:
        context.setattr(sqlite_migrations, "_rebuild_source_table", fail_after_cars)
        with pytest.raises(RuntimeError, match="simulated_rebuild_interruption"):
            migrate_sqlite(engine)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("SELECT MAX(version) FROM desktop_schema_migrations").scalar_one() == 8
        assert {table: connection.exec_driver_sql(f'SELECT * FROM "{table}"').all() for table in tables} == before
    assert migrate_sqlite(engine) == 9
    with engine.connect() as connection:
        assert {table: connection.exec_driver_sql(f'SELECT * FROM "{table}"').all() for table in tables} == before
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert "custom_car_url" in {row[1] for row in connection.exec_driver_sql("PRAGMA index_list(cars)")}
    with engine.begin() as connection:
        connection.exec_driver_sql("UPDATE cars SET title='Changed' WHERE id=29")
        assert connection.exec_driver_sql("SELECT comment FROM cars WHERE id=29").scalar_one() == "trigger retained"
    backups = list(tmp_path.glob("legacy.pre-source-identity-*.sqlite3"))
    assert len(backups) == 2  # Failed attempt and successful migration each keep a rollback point.
    for backup in backups:
        saved = create_database_engine(f"sqlite+pysqlite:///{backup}")
        with saved.connect() as connection:
            assert connection.exec_driver_sql("SELECT MAX(version) FROM desktop_schema_migrations").scalar_one() == 8
            assert connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
    assert migrate_sqlite(engine) == 9
    assert len(list(tmp_path.glob("legacy.pre-source-identity-*.sqlite3"))) == 2


def test_rebuild_does_not_start_when_backup_space_is_insufficient(tmp_path, monkeypatch):
    from app import sqlite_migrations
    from types import SimpleNamespace

    engine = additive_database(tmp_path, monkeypatch)
    monkeypatch.setattr(sqlite_migrations.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))
    with pytest.raises(RuntimeError, match="insufficient_disk_space"):
        migrate_sqlite(engine)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT MAX(version) FROM desktop_schema_migrations").scalar_one() == 8
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM cars").scalar_one() == 1


def test_scoped_backup_roundtrip_preserves_equal_external_ids_and_aliases(tmp_path):
    engine = legacy_database(tmp_path)
    migrate_sqlite(engine)
    with Session(engine) as db:
        other = Car(id=30, source_key="copart", source_listing_id="42688672",
                    url="https://copart.example.test/lot/42688672")
        db.add(other)
        db.flush()
        db.add(CarAlias(source_key="copart", car_id=30, alias_id="42812345"))
        db.add(CarSnapshot(car_id=30, payload={"current_bid": 1500, "currency": "USD"}))
        db.commit()
    engine.dispose()
    storage = tmp_path / "storage"
    storage.mkdir()
    archive = tmp_path / "mixed.searchcar-backup"
    export_backup(Path(engine.url.database), storage, archive)
    target = tmp_path / "mixed.sqlite3"
    restore_backup(archive, target, tmp_path / "target-storage", tmp_path / "backups")
    restored = create_database_engine(f"sqlite+pysqlite:///{target}")
    with Session(restored) as db:
        assert (db.get(Car, 29).source_key, db.get(Car, 30).source_key) == ("encar", "copart")
        assert db.get(Car, 29).source_listing_id == db.get(Car, 30).source_listing_id == "42688672"
        assert db.get(Car, 29).current_price == 360000
        assert db.get(Car, 30).canonical_encar_id is None
    with restored.connect() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM car_aliases WHERE alias_id='42812345'").scalar_one() == 2
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_alembic_scoped_identity_uses_the_same_rebuild_and_refuses_unsafe_downgrade(tmp_path, monkeypatch):
    engine = additive_database(tmp_path, monkeypatch)
    migration = runpy.run_path(str(
        Path(__file__).resolve().parents[1] / "alembic/versions/0018_source_scoped_identity.py"
    ))
    assert migration["revision"] == "0018"
    assert migration["down_revision"] == "0017"
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            with Operations.context(MigrationContext.configure(connection)):
                migration["upgrade"]()
            connection.commit()
        finally:
            connection.rollback()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    with Session(engine) as db:
        assert db.get(Car, 29).source_listing_id == "42688672"
        assert db.get(CarSnapshot, 51).payload["offer_type"] == "RENT"
        db.add(Car(source_key="copart", source_listing_id="42688672",
                   url="https://copart.example.test/lot/42688672"))
        db.commit()
    with pytest.raises(RuntimeError, match="verified_pre_migration_backup"):
        migration["downgrade"]()


def test_alembic_postgres_scoped_identity_ddl_compiles():
    from io import StringIO

    migration = runpy.run_path(str(
        Path(__file__).resolve().parents[1] / "alembic/versions/0018_source_scoped_identity.py"
    ))
    output = StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        migration["upgrade"]()
    sql = output.getvalue()
    assert "UNIQUE (source_key, source_listing_id)" in sql
    assert "UNIQUE (owner_id, source_key, name_key)" in sql
    assert "FOREIGN KEY(car_id, source_key) REFERENCES cars (id, source_key)" in sql
    assert "DROP CONSTRAINT cars_canonical_encar_id_key" in sql


def test_legacy_import_keeps_encar_separate_from_auction_aliases_and_names(tmp_path, monkeypatch):
    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker
    from app import importer

    engine = legacy_database(tmp_path)
    migrate_sqlite(engine)
    with Session(engine) as db:
        project = Project(owner_id=5, source_key="copart", name="BMW", name_key="bmw",
                          search_url="https://copart.example.test/search")
        car = Car(source_key="copart", source_listing_id="42791283",
                  url="https://copart.example.test/lot/42791283", title="Auction remains unchanged")
        db.add_all([project, car])
        db.flush()
        db.add(CarAlias(car_id=car.id, source_key="copart", alias_id="42791283"))
        db.commit()
        auction_id = car.id
    root = tmp_path / "legacy-import"
    (root / "data").mkdir(parents=True)
    (root / "data/seen.json").write_text(json.dumps({"42791283": {
        "car_id": "42791283", "source_car_id": "42791283", "title": "Encar imported",
        "source": "BMW", "source_search_url": "https://www.encar.com/fc/fc_carsearchlist.do?bmw=1",
        "url": "https://fem.encar.com/cars/detail/42791283", "price_krw": 15000000,
    }}), encoding="utf-8")
    monkeypatch.setattr(importer, "SessionLocal", sessionmaker(bind=engine))
    result = importer.run(root, commit=True, owner_id=5)
    assert result["cars_created"] == 1
    with Session(engine) as db:
        assert db.get(Car, auction_id).title == "Auction remains unchanged"
        imported = db.scalar(select(Car).where(Car.source_key == "encar", Car.source_listing_id == "42791283"))
        assert imported.title == "Encar imported"
        assert db.scalar(select(Project).where(Project.source_key == "encar", Project.name_key == "bmw")).name == "BMW"
