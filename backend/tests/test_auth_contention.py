"""Authentication must remain readable while the scan owns SQLite's writer."""
from datetime import datetime, timedelta, timezone
from time import monotonic

from fastapi import HTTPException, Request
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.auth import get_auth_context, require_csrf, secure_hash
from app.database import Base, create_database_engine, settings
from app.models import AuthSession, User


@pytest.fixture
def workspace(tmp_path):
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'auth.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    old = datetime.now(timezone.utc) - timedelta(minutes=10)
    with factory.begin() as db:
        user = User(username='fixture', username_key='fixture', password_hash='!fixture',
                    status='ACTIVE', last_activity_at=old)
        db.add(user)
        db.flush()
        db.add(AuthSession(user_id=user.id, token_hash=secure_hash('fixture-session'),
                           csrf_hash=secure_hash('fixture-csrf'), last_seen_at=old,
                           expires_at=old + timedelta(days=1)))
    yield engine, factory, old
    engine.dispose()


def request(csrf='fixture-csrf'):
    return Request({'type': 'http', 'method': 'GET', 'path': '/api/projects',
                    'headers': [(b'cookie', f'{settings.auth_cookie_name}=fixture-session'.encode()),
                                (b'x-csrf-token', csrf.encode())]})


def test_busy_activity_write_does_not_fail_or_delay_authenticated_reads(workspace):
    engine, factory, old = workspace
    with engine.connect() as writer, factory() as db:
        writer.exec_driver_sql('BEGIN IMMEDIATE')
        started = monotonic()
        context = get_auth_context(request(), db)
        assert context.user.username == 'fixture'
        assert require_csrf(request(), context).id == context.user.id
        assert db.scalar(select(User.username)) == 'fixture'
        assert monotonic() - started < 1, 'optional activity must not wait for the scan writer'
        assert context.session.last_seen_at == old
        writer.rollback()
    # The skipped timestamp is retried normally once the writer is free.
    with factory() as db:
        context = get_auth_context(request(), db)
        assert context.session.last_seen_at > old
        assert context.user.last_activity_at > old
    with engine.connect() as connection:
        assert connection.exec_driver_sql('PRAGMA busy_timeout').scalar_one() == 5000


@pytest.mark.parametrize('invalid', ['revoked', 'expired', 'blocked', 'csrf'])
def test_contention_never_bypasses_session_or_csrf_validation(workspace, invalid):
    engine, factory, old = workspace
    with factory.begin() as db:
        session = db.scalar(select(AuthSession))
        if invalid == 'revoked':
            session.revoked_at = old
        elif invalid == 'expired':
            session.expires_at = old
        elif invalid == 'blocked':
            db.scalar(select(User)).status = 'BLOCKED'
    with engine.connect() as writer, factory() as db:
        writer.exec_driver_sql('BEGIN IMMEDIATE')
        with pytest.raises(HTTPException) as error:
            context = get_auth_context(request(), db)
            require_csrf(request('wrong-csrf'), context)
        assert error.value.status_code == (403 if invalid == 'csrf' else 401)
        writer.rollback()


def test_non_contention_storage_errors_are_not_swallowed(workspace):
    engine, factory, _ = workspace
    with engine.begin() as connection:
        connection.execute(text("CREATE TRIGGER broken_activity BEFORE UPDATE ON auth_sessions "
                                "BEGIN SELECT RAISE(ABORT, 'fixture storage failure'); END"))
    with factory() as db, pytest.raises(Exception, match='fixture storage failure'):
        get_auth_context(request(), db)
