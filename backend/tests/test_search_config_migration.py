"""Project preset migration preserves IDs, dependent rows and recoverability."""
import runpy
from pathlib import Path
import sqlite3

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app import sqlite_migrations as migrations
from app.database import create_database_engine
from app.models import Project, ProjectCar
from app.search_identity import url_identity_v1
from app.desktop_backup import export_backup, restore_backup, validate_backup
from app.postgres_converter import convert_to_backup
from app.sources.iaa import IAASource
from test_source_search_selection import IAA
from test_source_identity_migration import legacy_database


@pytest.fixture
def v11(tmp_path, monkeypatch):
    engine = legacy_database(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(migrations, 'MIGRATIONS', migrations.MIGRATIONS[:11])
        assert migrations.migrate_sqlite(engine) == 11
    assert 'search_config' not in {column['name'] for column in inspect(engine).get_columns('projects')}
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE INDEX test_project_name_index ON projects(name)')
        connection.exec_driver_sql('CREATE TRIGGER test_project_noop AFTER UPDATE ON projects BEGIN SELECT 1; END')
    yield engine
    engine.dispose()


def test_upgrade_keeps_ids_relations_indexes_and_a_verified_pre_migration_backup(v11):
    migrations.migrate_sqlite(v11)
    with Session(v11) as db:
        project = db.get(Project, 17)
        assert project.search_config is None
        assert project.search_identity == url_identity_v1(project.search_url)
        assert db.get(ProjectCar, (17, 29)).favorite is True
    with v11.connect() as connection:
        assert connection.exec_driver_sql('PRAGMA foreign_key_check').all() == []
        assert connection.exec_driver_sql('PRAGMA foreign_keys').scalar_one() == 1
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM sqlite_master WHERE name IN ('test_project_name_index','test_project_noop')").scalar_one() == 2
    backups = list(Path(v11.url.database).parent.glob('*.pre-project-search-config-*.sqlite3'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as saved:
        assert saved.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert saved.execute('SELECT MAX(version) FROM desktop_schema_migrations').fetchone()[0] == 11
        assert saved.execute('SELECT favorite FROM project_cars WHERE project_id=17 AND car_id=29').fetchone()[0] == 1
    migrations.migrate_sqlite(v11)
    assert list(Path(v11.url.database).parent.glob('*.pre-project-search-config-*.sqlite3')) == backups


def test_failed_rebuild_rolls_back_schema_data_and_ledger_and_restores_foreign_keys(v11, monkeypatch):
    original = migrations.MIGRATIONS[-1]
    def fail(connection):
        original.action(connection)
        raise RuntimeError('simulated_project_search_failure')
    with monkeypatch.context() as patch:
        patch.setattr(migrations, 'MIGRATIONS', (*migrations.MIGRATIONS[:-1], migrations.SQLiteMigration(original.version, original.name, fail)))
        with pytest.raises(RuntimeError, match='simulated_project_search_failure'):
            migrations.migrate_sqlite(v11)
    assert 'search_config' not in {column['name'] for column in inspect(v11).get_columns('projects')}
    with v11.connect() as connection:
        assert connection.exec_driver_sql('SELECT MAX(version) FROM desktop_schema_migrations').scalar_one() == 11
        assert connection.exec_driver_sql('PRAGMA foreign_keys').scalar_one() == 1
        assert connection.exec_driver_sql('SELECT favorite FROM project_cars WHERE project_id=17 AND car_id=29').scalar_one() == 1
    migrations.migrate_sqlite(v11)


def test_alembic_equivalent_preserves_rows_and_refuses_lossy_downgrade(v11):
    module = runpy.run_path(str(Path(__file__).parents[1] / 'alembic/versions/0021_project_search_config.py'))
    assert module['down_revision'] == '0020'
    with v11.connect() as connection:
        # Batch table replacement requires this outside a SQLite transaction.
        connection.exec_driver_sql('PRAGMA foreign_keys=OFF')
        connection.commit()
        with connection.begin(), Operations.context(MigrationContext.configure(connection)):
            module['upgrade']()
            assert connection.exec_driver_sql('PRAGMA foreign_key_check').all() == []
        connection.exec_driver_sql('PRAGMA foreign_keys=ON')
    with Session(v11) as db:
        project = db.get(Project, 17)
        assert project.search_config is None
        assert project.search_identity == url_identity_v1(project.search_url)
        assert db.get(ProjectCar, (17, 29)).favorite
    with pytest.raises(RuntimeError, match='verified_backup'):
        module['downgrade']()


@pytest.mark.parametrize('mode', ['backup', 'converter'])
def test_public_preset_survives_backup_restore_and_read_only_conversion(v11, tmp_path, mode):
    migrations.migrate_sqlite(v11)
    source = IAASource()
    config = source.capture_search_request(source.selection_entry_url, 'POST', IAA)
    config['creation_url'] = 'https://www.iaai.com/Search?url=public-native-uri'
    with Session(v11) as db:
        project = Project(owner_id=5, source_key='iaa', name='Preset', name_key='preset', search_url=config['creation_url'],
                          search_config=config, search_identity=source.search_identity(config))
        db.add(project); db.commit(); project_id = project.id
    storage = tmp_path / 'preset-storage'; storage.mkdir()
    archive = tmp_path / 'preset.searchcar-backup'
    if mode == 'backup':
        export_backup(Path(v11.url.database), storage, archive)
    else:
        report = convert_to_backup(str(v11.url), storage, archive)
        assert report.source_counts == report.target_counts
    assert validate_backup(archive).schema_version == migrations.MIGRATIONS[-1].version
    target = tmp_path / 'restored-presets.sqlite3'
    restore_backup(archive, target, tmp_path / 'restored-storage', tmp_path / 'backups')
    restored = create_database_engine(f'sqlite+pysqlite:///{target}')
    with Session(restored) as db:
        project = db.get(Project, project_id)
        assert project.search_config == config
        assert project.search_identity == source.search_identity(config)
        assert db.get(Project, 17).search_config is None
        assert db.get(ProjectCar, (17, 29)).favorite
    restored.dispose()
