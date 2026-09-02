"""Local workspace creation for the passwordless desktop first run.

The local ``User`` row is an ownership boundary for SQLite data, not an
account a customer manages.  Cloudflare remains the source of truth for the
customer, license and device binding.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .models import AuditLog, AuthAttempt, AuthSession, User


DESKTOP_WORKSPACE_USERNAME = "SearchCar"
DESKTOP_WORKSPACE_USERNAME_KEY = "searchcar-workspace"
DESKTOP_WORKSPACE_PASSWORD_MARKER = "!desktop-workspace"


def has_local_users(db: Session) -> bool:
    return bool(db.scalar(select(func.count()).select_from(User)))


def desktop_workspace_user(db: Session) -> User | None:
    """Return only the non-interactive workspace created by desktop setup."""

    return db.scalar(
        select(User).where(
            User.username_key == DESKTOP_WORKSPACE_USERNAME_KEY,
            User.password_hash == DESKTOP_WORKSPACE_PASSWORD_MARKER,
            User.status == "ACTIVE",
        )
    )


def migrate_legacy_desktop_workspace(db: Session) -> str:
    """Turn a single pre-license local account into the hidden workspace.

    The row is updated in place so every project, run, scheduler setting and
    history record keeps its existing owner id. Multiple legacy users are not
    merged: the desktop runtime creates a verified backup and starts clean.
    """

    users = list(db.scalars(select(User).order_by(User.id)))
    if not users:
        return "empty"
    if len(users) != 1:
        return "multiple"
    user = users[0]
    if (
        user.username_key == DESKTOP_WORKSPACE_USERNAME_KEY
        and user.password_hash == DESKTOP_WORKSPACE_PASSWORD_MARKER
        and user.status == "ACTIVE"
    ):
        return "workspace"

    user.username = DESKTOP_WORKSPACE_USERNAME
    user.username_key = DESKTOP_WORKSPACE_USERNAME_KEY
    user.password_hash = DESKTOP_WORKSPACE_PASSWORD_MARKER
    user.role = "USER"
    user.status = "ACTIVE"
    user.project_limit = None
    user.must_change_password = False
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    db.execute(delete(AuthAttempt))
    db.add(
        AuditLog(
            actor_user_id=user.id,
            target_user_id=user.id,
            action="DESKTOP_WORKSPACE_MIGRATED",
            entity_type="USER",
            entity_id=str(user.id),
            outcome="SUCCESS",
            payload={"source": "LEGACY_SINGLE_USER"},
        )
    )
    db.flush()
    return "migrated"


def create_desktop_workspace(
    db: Session,
    *,
    preferred_locale: str = "ru",
    source: str = "LICENSE_ACTIVATION",
) -> User:
    """Create the one local ownership workspace after license setup.

    A pre-existing user means the database belongs to an older installation or
    has already completed activation.  It must never be silently repurposed.
    """

    if has_local_users(db):
        raise ValueError("desktop_workspace_already_initialized")
    if preferred_locale not in {"ru", "uk"}:
        preferred_locale = "ru"
    user = User(
        username=DESKTOP_WORKSPACE_USERNAME,
        username_key=DESKTOP_WORKSPACE_USERNAME_KEY,
        password_hash=DESKTOP_WORKSPACE_PASSWORD_MARKER,
        role="USER",
        status="ACTIVE",
        project_limit=None,
        must_change_password=False,
        preferred_locale=preferred_locale,
    )
    db.add(user)
    db.flush()
    db.add(
        AuditLog(
            actor_user_id=user.id,
            target_user_id=user.id,
            action="DESKTOP_WORKSPACE_CREATED",
            entity_type="USER",
            entity_id=str(user.id),
            outcome="SUCCESS",
            payload={"source": source},
        )
    )
    return user
