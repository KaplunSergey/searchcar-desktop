from fastapi import Request, Response
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import (
    create_session,
    hash_password,
    normalize_username,
    revoke_user_sessions,
    secure_hash,
    sync_csrf_cookie,
    verify_password,
)
from app.database import Base, settings
from app.models import Project, User


def make_user(username: str) -> User:
    return User(
        username=username,
        username_key=normalize_username(username),
        password_hash=hash_password("test-password"),
        role="USER",
        status="ACTIVE",
        project_limit=1,
    )


def test_password_hash_and_username_normalization() -> None:
    password_hash = hash_password("sample-strong-password")

    assert password_hash != "sample-strong-password"
    assert verify_password("sample-strong-password", password_hash)
    assert not verify_password("wrong-password", password_hash)
    assert normalize_username("  ＳＥＲＨＩＩ  ") == "serhii"


def test_session_secrets_are_stored_as_hashes() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = make_user("Owner")
        db.add(user)
        db.flush()
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/auth/login",
                "headers": [(b"user-agent", b"pytest")],
                "client": ("127.0.0.1", 1234),
                "scheme": "http",
                "server": ("localhost", 8000),
                "query_string": b"",
            }
        )

        token, csrf_token, session = create_session(db, user, request)

        assert session.token_hash == secure_hash(token)
        assert session.csrf_hash == secure_hash(csrf_token)
        assert token not in session.token_hash
        revoke_user_sessions(db, user.id)
        assert session.revoked_at is not None


def test_csrf_cookie_is_reused_across_tabs() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = make_user("Owner")
        db.add(user)
        db.flush()
        login_request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/auth/login",
                "headers": [],
                "client": ("127.0.0.1", 1234),
                "scheme": "http",
                "server": ("localhost", 8000),
                "query_string": b"",
            }
        )
        _, original_token, session = create_session(db, user, login_request)
        response = Response()
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/auth/me",
                "headers": [
                    (
                        b"cookie",
                        f"{settings.auth_csrf_cookie_name}={original_token}".encode(),
                    )
                ],
                "client": ("127.0.0.1", 1234),
                "scheme": "http",
                "server": ("localhost", 8000),
                "query_string": b"",
            }
        )

        returned_token, rotated = sync_csrf_cookie(
            request,
            response,
            session,
        )

        assert not rotated
        assert returned_token == original_token
        assert session.csrf_hash == secure_hash(original_token)


def test_project_uniqueness_is_scoped_to_owner() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        first = make_user("First")
        second = make_user("Second")
        db.add_all([first, second])
        db.flush()
        db.add_all(
            [
                Project(
                    owner_id=first.id,
                    name="Audi",
                    name_key="audi",
                    search_url="https://www.encar.com/audi",
                ),
                Project(
                    owner_id=second.id,
                    name="Audi",
                    name_key="audi",
                    search_url="https://www.encar.com/audi",
                ),
            ]
        )
        db.commit()
        db.add(
            Project(
                owner_id=first.id,
                name="AUDI",
                name_key="audi",
                search_url="https://www.encar.com/another-audi",
            )
        )

        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        else:
            raise AssertionError("same-owner project name must be unique")
