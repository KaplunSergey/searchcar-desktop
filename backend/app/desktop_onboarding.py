"""Local workspace creation for the passwordless desktop first run.

The local ``User`` row is an ownership boundary for SQLite data, not an
account a customer manages.  Cloudflare remains the source of truth for the
customer, license and device binding.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import AuditLog, User


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


def create_desktop_workspace(
    db: Session,
    *,
    preferred_locale: str = "ru",
) -> User:
    """Create the one local ownership workspace after a license is redeemed.

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
            payload={"source": "LICENSE_ACTIVATION"},
        )
    )
    return user
