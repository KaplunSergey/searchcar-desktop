from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from .auth import hash_password, normalize_username
from .models import AuditLog, User


def ensure_initial_admin(
    session_factory: sessionmaker,
    *,
    username: str,
    password: str,
) -> User | None:
    """Create the first local administrator once, without a CLI step."""

    normalized_username = normalize_username(username)
    if len(password) < 8 or len(password) > 128:
        raise ValueError("password_must_be_between_8_and_128_characters")
    with session_factory.begin() as db:
        if db.scalar(select(func.count()).select_from(User)):
            return None
        user = User(
            username=username.strip(),
            username_key=normalized_username,
            password_hash=hash_password(password),
            role="ADMIN",
            status="ACTIVE",
            project_limit=None,
            must_change_password=True,
        )
        db.add(user)
        db.flush()
        db.add(
            AuditLog(
                actor_user_id=user.id,
                target_user_id=user.id,
                action="DESKTOP_ADMIN_CREATED",
                entity_type="USER",
                entity_id=str(user.id),
                outcome="SUCCESS",
                payload={"source": "FIRST_RUN"},
            )
        )
        return user
