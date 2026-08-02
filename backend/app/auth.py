import argparse
import getpass
import hashlib
import hmac
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import SessionLocal, get_db, settings
from .models import AuditLog, AuthSession, User


password_hasher = PasswordHash.recommended()
_dummy_password_hash: str | None = None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def normalize_username(value: str) -> str:
    return unicodedata.normalize("NFKC", value.strip()).casefold()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    global _dummy_password_hash
    if stored_hash.startswith("!"):
        if _dummy_password_hash is None:
            _dummy_password_hash = password_hasher.hash("invalid-password-sentinel")
        stored_hash = _dummy_password_hash
    try:
        return password_hasher.verify(password, stored_hash)
    except Exception:
        return False


def secure_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def privacy_hash(value: str | None) -> str | None:
    if not value:
        return None
    return hmac.new(
        settings.auth_hash_secret.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def request_ip_hash(request: Request) -> str | None:
    return privacy_hash(request.client.host if request.client else None)


def request_user_agent_hash(request: Request) -> str | None:
    return privacy_hash(request.headers.get("user-agent"))


def allowed_origins() -> set[str]:
    return {
        origin.strip().rstrip("/")
        for origin in settings.allowed_origins.split(",")
        if origin.strip()
    }


def verify_request_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in allowed_origins():
        raise HTTPException(403, "origin_not_allowed")


@dataclass
class AuthContext:
    user: User
    session: AuthSession


def get_auth_context(
    request: Request,
    db: Session = Depends(get_db),
) -> AuthContext:
    token = request.cookies.get(settings.auth_cookie_name)
    if not token:
        raise HTTPException(401, "authentication_required")
    session = db.scalar(
        select(AuthSession).where(AuthSession.token_hash == secure_hash(token))
    )
    current = now_utc()
    if (
        not session
        or session.revoked_at is not None
        or session.expires_at <= current
    ):
        raise HTTPException(401, "authentication_required")
    user = db.get(User, session.user_id)
    if not user or user.status != "ACTIVE":
        raise HTTPException(401, "authentication_required")
    if (
        not session.last_seen_at
        or session.last_seen_at < current - timedelta(minutes=5)
    ):
        session.last_seen_at = current
        user.last_activity_at = current
        db.commit()
    return AuthContext(user=user, session=session)


def require_user(context: AuthContext = Depends(get_auth_context)) -> User:
    return context.user


def require_admin(context: AuthContext = Depends(get_auth_context)) -> User:
    if context.user.role != "ADMIN":
        raise HTTPException(403, "admin_required")
    return context.user


def require_csrf(
    request: Request,
    context: AuthContext = Depends(get_auth_context),
) -> User:
    verify_request_origin(request)
    supplied = request.headers.get("x-csrf-token") or ""
    if not supplied or not hmac.compare_digest(
        secure_hash(supplied),
        context.session.csrf_hash,
    ):
        raise HTTPException(403, "csrf_validation_failed")
    return context.user


def create_session(db: Session, user: User, request: Request) -> tuple[str, str, AuthSession]:
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    session = AuthSession(
        user_id=user.id,
        token_hash=secure_hash(token),
        csrf_hash=secure_hash(csrf_token),
        expires_at=now_utc() + timedelta(days=settings.auth_session_days),
        last_seen_at=now_utc(),
        ip_hash=request_ip_hash(request),
        user_agent_hash=request_user_agent_hash(request),
    )
    db.add(session)
    db.flush()
    return token, csrf_token, session


def rotate_csrf(session: AuthSession) -> str:
    csrf_token = secrets.token_urlsafe(32)
    session.csrf_hash = secure_hash(csrf_token)
    return csrf_token


def sync_csrf_cookie(
    request: Request,
    response: Response,
    session: AuthSession,
) -> tuple[str, bool]:
    csrf_token = request.cookies.get(settings.auth_csrf_cookie_name)
    rotated = not csrf_token or not hmac.compare_digest(
        secure_hash(csrf_token),
        session.csrf_hash,
    )
    if rotated:
        csrf_token = rotate_csrf(session)
    response.set_cookie(
        settings.auth_csrf_cookie_name,
        csrf_token,
        httponly=False,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        max_age=settings.auth_session_days * 24 * 60 * 60,
        path="/",
    )
    return csrf_token, rotated


def revoke_user_sessions(
    db: Session,
    user_id: int,
    *,
    except_session_id: int | None = None,
) -> None:
    current = now_utc()
    for session in db.scalars(
        select(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
    ):
        if except_session_id is None or session.id != except_session_id:
            session.revoked_at = current


def audit(
    db: Session,
    action: str,
    *,
    actor_user_id: int | None = None,
    target_user_id: int | None = None,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    outcome: str = "SUCCESS",
    payload: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            outcome=outcome,
            payload=payload,
        )
    )


def bootstrap_admin(username: str, password: str, must_change_password: bool) -> User:
    username_key = normalize_username(username)
    if len(password) < 8 or len(password) > 128:
        raise ValueError("password_must_be_between_8_and_128_characters")
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.username_key == username_key))
        if not user:
            user = User(
                username=username.strip(),
                username_key=username_key,
                password_hash="!bootstrap-required",
                role="ADMIN",
                status="ACTIVE",
                project_limit=None,
            )
            db.add(user)
            db.flush()
        user.username = username.strip()
        user.password_hash = hash_password(password)
        user.role = "ADMIN"
        user.status = "ACTIVE"
        user.project_limit = None
        user.must_change_password = must_change_password
        revoke_user_sessions(db, user.id)
        audit(
            db,
            "ADMIN_BOOTSTRAPPED",
            actor_user_id=user.id,
            target_user_id=user.id,
            entity_type="USER",
            entity_id=user.id,
        )
        return user


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage SearchCar authentication")
    subparsers = parser.add_subparsers(dest="command", required=True)
    bootstrap = subparsers.add_parser("bootstrap-admin")
    bootstrap.add_argument("--username", default="Serhii")
    bootstrap.add_argument(
        "--no-require-password-change",
        action="store_true",
    )
    arguments = parser.parse_args()
    if arguments.command == "bootstrap-admin":
        password = getpass.getpass("Initial password: ")
        confirmation = getpass.getpass("Repeat password: ")
        if not hmac.compare_digest(password, confirmation):
            raise SystemExit("Passwords do not match")
        user = bootstrap_admin(
            arguments.username,
            password,
            not arguments.no_require_password_change,
        )
        print(f"Administrator {user.username} is ready.")


if __name__ == "__main__":
    main()
