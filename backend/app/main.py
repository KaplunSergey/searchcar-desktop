from datetime import datetime, timedelta, timezone
from copy import deepcopy
import json
import logging
import os
from queue import Empty
import shutil
import webbrowser
from pathlib import Path
from uuid import uuid4
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import and_, delete as sa_delete, func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from .auth import (
    AuthContext,
    audit,
    create_session,
    get_auth_context,
    hash_password,
    normalize_username,
    now_utc,
    request_ip_hash,
    require_admin,
    require_csrf,
    require_user,
    revoke_user_sessions,
    sync_csrf_cookie,
    verify_password,
    verify_request_origin,
)
from .database import get_db, settings
from .money import comparable_money, money_values_out, payload_money_values
from .auction_read import project_auction_view, single_auction_view, report_snapshot_view
from .auction_time import _timezone
from .workspace_preferences import WorkspacePreferences, load_workspace, save_workspace
from .telegram_connection import router as telegram_router
from . import telegram_pairing
from . import telegram_dispatch
from . import telegram_automatic
from . import telegram_reschedules
from .telegram_connection import TelegramError
from starlette.concurrency import run_in_threadpool
from .source_search_selection import search_selections
from .search_identity import url_identity_v1
from .sources.registry import is_auction_source
from .importer import inspect_source, run as run_import
from .job_queue import SCAN_COMMAND_LOCK, detach_project_from_active_jobs, project_execution_order
from .models import (
    Car,
    CarAlias,
    CarEvent,
    CarImage,
    CarSnapshot,
    AuctionObservation,
    AuthAttempt,
    AuthSession,
    AuditLog,
    PriceHistory,
    Project,
    ProjectCar,
    ProjectScanRun,
    ScanRun,
    ScheduledProject,
    SchedulerSetting,
    User,
    UserCarState,
)
from .current_images import image_storage_path
from .parser import (
    extract_car_id,
    material_changes,
    parse_condition,
    parse_contract_status,
    parse_new_car_price_percent,
    parse_options,
    parse_vehicle_fields,
)
from .schemas import (
    BoolPatch,
    AdminPasswordResetIn,
    AdminUserIn,
    AdminUserPatch,
    CommentPatch,
    LoginIn,
    PasswordChangeIn,
    ProfilePatch,
    ProjectIn,
    ProjectPatch,
    RatingPatch,
    ScanCarsIn,
    CancelScanIn,
    ScanProjectsIn,
    SchedulerIn,
    DesktopMigrationIn,
    DesktopLicenseRedeemIn,
    DesktopOnboardingActivateIn,
    DesktopOnboardingTransferClaimIn,
    DesktopLicenseTransferClaimIn,
    DesktopLicenseTrialIn,
    ExternalUrlIn,
    SearchSelectionIn,
    ProjectSearchSelectionIn,
    validate_price_filter,
    RegistrationIn,
)
from .services import merge_reliable_detail
from .sources.contracts import InvalidSourceUrlError, SourceUnavailableError
from .sources.registry import get_source
from .reporting import dedupe_report
from .vehicle_groups import group_vehicle_listings
from .live_updates import live_updates
from .http_middleware import RequestLogMiddleware

logger = logging.getLogger(__name__)

app = FastAPI(title="SearchCar API", version=settings.app_version)
app.include_router(telegram_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in settings.allowed_origins.split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
storage_path = Path(settings.storage_root)
storage_path.mkdir(parents=True, exist_ok=True)
MAX_DESKTOP_BACKUP_UPLOAD_BYTES = 20 * 1024 * 1024 * 1024
app.add_event_handler('shutdown', search_selections.shutdown)
app.add_event_handler('shutdown', telegram_dispatch.service.stop)

def start_telegram_delivery():
    try:
        telegram_dispatch.service.start()
    except Exception:
        logger.warning('Telegram delivery is unavailable; application startup continues')

app.add_event_handler('startup', start_telegram_delivery)


app.add_middleware(RequestLogMiddleware)


def user_out(user: User, db: Session, *, include_usage: bool = True) -> dict:
    result = {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "status": user.status,
        "project_limit": user.project_limit,
        "must_change_password": user.must_change_password,
        "passwordless_workspace": (
            user.username_key == "searchcar-workspace"
            and user.password_hash == "!desktop-workspace"
        ),
        "preferred_locale": user.preferred_locale,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
        "last_login_at": user.last_login_at,
        "last_activity_at": user.last_activity_at,
    }
    if os.environ.get("SEARCHCAR_DESKTOP_DATA_DIR"):
        result["workspace"] = load_workspace(user.id)
    if include_usage:
        result["project_count"] = (
            db.scalar(
                select(func.count())
                .select_from(Project)
                .where(Project.owner_id == user.id)
            )
            or 0
        )
        result["car_count"] = (
            db.scalar(
                select(func.count(func.distinct(ProjectCar.car_id)))
                .select_from(ProjectCar)
                .join(Project, Project.id == ProjectCar.project_id)
                .where(
                    Project.owner_id == user.id,
                    ProjectCar.tracking_enabled.is_(True),
                )
            )
            or 0
        )
        result["scan_count"] = (
            db.scalar(
                select(func.count())
                .select_from(ScanRun)
                .where(ScanRun.owner_id == user.id)
            )
            or 0
        )
    return result


def owned_project(project_id: int, user: User, db: Session) -> Project:
    project = db.scalar(
        select(Project).where(
            Project.id == project_id,
            Project.owner_id == user.id,
        )
    )
    if not project:
        raise HTTPException(404, "project_not_found")
    return project


def auction_computer_timezone(computer_timezone: str = Query(..., min_length=1, max_length=64)) -> str:
    try:
        _timezone(computer_timezone)
    except ValueError as exc:
        raise HTTPException(400, "invalid_computer_timezone") from exc
    return computer_timezone


def user_car_state(user_id: int, car_id: int, db: Session) -> UserCarState:
    state = db.get(UserCarState, {"user_id": user_id, "car_id": car_id})
    if not state:
        state = UserCarState(user_id=user_id, car_id=car_id)
        db.add(state)
        db.flush()
    return state


def accessible_car_relation(
    car_id: int,
    user: User,
    db: Session,
    project_id: int | None = None,
    *,
    require_tracking: bool = True,
) -> ProjectCar:
    query = (
        select(ProjectCar)
        .join(Project, Project.id == ProjectCar.project_id)
        .where(
            ProjectCar.car_id == car_id,
            Project.owner_id == user.id,
        )
    )
    if project_id is not None:
        query = query.where(ProjectCar.project_id == project_id)
    if require_tracking:
        query = query.where(ProjectCar.tracking_enabled.is_(True))
    relation_record = db.scalar(query.order_by(Project.updated_at.desc()))
    if not relation_record:
        raise HTTPException(404, "project_car_not_found")
    return relation_record


@app.post("/api/auth/login")
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    verify_request_origin(request)
    username_key = normalize_username(body.username)
    ip_hash = request_ip_hash(request)
    cutoff = now_utc() - timedelta(minutes=15)
    username_failures = (
        db.scalar(
            select(func.count())
            .select_from(AuthAttempt)
            .where(
                AuthAttempt.username_key == username_key,
                AuthAttempt.success.is_(False),
                AuthAttempt.created_at >= cutoff,
            )
        )
        or 0
    )
    ip_failures = (
        db.scalar(
            select(func.count())
            .select_from(AuthAttempt)
            .where(
                AuthAttempt.ip_hash == ip_hash,
                AuthAttempt.success.is_(False),
                AuthAttempt.created_at >= cutoff,
            )
        )
        or 0
    )
    if username_failures >= 5 or ip_failures >= 10:
        raise HTTPException(429, "too_many_login_attempts")

    user = db.scalar(select(User).where(User.username_key == username_key))
    password_valid = verify_password(
        body.password,
        user.password_hash if user else "!unknown-user",
    )
    success = bool(user and password_valid and user.status == "ACTIVE")
    db.add(
        AuthAttempt(
            username_key=username_key,
            ip_hash=ip_hash,
            success=success,
        )
    )
    if not success:
        audit(
            db,
            "LOGIN_FAILED",
            target_user_id=user.id if user else None,
            outcome="FAILED",
            payload={"username_key": username_key, "ip_hash": ip_hash},
        )
        db.commit()
        raise HTTPException(401, "invalid_username_or_password")

    token, _, session = create_session(db, user, request)
    csrf_token, _ = sync_csrf_cookie(request, response, session)
    user.last_login_at = now_utc()
    user.last_activity_at = user.last_login_at
    audit(
        db,
        "LOGIN_SUCCEEDED",
        actor_user_id=user.id,
        target_user_id=user.id,
        entity_type="USER",
        entity_id=user.id,
    )
    db.commit()
    response.set_cookie(
        settings.auth_cookie_name,
        token,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        max_age=settings.auth_session_days * 24 * 60 * 60,
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"user": user_out(user, db), "csrf_token": csrf_token}


@app.get("/api/auth/me")
def auth_me(
    request: Request,
    response: Response,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict:
    csrf_token, rotated = sync_csrf_cookie(
        request,
        response,
        context.session,
    )
    if rotated:
        db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {
        "user": user_out(context.user, db),
        "csrf_token": csrf_token,
        "registration_enabled": settings.registration_enabled,
    }


@app.post("/api/auth/logout", status_code=204)
def logout(
    response: Response,
    context: AuthContext = Depends(get_auth_context),
    _: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Response:
    context.session.revoked_at = now_utc()
    audit(
        db,
        "LOGOUT",
        actor_user_id=context.user.id,
        target_user_id=context.user.id,
    )
    db.commit()
    response.delete_cookie(settings.auth_cookie_name, path="/")
    response.delete_cookie(settings.auth_csrf_cookie_name, path="/")
    response.headers["Cache-Control"] = "no-store"
    response.status_code = 204
    return response


@app.post("/api/auth/change-password")
def change_password(
    body: PasswordChangeIn,
    context: AuthContext = Depends(get_auth_context),
    _: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    if not verify_password(body.current_password, context.user.password_hash):
        raise HTTPException(400, "current_password_invalid")
    if body.current_password == body.new_password:
        raise HTTPException(422, "new_password_must_differ")
    context.user.password_hash = hash_password(body.new_password)
    context.user.must_change_password = False
    revoke_user_sessions(
        db,
        context.user.id,
        except_session_id=context.session.id,
    )
    audit(
        db,
        "PASSWORD_CHANGED",
        actor_user_id=context.user.id,
        target_user_id=context.user.id,
    )
    db.commit()
    return {"must_change_password": False}


@app.patch("/api/auth/profile")
def update_profile(
    body: ProfilePatch,
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    current.preferred_locale = body.preferred_locale
    audit(
        db,
        "USER_PROFILE_UPDATED",
        actor_user_id=current.id,
        target_user_id=current.id,
        entity_type="USER",
        entity_id=current.id,
        payload={"preferred_locale": current.preferred_locale},
    )
    db.commit()
    return user_out(current, db)


@app.put("/api/auth/workspace")
def update_workspace(body: WorkspacePreferences, current: User = Depends(require_csrf)) -> dict:
    if not os.environ.get("SEARCHCAR_DESKTOP_DATA_DIR"):
        raise HTTPException(400, "desktop_workspace_unavailable")
    try:
        save_workspace(current.id, body)
    except OSError:
        logger.exception("Desktop navigation preferences could not be saved")
        raise HTTPException(503, "workspace_storage_unavailable")
    return {"saved": True}


@app.post("/api/auth/register", status_code=202)
def register(
    body: RegistrationIn,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    verify_request_origin(request)
    if not settings.registration_enabled:
        raise HTTPException(404, "registration_disabled")
    username_key = normalize_username(body.username)
    if db.scalar(select(User.id).where(User.username_key == username_key)):
        return {"status": "PENDING_APPROVAL"}
    user = User(
        username=body.username.strip(),
        username_key=username_key,
        password_hash=hash_password(body.password),
        role="USER",
        status="PENDING_APPROVAL",
        project_limit=1,
        must_change_password=False,
    )
    db.add(user)
    db.flush()
    audit(
        db,
        "REGISTRATION_REQUESTED",
        target_user_id=user.id,
        entity_type="USER",
        entity_id=user.id,
    )
    db.commit()
    return {"status": "PENDING_APPROVAL"}


@app.get("/api/admin/users")
def admin_users(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    return [
        user_out(user, db)
        for user in db.scalars(select(User).order_by(User.created_at.desc()))
    ]


@app.post("/api/admin/users", status_code=201)
def admin_create_user(
    body: AdminUserIn,
    admin: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    if admin.role != "ADMIN":
        raise HTTPException(403, "admin_required")
    username_key = normalize_username(body.username)
    if db.scalar(select(User.id).where(User.username_key == username_key)):
        raise HTTPException(409, "username_exists")
    user = User(
        username=body.username.strip(),
        username_key=username_key,
        password_hash=hash_password(body.password),
        role=body.role,
        status=body.status,
        project_limit=None if body.role == "ADMIN" else body.project_limit,
        must_change_password=body.must_change_password,
    )
    db.add(user)
    db.flush()
    audit(
        db,
        "USER_CREATED",
        actor_user_id=admin.id,
        target_user_id=user.id,
        entity_type="USER",
        entity_id=user.id,
        payload={"role": user.role, "status": user.status},
    )
    db.commit()
    return user_out(user, db)


def ensure_not_last_admin(db: Session, user: User, changes: dict) -> None:
    removing_admin = user.role == "ADMIN" and (
        changes.get("role", user.role) != "ADMIN"
        or changes.get("status", user.status) != "ACTIVE"
    )
    if not removing_admin:
        return
    active_admins = (
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == "ADMIN", User.status == "ACTIVE")
        )
        or 0
    )
    if active_admins <= 1:
        raise HTTPException(409, "last_active_admin")


def delete_user_account_data(db: Session, user: User, admin: User) -> dict:
    project_count = (
        db.scalar(
            select(func.count())
            .select_from(Project)
            .where(Project.owner_id == user.id)
        )
        or 0
    )
    scan_count = (
        db.scalar(
            select(func.count())
            .select_from(ScanRun)
            .where(ScanRun.owner_id == user.id)
        )
        or 0
    )
    audit(
        db,
        "USER_DELETED",
        actor_user_id=admin.id,
        entity_type="USER",
        entity_id=user.id,
        payload={
            "username": user.username,
            "role": user.role,
            "status": user.status,
            "projects_deleted": project_count,
            "scans_deleted": scan_count,
        },
    )
    db.execute(
        sa_delete(SchedulerSetting).where(SchedulerSetting.user_id == user.id)
    )
    db.execute(sa_delete(ScanRun).where(ScanRun.owner_id == user.id))
    db.execute(sa_delete(Project).where(Project.owner_id == user.id))
    db.delete(user)
    return {
        "projects_deleted": project_count,
        "scans_deleted": scan_count,
    }


@app.patch("/api/admin/users/{user_id}")
def admin_patch_user(
    user_id: int,
    body: AdminUserPatch,
    admin: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    if admin.role != "ADMIN":
        raise HTTPException(403, "admin_required")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "user_not_found")
    changes = body.model_dump(exclude_unset=True)
    if user.id == admin.id and (
        changes.get("status", user.status) != "ACTIVE"
        or changes.get("role", user.role) != "ADMIN"
    ):
        raise HTTPException(409, "cannot_restrict_self")
    ensure_not_last_admin(db, user, changes)
    if "username" in changes:
        username_key = normalize_username(changes["username"])
        existing = db.scalar(
            select(User.id).where(
                User.username_key == username_key,
                User.id != user.id,
            )
        )
        if existing:
            raise HTTPException(409, "username_exists")
        user.username = changes.pop("username").strip()
        user.username_key = username_key
    for key, value in changes.items():
        setattr(user, key, value)
    if user.role == "ADMIN":
        user.project_limit = None
    if user.status != "ACTIVE":
        revoke_user_sessions(db, user.id)
        for run in db.scalars(
            select(ScanRun).where(
                ScanRun.owner_id == user.id,
                ScanRun.status.in_(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]),
            )
        ):
            if run.status == "QUEUED":
                run.status = "CANCELLED"
                run.finished_at = datetime.now(timezone.utc)
            else:
                run.status = "CANCEL_REQUESTED"
    audit(
        db,
        "USER_UPDATED",
        actor_user_id=admin.id,
        target_user_id=user.id,
        entity_type="USER",
        entity_id=user.id,
        payload={key: value for key, value in changes.items() if key != "password_hash"},
    )
    db.commit()
    return user_out(user, db)


@app.delete("/api/admin/users/{user_id}", status_code=204)
def admin_delete_user(
    user_id: int,
    admin: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Response:
    if admin.role != "ADMIN":
        raise HTTPException(403, "admin_required")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "user_not_found")
    if user.id == admin.id:
        raise HTTPException(409, "cannot_delete_self")
    if user.role == "ADMIN" and user.status == "ACTIVE":
        active_admins = (
            db.scalar(
                select(func.count())
                .select_from(User)
                .where(User.role == "ADMIN", User.status == "ACTIVE")
            )
            or 0
        )
        if active_admins <= 1:
            raise HTTPException(409, "last_active_admin")
    active_scans = (
        db.scalar(
            select(func.count())
            .select_from(ScanRun)
            .where(
                ScanRun.owner_id == user.id,
                ScanRun.status.in_(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]),
            )
        )
        or 0
    )
    if active_scans:
        raise HTTPException(409, "user_has_active_scans")
    revoke_user_sessions(db, user.id)
    delete_user_account_data(db, user, admin)
    db.commit()
    return Response(status_code=204)


@app.post("/api/admin/users/{user_id}/reset-password")
def admin_reset_password(
    user_id: int,
    body: AdminPasswordResetIn,
    admin: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    if admin.role != "ADMIN":
        raise HTTPException(403, "admin_required")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "user_not_found")
    user.password_hash = hash_password(body.password)
    user.must_change_password = body.must_change_password
    revoke_user_sessions(db, user.id)
    audit(
        db,
        "PASSWORD_RESET_BY_ADMIN",
        actor_user_id=admin.id,
        target_user_id=user.id,
        entity_type="USER",
        entity_id=user.id,
    )
    db.commit()
    return {"must_change_password": user.must_change_password}


@app.get("/api/admin/stats")
def admin_stats(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    cutoff = now_utc() - timedelta(days=30)
    return {
        "users": db.scalar(select(func.count()).select_from(User)) or 0,
        "active_users": db.scalar(
            select(func.count()).select_from(User).where(User.status == "ACTIVE")
        )
        or 0,
        "pending_users": db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.status == "PENDING_APPROVAL")
        )
        or 0,
        "blocked_users": db.scalar(
            select(func.count()).select_from(User).where(User.status == "BLOCKED")
        )
        or 0,
        "projects": db.scalar(select(func.count()).select_from(Project)) or 0,
        "cars": db.scalar(select(func.count()).select_from(Car)) or 0,
        "scans_30d": db.scalar(
            select(func.count())
            .select_from(ScanRun)
            .where(ScanRun.created_at >= cutoff)
        )
        or 0,
        "failed_scans_30d": db.scalar(
            select(func.count())
            .select_from(ScanRun)
            .where(
                ScanRun.created_at >= cutoff,
                ScanRun.status.in_(["FAILED", "PARTIAL", "CAPTCHA"]),
            )
        )
        or 0,
    }


@app.get("/api/admin/audit")
def admin_audit(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    return [
        {
            "id": item.id,
            "actor_user_id": item.actor_user_id,
            "target_user_id": item.target_user_id,
            "action": item.action,
            "entity_type": item.entity_type,
            "entity_id": item.entity_id,
            "outcome": item.outcome,
            "payload": item.payload,
            "created_at": item.created_at,
        }
        for item in db.scalars(
            select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200)
        )
    ]


def project_out(project: Project, db: Session) -> dict:
    cars = len(group_vehicle_listings([
        {"id": car.id, "details": car.details}
        for car in db.scalars(
            select(Car)
            .select_from(ProjectCar)
            .join(Car, Car.id == ProjectCar.car_id)
            .outerjoin(
                UserCarState,
                and_(
                    UserCarState.user_id == project.owner_id,
                    UserCarState.car_id == ProjectCar.car_id,
                ),
            )
            .where(
                ProjectCar.project_id == project.id,
                ProjectCar.tracking_enabled.is_(True),
                Car.excluded.is_(False),
                or_(
                    UserCarState.user_id.is_(None),
                    UserCarState.excluded.is_(False),
                ),
            )
        )
    ]))
    last_run = db.execute(
        select(ProjectScanRun, ScanRun)
        .join(ScanRun, ScanRun.id == ProjectScanRun.scan_run_id)
        .where(ProjectScanRun.project_id == project.id)
        .order_by(ScanRun.created_at.desc())
        .limit(1)
    ).first()
    last_refreshed_at = db.scalar(
        select(func.max(ScanRun.finished_at))
        .join(ProjectScanRun, ProjectScanRun.scan_run_id == ScanRun.id)
        .where(ProjectScanRun.project_id == project.id, ProjectScanRun.status == "SUCCEEDED")
    )
    return {
        "id": project.id,
        "source_key": project.source_key,
        "name": project.name,
        "search_url": project.search_url,
        "search_config": project.search_config,
        "search_revision": project.price_filter_revision,
        "telegram_url": project.telegram_url,
        "scan_mode": project.scan_mode,
        "search_page_mode": project.search_page_mode,
        "price_filter_mode": project.price_filter_mode,
        "price_min_krw": project.price_min_krw,
        "price_max_krw": project.price_max_krw,
        "auto_update": project.auto_update,
        "cars": cars,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "last_refreshed_at": last_refreshed_at,
        "latest_scan": (
            {
                "id": last_run.ScanRun.id,
                "status": last_run.ProjectScanRun.status,
                "error": last_run.ProjectScanRun.error_code,
                "created_at": last_run.ScanRun.created_at,
            }
            if last_run
            else None
        ),
    }


def get_car_or_404(car_id: int, db: Session) -> Car:
    car = db.get(Car, car_id)
    if not car:
        raise HTTPException(404, "car_not_found")
    return car


def effective_status(record: Car, relation: ProjectCar | None) -> str:
    if relation is None or not relation.tracking_enabled:
        return "REMOVED_FROM_SEARCH"
    if record.status == "SOLD":
        return "SOLD"
    if relation.search_status == "NOT_FOUND_IN_SEARCH":
        return "NOT_FOUND_IN_SEARCH"
    if relation.is_new:
        return "NEW"
    if record.status == "NEW":
        return "UPDATED"
    return record.status


def image_payloads(car_id: int, db: Session) -> dict:
    result = {}
    for image in db.scalars(
        select(CarImage).where(
            CarImage.car_id == car_id,
            CarImage.integrity_status == "VALID",
        ).order_by(CarImage.created_at.desc(), CarImage.id.desc())
    ):
        if image.kind in result:
            continue
        payload = dict(image.payload or {})
        path = payload.get("path")
        if path:
            resolved_path = image_storage_path(path, storage_path)
            payload["url"] = (
                f"/storage/{resolved_path.relative_to(storage_path.resolve()).as_posix()}"
                if resolved_path is not None
                else None
            )
        payload["stored_at"] = payload.get("updated_at") or image.created_at
        result[image.kind] = payload
    return result


def enriched_details(record: Car) -> dict:
    details = dict(record.details or {})
    raw = details.get("raw_text_excerpt") or ""
    if raw:
        fields = parse_vehicle_fields(raw, record.title or "")
        condition, condition_summary = parse_condition(raw)
        parsed = {
            **{key: value for key, value in fields.items() if value is not None},
            "new_car_price_percent": (
                details.get("new_car_price_percent")
                or parse_new_car_price_percent(raw)
            ),
            "under_contract": details.get("under_contract")
            or parse_contract_status(raw),
            "options": parse_options(raw),
            "condition": condition,
            "condition_summary": condition_summary,
        }
        details = merge_reliable_detail(details, parsed)
    return details


def latest_price_change(record: Car, db: Session) -> dict | None:
    quotes = money_values_out(record, db)
    # This legacy display is a single Encar price, never a bid/estimate widget.
    if record.source_key != "encar" or len(quotes) != 1:
        return None
    current_quote = quotes[0]
    if (current_quote["state"] != "KNOWN" or current_quote["amount_minor"] is None
            or current_quote["currency"] != "KRW"
            or current_quote["meaning"] not in {"SALE_TOTAL", "RENT_MONTHLY", "LEASE_MONTHLY"}):
        return None
    values = [current_quote["amount_minor"]]
    for point in db.scalars(
        select(PriceHistory)
        .where(
            PriceHistory.car_id == record.id,
            PriceHistory.integrity_status == "VALID",
        )
        .order_by(PriceHistory.created_at.desc(), PriceHistory.id.desc())
        .limit(30)
    ):
        historical = payload_money_values(point.payload or {}, record.source_key)
        if len(historical) != 1 or not comparable_money(historical[0], current_quote):
            break  # Do not skip a change of basis to find an older unrelated price.
        value = historical[0]["amount_minor"]
        if value not in values:
            values.append(value)
        if len(values) == 2:
            break
    if len(values) < 2 or values[0] == values[1] or values[1] == 0:
        return None
    current, previous = values
    difference = current - previous
    return {
        "direction": "DOWN" if difference < 0 else "UP",
        "previous": previous,
        "current": current,
        "difference": abs(difference),
        "percent": round(abs(difference) / previous * 100, 2),
    }


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": settings.app_version}


@app.get("/storage/{file_path:path}")
def storage_file(
    file_path: str,
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    requested = image_storage_path(file_path, storage_path)
    root = storage_path.resolve()
    if requested is None:
        raise HTTPException(404, "file_not_found")
    relative = requested.relative_to(root)
    if not requested.is_file() or len(relative.parts) < 3 or relative.parts[0] != "cars":
        raise HTTPException(404, "file_not_found")
    if len(relative.parts) == 4 and relative.parts[1] == "by-id" and relative.parts[2].isdigit():
        car_id = db.scalar(select(Car.id).where(Car.id == int(relative.parts[2])))
    elif len(relative.parts) == 3:
        car_id = db.scalar(select(Car.id).where(Car.source_key == "encar", Car.source_listing_id == relative.parts[1]))
    else:
        car_id = None
    if not car_id:
        raise HTTPException(404, "file_not_found")
    current_images = image_payloads(car_id, db)
    if not any(payload.get("url") == f"/storage/{relative.as_posix()}" for payload in current_images.values()):
        raise HTTPException(404, "file_not_found")
    accessible_car_relation(
        car_id,
        current,
        db,
        require_tracking=False,
    )
    image_types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                   ".webp": "image/webp", ".gif": "image/gif"}
    media_type = image_types.get(requested.suffix.lower())
    if media_type is None:
        raise HTTPException(404, "file_not_found")
    return FileResponse(requested, media_type=media_type, headers={"X-Content-Type-Options": "nosniff"})


@app.get("/api/projects")
def projects(
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
    source_key: str | None = None,
) -> list[dict]:
    query = select(Project).where(Project.owner_id == current.id)
    if source_key is not None:
        query = query.where(Project.source_key == source_key)
    return [
        project_out(project, db)
        for project in db.scalars(query.order_by(Project.updated_at.desc()))
    ]


@app.post("/api/projects", status_code=201)
def create_project(
    body: ProjectIn,
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    search_config = None
    if body.search_selection_id is not None:
        root = selection_root(body.source_key, db)
        try:
            selected = search_selections.current(root, current.id, body.source_key, draft_id=body.search_draft_id)
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(409, 'search_selection_changed') from None
        if (not selected or selected['id'] != body.search_selection_id
                or selected.get('project_id') is not None
                or not selected['can_use'] or selected['status'] != 'CLOSED'
                or selected['snapshot']['creation_url'] != body.search_url):
            raise HTTPException(409, 'search_selection_changed')
        search_config = deepcopy(selected['snapshot'])
    search_identity = url_identity_v1(body.search_url)
    if search_config:
        identity_for_config = getattr(get_source(body.source_key), 'search_identity', None)
        if identity_for_config:
            search_identity = identity_for_config(search_config)
    locked_user = db.scalar(
        select(User).where(User.id == current.id).with_for_update()
    )
    project_count = (
        db.scalar(
            select(func.count())
            .select_from(Project)
            .where(Project.owner_id == current.id)
        )
        or 0
    )
    if (
        locked_user
        and locked_user.project_limit is not None
        and project_count >= locked_user.project_limit
    ):
        raise HTTPException(403, "project_limit_reached")
    project = Project(
        owner_id=current.id,
        source_key=body.source_key,
        name=body.name.strip(),
        name_key=body.name.strip().casefold(),
        search_url=body.search_url,
        search_config=search_config,
        search_identity=search_identity,
        telegram_url=body.telegram_url,
        scan_mode=body.scan_mode,
        search_page_mode=body.search_page_mode,
        price_filter_mode=body.price_filter_mode,
        price_min_krw=body.price_min_krw,
        price_max_krw=body.price_max_krw,
        auto_update=body.auto_update,
    )
    db.add(project)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "project_name_or_url_exists")
    audit(
        db,
        "PROJECT_CREATED",
        actor_user_id=current.id,
        entity_type="PROJECT",
        entity_id=project.id,
    )
    db.commit()
    return project_out(project, db)


def selection_root(source_key: str, db: Session):
    from .source_test_profile import validate_source_test_isolation, SourceTestModeError
    if not settings.source_test_mode:
        raise HTTPException(409, 'source_test_profile_required')
    try:
        validate_source_test_isolation(settings, database_url=db.get_bind().url)
        source = get_source(source_key)
        if not getattr(source, 'selection_entry_url', None):
            raise ValueError('unsupported_selection')
    except (ValueError, SourceTestModeError, SourceUnavailableError):
        raise HTTPException(409, 'source_selection_unavailable') from None
    return Path(settings.storage_root).parent


@app.get('/api/source-search/{source_key}/selection')
def get_search_selection(source_key: str, current: User = Depends(require_user), db: Session = Depends(get_db),
                         draft_id: str | None = Query(default=None, pattern=r'^[a-f0-9]{32}$')):
    root = selection_root(source_key, db)
    try:
        return {'selection': search_selections.current(root, current.id, source_key, draft_id=draft_id)}
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(503, 'selection_state_unreadable') from None


@app.post('/api/source-search/{source_key}/selection')
def change_search_selection(source_key: str, body: SearchSelectionIn,
                            current: User = Depends(require_csrf), db: Session = Depends(get_db)):
    from .desktop_license import SearchEntitlementError, require_search_entitlement
    from .maintenance import maintenance_active
    root = selection_root(source_key, db)
    project = owned_project(body.project_id, current, db) if body.project_id is not None else None
    if project and (project.source_key != source_key or body.draft_id is None):
        raise HTTPException(409, 'search_selection_changed')
    if body.action != 'OPEN':
        try:
            selected = search_selections.current(root, current.id, source_key, draft_id=body.draft_id)
            if not selected or selected.get('project_id') != body.project_id:
                raise ValueError('selection_project_changed')
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(409, 'search_selection_changed') from None
    if body.action == 'CANCEL':
        try:
            return {'selection': search_selections.cancel(root, current.id, source_key, body.selection_id, draft_id=body.draft_id)}
        except ValueError:
            raise HTTPException(409, 'selection_not_ready') from None
    if maintenance_active():
        raise HTTPException(409, 'desktop_maintenance_active')
    try:
        require_search_entitlement(source_key)
    except SearchEntitlementError as exc:
        raise HTTPException(402, exc.code.lower()) from exc
    try:
        selection = (search_selections.start(root, current.id, source_key, locale=current.preferred_locale, draft_id=body.draft_id,
                         **({'project_id': project.id, 'initial_snapshot': project.search_config, 'entry_url': project.search_url} if project else {})) if body.action == 'OPEN'
                     else search_selections.confirm(root, current.id, source_key, body.selection_id, draft_id=body.draft_id))
        return {'selection': selection}
    except (ValueError, InvalidSourceUrlError):
        raise HTTPException(409, 'selection_not_ready') from None
    except OSError:
        raise HTTPException(503, 'selection_state_unwritable') from None


@app.get("/api/projects/{project_id}")
def get_project(
    project_id: int,
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    project = owned_project(project_id, current, db)
    return project_out(project, db)


def telegram_project_scope(project):
    return [project.source_key, project.created_at.isoformat()]


@app.get("/api/telegram/recipients")
def telegram_recipients(current: User = Depends(require_user), db: Session = Depends(get_db)):
    projects = db.scalars(select(Project).where(Project.owner_id == current.id)).all()
    try:
        return telegram_pairing.saved_recipients(current.id,
            [(project.id, telegram_project_scope(project)) for project in projects])
    except (OSError, ValueError, telegram_pairing.sqlite3.Error):
        raise HTTPException(503, "telegram_pairing_storage_failed") from None


@app.get("/api/projects/{project_id}/telegram")
def project_telegram(project_id: int, source_key: str | None = None,
                     current: User = Depends(require_user), db: Session = Depends(get_db)):
    project = owned_project(project_id, current, db)
    if source_key and source_key != project.source_key:
        raise HTTPException(404, "project_not_found")
    try:
        return telegram_pairing.project_status(current.id, project.id, telegram_project_scope(project))
    except (OSError, ValueError, telegram_pairing.sqlite3.Error):
        raise HTTPException(503, "telegram_pairing_storage_failed") from None


@app.post("/api/projects/{project_id}/telegram")
async def change_project_telegram(project_id: int, request: Request, source_key: str | None = None,
                                 current: User = Depends(require_csrf), db: Session = Depends(get_db)):
    project = owned_project(project_id, current, db)
    if source_key and source_key != project.source_key:
        raise HTTPException(404, "project_not_found")
    body = await telegram_pairing.pairing_body(request)
    try:
        return await run_in_threadpool(telegram_pairing.change_project, current.id, project.id, telegram_project_scope(project), body)
    except TelegramError as error:
        raise HTTPException(error.status, error.code) from None

@app.post('/api/telegram/automatic-clock')
def telegram_automatic_clock(body: telegram_automatic.AutomaticClock, current: User = Depends(require_csrf)):
    try:
        with telegram_pairing.LOCK:
            telegram_automatic.update_clock(telegram_dispatch.configuration(), current.id, body.computer_timezone, int(now_utc().timestamp()*1000))
        return {'ok': True}
    except TelegramError as error:
        raise HTTPException(error.status, error.code) from None
    except (OSError, ValueError, telegram_pairing.sqlite3.Error):
        raise HTTPException(503, 'telegram_delivery_storage_failed') from None


@app.get('/api/projects/{project_id}/telegram-automatic')
def telegram_automatic_status(project_id: int, source_key: str | None = None,
                              current: User = Depends(require_user), db: Session = Depends(get_db)):
    project = owned_project(project_id, current, db)
    if source_key and source_key != project.source_key:
        raise HTTPException(404, 'project_not_found')
    try:
        with telegram_pairing.LOCK:
            return telegram_automatic.status(telegram_dispatch.configuration(), current.id, project, int(now_utc().timestamp()*1000))
    except TelegramError as error:
        raise HTTPException(error.status, error.code) from None
    except (OSError, ValueError, telegram_pairing.sqlite3.Error):
        raise HTTPException(503, 'telegram_delivery_storage_failed') from None


@app.post('/api/projects/{project_id}/telegram-automatic')
def configure_telegram_automatic(project_id: int, body: telegram_automatic.AutomaticPolicy, source_key: str | None = None,
                                 current: User = Depends(require_csrf), db: Session = Depends(get_db)):
    project = owned_project(project_id, current, db)
    if source_key and source_key != project.source_key:
        raise HTTPException(404, 'project_not_found')
    try:
        with telegram_pairing.LOCK:
            result = telegram_automatic.configure(telegram_dispatch.configuration(), current.id, project, body, int(now_utc().timestamp()*1000))
        telegram_dispatch.service.kick()
        return result
    except TelegramError as error:
        raise HTTPException(error.status, error.code) from None
    except (OSError, ValueError, telegram_pairing.sqlite3.Error):
        raise HTTPException(503, 'telegram_delivery_storage_failed') from None


@app.get('/api/projects/{project_id}/telegram-reschedules')
def telegram_reschedule_status(project_id: int, source_key: str | None = None,
                               current: User = Depends(require_user), db: Session = Depends(get_db)):
    project = owned_project(project_id, current, db)
    if source_key and source_key != project.source_key:
        raise HTTPException(404, 'project_not_found')
    try:
        with telegram_pairing.LOCK:
            return telegram_reschedules.status(telegram_dispatch.configuration(), current.id, project, int(now_utc().timestamp()*1000))
    except TelegramError as error:
        raise HTTPException(error.status, error.code) from None
    except (OSError, ValueError, telegram_pairing.sqlite3.Error):
        raise HTTPException(503, 'telegram_delivery_storage_failed') from None


@app.post('/api/projects/{project_id}/telegram-reschedules')
def configure_telegram_reschedules(project_id: int, body: telegram_automatic.AutomaticPolicy, source_key: str | None = None,
                                   current: User = Depends(require_csrf), db: Session = Depends(get_db)):
    project = owned_project(project_id, current, db)
    if source_key and source_key != project.source_key:
        raise HTTPException(404, 'project_not_found')
    try:
        with telegram_pairing.LOCK:
            result = telegram_reschedules.configure(telegram_dispatch.configuration(), current.id, project, body, int(now_utc().timestamp()*1000))
        telegram_dispatch.service.kick()
        return result
    except TelegramError as error:
        raise HTTPException(error.status, error.code) from None
    except (OSError, ValueError, telegram_pairing.sqlite3.Error):
        raise HTTPException(503, 'telegram_delivery_storage_failed') from None


@app.post('/api/projects/{project_id}/telegram-deliveries')
async def create_telegram_delivery(project_id: int, request: Request, source_key: str | None = None,
                                   current: User = Depends(require_csrf), db: Session = Depends(get_db)):
    project = owned_project(project_id, current, db)
    if source_key and source_key != project.source_key:
        raise HTTPException(404, 'project_not_found')
    if not is_auction_source(project.source_key):
        raise HTTPException(400, 'not_an_auction_source')
    body = await telegram_dispatch.delivery_body(request)
    def enqueue():
        with telegram_pairing.LOCK:
            config = telegram_dispatch.configuration()
            result = telegram_dispatch.enqueue(config, current, project, db, body, now=now_utc())
        telegram_dispatch.service.kick()
        return result
    try:
        return await run_in_threadpool(enqueue)
    except TelegramError as error:
        raise HTTPException(error.status, error.code) from None
    except (OSError, ValueError, telegram_pairing.sqlite3.Error):
        raise HTTPException(503, 'telegram_delivery_storage_failed') from None

@app.get('/api/projects/{project_id}/telegram-history')
def telegram_delivery_history(project_id: int, source_key: str | None = None,
                               current: User = Depends(require_user), db: Session = Depends(get_db)):
    project = owned_project(project_id, current, db)
    if source_key and source_key != project.source_key:
        raise HTTPException(404, 'project_not_found')
    config, issue = telegram_pairing.configuration()
    if issue:
        return dict(project_id=project_id, source_key=project.source_key, sent_car_ids=[])
    try:
        with telegram_pairing.LOCK:
            session = telegram_pairing.PairingSession(config, current.id, project.id, telegram_project_scope(project))
            return dict(project_id=project_id, source_key=project.source_key,
                sent_car_ids=telegram_dispatch.DeliveryBox(session.box.path).history(current.id, project.id, session.scope))
    except (OSError, ValueError, telegram_pairing.sqlite3.Error):
        raise HTTPException(503, 'telegram_delivery_storage_failed') from None

@app.get('/api/projects/{project_id}/telegram-deliveries/{intent_id}')
def get_telegram_delivery(project_id: int, intent_id: str, source_key: str | None = None,
                          current: User = Depends(require_user), db: Session = Depends(get_db)):
    project = owned_project(project_id, current, db)
    if source_key and source_key != project.source_key:
        raise HTTPException(404, 'project_not_found')
    try:
        with telegram_pairing.LOCK:
            config = telegram_dispatch.configuration()
            return telegram_dispatch.read_batch(config, current.id, project, intent_id)
    except TelegramError as error:
        raise HTTPException(error.status, error.code) from None
    except (OSError, ValueError, telegram_pairing.sqlite3.Error):
        raise HTTPException(503, 'telegram_delivery_storage_failed') from None

@app.post('/api/projects/{project_id}/telegram-deliveries/{intent_id}/cancel')
def cancel_telegram_delivery(project_id: int, intent_id: str, source_key: str | None = None,
                             current: User = Depends(require_csrf), db: Session = Depends(get_db)):
    project = owned_project(project_id, current, db)
    if source_key and source_key != project.source_key:
        raise HTTPException(404, 'project_not_found')
    try:
        with telegram_pairing.LOCK:
            return telegram_dispatch.cancel_batch(telegram_dispatch.configuration(), current.id, project, intent_id)
    except TelegramError as error:
        raise HTTPException(error.status, error.code) from None
    except (OSError, ValueError, telegram_pairing.sqlite3.Error):
        raise HTTPException(503, 'telegram_delivery_storage_failed') from None


@app.post('/api/projects/{project_id}/search-selection')
def update_project_search_selection(project_id: int, body: ProjectSearchSelectionIn,
                                    current: User = Depends(require_csrf), db: Session = Depends(get_db)):
    from .desktop_license import SearchEntitlementError, require_search_entitlement
    from .maintenance import maintenance_active
    project = owned_project(project_id, current, db)
    root = selection_root(project.source_key, db)
    if maintenance_active():
        raise HTTPException(409, 'desktop_maintenance_active')
    try:
        require_search_entitlement(project.source_key)
    except SearchEntitlementError as exc:
        raise HTTPException(402, exc.code.lower()) from exc
    try:
        selected = search_selections.current(root, current.id, project.source_key, draft_id=body.draft_id)
        if (not selected or selected['id'] != body.selection_id or selected.get('project_id') != project.id
                or not selected['can_use'] or selected['status'] != 'CLOSED'
                or not selected['snapshot'].get('creation_url')):
            raise ValueError('unconfirmed_selection')
        source = get_source(project.source_key)
        config = source.validate_selection_snapshot(selected['snapshot'])
        url = source.validate_search_url(config['creation_url'])
    except (OSError, ValueError, KeyError, TypeError, InvalidSourceUrlError):
        raise HTTPException(409, 'search_selection_changed') from None
    identity = getattr(source, 'search_identity', lambda _: url_identity_v1(url))(config)
    with SCAN_COMMAND_LOCK:
        db.refresh(project)
        if project.price_filter_revision != body.expected_revision:
            raise HTTPException(409, 'project_search_changed')
        active = list(db.scalars(select(ScanRun).where(ScanRun.owner_id == current.id,
            ScanRun.status.in_(['QUEUED', 'RUNNING', 'CANCEL_REQUESTED']))))
        if project.id in _busy_project_runs(db, active, {project.id}):
            raise HTTPException(409, 'project_search_active')
        project.search_config, project.search_url, project.search_identity = deepcopy(config), url, identity
        project.price_filter_revision += 1
        project.updated_at = datetime.now(timezone.utc)
        try:
            db.flush()
            audit(db, 'PROJECT_UPDATED', actor_user_id=current.id, entity_type='PROJECT', entity_id=project.id,
                  payload={'fields': ['search_config', 'search_url'], 'search_revision': project.price_filter_revision})
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, 'project_name_or_url_exists') from None
    return project_out(project, db)


@app.patch("/api/projects/{project_id}")
def patch_project(
    project_id: int,
    body: ProjectPatch,
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    project = owned_project(project_id, current, db)
    changes = body.model_dump(exclude_unset=True)
    if 'search_url' in changes and project.search_config:
        raise HTTPException(409, 'search_selection_required')
    next_price_mode = changes.get("price_filter_mode", project.price_filter_mode)
    next_price_minimum = changes.get("price_min_krw", project.price_min_krw)
    next_price_maximum = changes.get("price_max_krw", project.price_max_krw)
    try:
        validate_price_filter(next_price_mode, next_price_minimum, next_price_maximum)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    changes_search_conditions = any(
        key in changes
        for key in {
            "search_url",
            "price_filter_mode",
            "price_min_krw",
            "price_max_krw",
        }
    ) and (
        next_price_mode != project.price_filter_mode
        or next_price_minimum != project.price_min_krw
        or next_price_maximum != project.price_max_krw
        or changes.get("search_url", project.search_url) != project.search_url
    )
    for key, value in changes.items():
        setattr(
            project,
            key,
            value.strip() if key in {"name", "telegram_url"} and value else value,
        )
    if body.name:
        project.name_key = body.name.strip().casefold()
    if changes_search_conditions:
        project.price_filter_revision += 1
    if 'search_url' in changes:
        project.search_identity = url_identity_v1(project.search_url)
    project.updated_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "project_name_or_url_exists")
    audit(
        db,
        "PROJECT_UPDATED",
        actor_user_id=current.id,
        entity_type="PROJECT",
        entity_id=project.id,
        payload={"fields": sorted(changes)},
    )
    db.commit()
    return project_out(project, db)


@app.delete("/api/projects/{project_id}", status_code=204)
def delete_project(
    project_id: int,
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Response:
    project = owned_project(project_id, current, db)
    affected_scans = detach_project_from_active_jobs(
        db,
        current.id,
        project.id,
    )
    audit(
        db,
        "PROJECT_DELETED",
        actor_user_id=current.id,
        entity_type="PROJECT",
        entity_id=project.id,
        payload={"name": project.name, "affected_scans": affected_scans},
    )
    db.delete(project)
    db.commit()
    return Response(status_code=204)


@app.get("/api/projects/{project_id}/cars")
def project_cars(
    project_id: int,
    favorite: bool | None = None,
    status: str | None = None,
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    owned_project(project_id, current, db)
    query = (
        select(ProjectCar, Car)
        .join(Car, Car.id == ProjectCar.car_id)
        .outerjoin(
            UserCarState,
            and_(
                UserCarState.user_id == current.id,
                UserCarState.car_id == Car.id,
            ),
        )
        .where(
            ProjectCar.project_id == project_id,
            ProjectCar.tracking_enabled.is_(True),
            Car.excluded.is_(False),
            or_(
                UserCarState.user_id.is_(None),
                UserCarState.excluded.is_(False),
            ),
        )
    )
    if favorite is not None:
        query = query.where(ProjectCar.favorite == favorite)
    if status:
        if status in {"UNAVAILABLE", "NOT_FOUND_IN_SEARCH"}:
            query = query.where(
                ProjectCar.search_status == "NOT_FOUND_IN_SEARCH"
            )
        elif status == "NEW":
            query = query.where(ProjectCar.is_new.is_(True))
        else:
            query = query.where(Car.status == status)
    result = []
    for relation, car in db.execute(query.order_by(Car.updated_at.desc())):
        details = enriched_details(car)
        result.append(
            {
                "id": car.id,
                "source_key": car.source_key,
                "source_listing_id": car.source_listing_id,
                "encar_id": car.canonical_encar_id,
                "url": car.url,
                "title": car.title,
                "price": car.current_price,
                "money_values": money_values_out(car, db),
                "status": effective_status(car, relation),
                "details": details,
                "favorite": relation.favorite,
                "viewed": relation.viewed,
                "missing_scans": relation.consecutive_missing_scans,
                "updated_at": car.updated_at,
                "first_seen_at": relation.first_seen_at,
                "image": (image_payloads(car.id, db).get("MAIN") or {}).get("url"),
                "price_change": latest_price_change(car, db),
            }
        )
    return group_vehicle_listings(result)


@app.get("/api/projects/{project_id}/auction-lots")
def project_auction_lots(
    project_id: int,
    computer_timezone: str = Depends(auction_computer_timezone),
    today_only: bool = False,
    collection: Literal["active", "today", "future", "undated", "archive"] = "active",
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    project = owned_project(project_id, current, db)
    if not is_auction_source(project.source_key):
        raise HTTPException(400, "not_an_auction_source")
    if today_only and collection not in {"active", "today"}:
        raise HTTPException(400, "conflicting_auction_collection")
    result = project_auction_view(db, project, now=now_utc(), computer_timezone=computer_timezone, today_only=today_only, collection=collection)
    for lot in result["lots"]:
        lot["image"] = (image_payloads(lot["id"], db).get("MAIN") or {}).get("url") or lot["image"]
    return result


@app.get("/api/favorites")
def favorite_cars(
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
    source_key: str | None = None,
) -> list[dict]:
    result = []
    query = (
        select(ProjectCar, Car, Project)
        .join(Car, Car.id == ProjectCar.car_id)
        .join(Project, Project.id == ProjectCar.project_id)
        .outerjoin(
            UserCarState,
            and_(
                UserCarState.user_id == current.id,
                UserCarState.car_id == Car.id,
            ),
        )
        .where(
            Project.owner_id == current.id,
            ProjectCar.favorite.is_(True),
            ProjectCar.tracking_enabled.is_(True),
            Car.excluded.is_(False),
            or_(
                UserCarState.user_id.is_(None),
                UserCarState.excluded.is_(False),
            ),
        )
    )
    if source_key is not None:
        query = query.where(Project.source_key == source_key, Car.source_key == source_key)
    rows = db.execute(query.order_by(Car.updated_at.desc())).all()
    for relation, car, project in rows:
        result.append(
            {
                "id": car.id,
                "source_key": car.source_key,
                "source_listing_id": car.source_listing_id,
                "project_id": project.id,
                "project_name": project.name,
                "encar_id": car.canonical_encar_id,
                "url": car.url,
                "title": car.title,
                "price": car.current_price,
                "money_values": money_values_out(car, db),
                "status": effective_status(car, relation),
                "details": enriched_details(car),
                "favorite": relation.favorite,
                "viewed": relation.viewed,
                "missing_scans": relation.consecutive_missing_scans,
                "updated_at": car.updated_at,
                "first_seen_at": relation.first_seen_at,
                "image": (image_payloads(car.id, db).get("MAIN") or {}).get("url"),
                "price_change": latest_price_change(car, db),
            }
        )
    return result


@app.get("/api/car-lookup")
def lookup_car(
    q: str = Query(min_length=1, max_length=500),
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    value = q.strip()
    encar_id = extract_car_id(value) or (value if value.isdigit() and len(value) >= 6 else None)
    if not encar_id:
        return {"found": False, "reason": "invalid_encar_url"}
    record = db.scalar(select(Car).where(Car.source_key == "encar", Car.source_listing_id == encar_id))
    if not record:
        record = db.scalar(
            select(Car)
            .join(CarAlias, CarAlias.car_id == Car.id)
            .where(CarAlias.source_key == "encar", Car.source_key == "encar", CarAlias.alias_id == encar_id)
        )
    if not record:
        return {"found": False, "encar_id": encar_id, "reason": "not_in_database"}
    project_rows = db.execute(
        select(ProjectCar, Project)
        .join(Project, ProjectCar.project_id == Project.id)
        .where(
            ProjectCar.car_id == record.id,
            Project.owner_id == current.id,
        )
        .order_by(Project.updated_at.desc())
    ).all()
    if not project_rows:
        return {"found": False, "encar_id": encar_id, "reason": "not_in_database"}
    projects = [
        {"id": row.Project.id, "name": row.Project.name}
        for row in project_rows
        if row.ProjectCar.tracking_enabled
    ]
    lookup_relation = project_rows[0].ProjectCar if project_rows else None
    state = db.get(UserCarState, {"user_id": current.id, "car_id": record.id})
    return {
        "found": True,
        "encar_id": encar_id,
        "project_id": (
            projects[0]["id"]
            if projects
            else lookup_relation.project_id if lookup_relation else None
        ),
        "projects": projects,
        "car": {
            "id": record.id,
            "encar_id": record.canonical_encar_id,
            "url": record.url,
            "title": record.title,
            "price": record.current_price,
            "money_values": money_values_out(record, db),
            "status": effective_status(record, lookup_relation),
            "details": enriched_details(record),
            "image": (image_payloads(record.id, db).get("MAIN") or {}).get("url"),
            "price_change": latest_price_change(record, db),
            "excluded": bool(state and state.excluded),
            "integrity_status": record.integrity_status,
            "integrity_reason": record.integrity_reason,
        },
    }


def relation(
    project_id: int,
    car_id: int,
    user: User,
    db: Session,
    *,
    require_tracking: bool = True,
) -> ProjectCar:
    return accessible_car_relation(
        car_id,
        user,
        db,
        project_id,
        require_tracking=require_tracking,
    )


@app.delete("/api/projects/{project_id}/cars/{car_id}", status_code=204)
def remove_car_from_project(
    project_id: int,
    car_id: int,
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Response:
    project_car = relation(project_id, car_id, current, db)
    project_car.tracking_enabled = False
    project_car.search_status = "REMOVED_FROM_PROJECT"
    project_car.favorite = False
    db.add(
        CarEvent(
            car_id=car_id,
            kind="REMOVED_FROM_PROJECT",
            user_id=current.id,
            project_id=project_id,
            payload={"project_id": project_id},
        )
    )
    db.commit()
    return Response(status_code=204)


@app.patch("/api/projects/{project_id}/cars/{car_id}/favorite")
def favorite(
    project_id: int,
    car_id: int,
    body: BoolPatch,
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    project_car = relation(project_id, car_id, current, db)
    project_car.favorite = body.value
    db.add(
        CarEvent(
            car_id=car_id,
            kind="FAVORITE_ADDED" if body.value else "FAVORITE_REMOVED",
            user_id=current.id,
            project_id=project_id,
            payload={"project_id": project_id},
        )
    )
    db.commit()
    return {"favorite": project_car.favorite}


@app.patch("/api/projects/{project_id}/cars/{car_id}/viewed")
def viewed(
    project_id: int,
    car_id: int,
    body: BoolPatch,
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    project_car = relation(project_id, car_id, current, db)
    if project_car.viewed == body.value:
        return {"viewed": project_car.viewed}
    project_car.viewed = body.value
    project_car.viewed_at = datetime.now(timezone.utc) if body.value else None
    db.add(
        CarEvent(
            car_id=car_id,
            kind="USER_VIEWED",
            user_id=current.id,
            project_id=project_id,
            payload={"project_id": project_id, "value": body.value},
        )
    )
    db.commit()
    return {"viewed": project_car.viewed}


@app.get("/api/cars/{car_id}")
def car(
    car_id: int,
    project_id: int | None = Query(None),
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    record = get_car_or_404(car_id, db)
    project_car = accessible_car_relation(
        car_id,
        current,
        db,
        project_id,
        require_tracking=False,
    )
    state = user_car_state(current.id, car_id, db)
    return {
        "id": record.id,
        "source_key": record.source_key,
        "source_listing_id": record.source_listing_id,
        "encar_id": record.canonical_encar_id,
        "url": record.url,
        "title": record.title,
        "price": record.current_price,
        "money_values": money_values_out(record, db),
        "status": effective_status(record, project_car),
        "details": enriched_details(record),
        "comment": state.comment,
        "comment_updated_at": state.comment_updated_at,
        "rating": state.rating,
        "images": image_payloads(record.id, db),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "first_seen_at": project_car.first_seen_at if project_car else record.created_at,
        "last_seen_at": project_car.last_seen_at if project_car else None,
        "favorite": project_car.favorite if project_car else False,
        "viewed": project_car.viewed if project_car else False,
        "excluded": state.excluded,
        "integrity_status": record.integrity_status,
        "integrity_reason": record.integrity_reason,
    }


@app.get("/api/cars/{car_id}/auction")
def car_auction(
    car_id: int,
    computer_timezone: str = Depends(auction_computer_timezone),
    project_id: int | None = Query(None),
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    relation = accessible_car_relation(car_id, current, db, project_id, require_tracking=False)
    record = get_car_or_404(car_id, db)
    if record.source_key != db.get(Project, relation.project_id).source_key:
        raise HTTPException(404, "project_car_not_found")
    if not is_auction_source(record.source_key):
        raise HTTPException(400, "not_an_auction_source")
    result = single_auction_view(db, car_id, now=now_utc(), computer_timezone=computer_timezone)
    result["lot"]["image"] = (image_payloads(car_id, db).get("MAIN") or {}).get("url") or result["lot"]["image"]
    return result


@app.patch("/api/cars/{car_id}/comment")
def comment(
    car_id: int,
    body: CommentPatch,
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    accessible_car_relation(car_id, current, db, require_tracking=False)
    state = user_car_state(current.id, car_id, db)
    state.comment = body.comment
    state.comment_updated_at = now_utc()
    db.add(
        CarEvent(
            car_id=car_id,
            kind="COMMENT_UPDATED",
            user_id=current.id,
            payload={},
        )
    )
    db.commit()
    return {"comment": state.comment, "comment_updated_at": state.comment_updated_at}


@app.patch("/api/cars/{car_id}/rating")
def rating(
    car_id: int,
    body: RatingPatch,
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    accessible_car_relation(car_id, current, db, require_tracking=False)
    state = user_car_state(current.id, car_id, db)
    state.rating = body.rating
    db.add(
        CarEvent(
            car_id=car_id,
            kind="RATING_UPDATED",
            user_id=current.id,
            payload={"rating": body.rating},
        )
    )
    db.commit()
    return {"rating": state.rating}


@app.post("/api/cars/{car_id}/refresh", status_code=202)
def refresh_car(
    car_id: int,
    project_id: int | None = Query(None),
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    get_car_or_404(car_id, db)
    if project_id is not None:
        relation(
            project_id,
            car_id,
            current,
            db,
            require_tracking=False,
        )
    else:
        accessible_car_relation(
            car_id,
            current,
            db,
            require_tracking=False,
        )
    return enqueue(
        "CARS",
        {"car_ids": [car_id], "project_id": project_id},
        current,
        db,
    )


@app.post("/api/cars/{car_id}/exclude")
def exclude(
    car_id: int,
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    accessible_car_relation(car_id, current, db, require_tracking=False)
    state = user_car_state(current.id, car_id, db)
    state.excluded = True
    for project_car in db.scalars(
        select(ProjectCar)
        .join(Project, Project.id == ProjectCar.project_id)
        .where(
            ProjectCar.car_id == car_id,
            Project.owner_id == current.id,
        )
    ):
        project_car.tracking_enabled = False
        project_car.search_status = "REMOVED_FROM_PROJECT"
        project_car.favorite = False
    db.add(
        CarEvent(
            car_id=car_id,
            kind="USER_EXCLUDED",
            user_id=current.id,
            payload={},
        )
    )
    db.commit()
    return {"excluded": True}


@app.get("/api/cars/{car_id}/events")
def events(
    car_id: int,
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    accessible_car_relation(car_id, current, db, require_tracking=False)
    owned_project_ids = select(Project.id).where(Project.owner_id == current.id)
    return [
        {"kind": event.kind, "payload": event.payload, "created_at": event.created_at}
        for event in db.scalars(
            select(CarEvent)
            .where(
                CarEvent.car_id == car_id,
                CarEvent.integrity_status == "VALID",
                or_(
                    CarEvent.user_id == current.id,
                    CarEvent.project_id.in_(owned_project_ids),
                    and_(
                        CarEvent.user_id.is_(None),
                        CarEvent.project_id.is_(None),
                    ),
                ),
            )
            .order_by(CarEvent.created_at.desc())
        )
    ]


def price_history_out(car_id: int, db: Session) -> list[dict]:
    record = db.get(Car, car_id)
    return [
        {
            "price": (point.payload or {}).get("price_krw"),
            "offer_type": (point.payload or {}).get("offer_type") or ("SALE" if record.source_key == "encar" else None),
            "money_values": payload_money_values(point.payload or {}, record.source_key),
            "at": (point.payload or {}).get("checked_at") or point.created_at,
        }
        for point in db.scalars(
            select(PriceHistory)
            .where(
                PriceHistory.car_id == car_id,
                PriceHistory.integrity_status == "VALID",
            )
            .order_by(PriceHistory.created_at, PriceHistory.id)
        )
    ]


@app.get("/api/cars/{car_id}/price-history")
def prices(
    car_id: int,
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    accessible_car_relation(car_id, current, db, require_tracking=False)
    return price_history_out(car_id, db)


@app.get("/api/cars/{car_id}/chatgpt-prompt")
def prompt(
    car_id: int,
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    accessible_car_relation(car_id, current, db, require_tracking=False)
    record = get_car_or_404(car_id, db)
    history = price_history_out(car_id, db)
    details = record.details or {}
    return {
        "prompt": f"""Analyze this Encar Korea listing. Do not invent missing data. Explain Korean terms, assess accident and repair risks, identify everything unverified, and finish with a concise verdict.

Vehicle: {record.title}
Current price: {record.current_price} KRW
Mileage: {details.get('mileage_km')}
Fuel: {details.get('fuel') or details.get('fuel_text_detected')}
Drivetrain: {details.get('drivetrain')}
Insurance/accident evidence: {details.get('accident') or details.get('accident_lines')}
Options: {details.get('options') or details.get('options_detected')}
Original Korean lines: {details.get('raw_korean_lines') or details.get('accident_lines')}
Price history: {history}
Encar URL: {record.url}"""
    }


def enqueue(kind: str, payload: dict, owner: User, db: Session) -> dict:
    with SCAN_COMMAND_LOCK:
        return _enqueue_unlocked(kind, payload, owner, db)


def _project_payload_subset(payload: dict, project_ids: list[int]) -> dict:
    allowed = set(project_ids)
    result = {**payload, "project_ids": project_ids}
    plan_by_project = {
        plan["project_id"]: plan
        for plan in payload.get("project_plans") or []
        if plan.get("project_id") in allowed
    }
    plans = [plan_by_project[project_id] for project_id in project_ids if project_id in plan_by_project]
    if "project_plans" in payload:
        result["project_plans"] = plans
    if plans:
        result["source_keys"] = list(
            dict.fromkeys(plan.get("source_key") or "encar" for plan in plans)
        )
    return result


def _busy_project_runs(db: Session, active_runs: list[ScanRun], project_ids: set[int]) -> dict[int, ScanRun]:
    busy = {}
    for active in active_runs:
        payload = active.payload or {}
        active_ids = set(payload.get("project_ids") or []) - set(payload.get("cancelled_project_ids") or [])
        active_ids -= set(db.scalars(select(ProjectScanRun.project_id).where(
            ProjectScanRun.scan_run_id == active.id,
            ProjectScanRun.status.in_(["CANCELLED", "CANCEL_REQUESTED"]),
        )))
        for project_id in project_ids & active_ids:
            busy.setdefault(project_id, active)
    return busy


def _enqueue_unlocked(kind: str, payload: dict, owner: User, db: Session) -> dict:
    from .maintenance import maintenance_active
    from .desktop_license import SearchEntitlementError, require_search_entitlement
    from .source_test_profile import SourceTestModeError, validate_source_test_scan

    try:
        validate_source_test_scan(settings, kind, payload, database_url=db.get_bind().url)
    except SourceTestModeError as exc:
        raise HTTPException(409, exc.code.lower()) from exc
    if settings.source_test_mode:
        payload = {**payload, "source_test_mode": True}

    if maintenance_active():
        raise HTTPException(409, "desktop_maintenance_active")
    source_keys = set(payload.get("source_keys") or [])
    plans = {plan["project_id"]: plan for plan in payload.get("project_plans") or []}
    if kind == "PROJECTS":
        for project in db.scalars(select(Project).where(
            Project.owner_id == owner.id, Project.id.in_(payload.get("project_ids") or [])
        )):
            source_keys.add(plans.get(project.id, {}).get("source_key") or project.source_key)
    elif kind == "CARS":
        source_keys.update(db.scalars(select(Car.source_key).join(ProjectCar).join(Project).where(
            Project.owner_id == owner.id, Car.id.in_(payload.get("car_ids") or [])
        )))
    for source_key in sorted(source_keys) or [None]:
        try:
            require_search_entitlement(source_key)
        except SearchEntitlementError as exc:
            raise HTTPException(402, exc.code.lower()) from exc
    requested_project_ids = list(dict.fromkeys(payload.get("project_ids") or []))
    requested_projects = set(requested_project_ids)
    requested_cars = set(payload.get("car_ids") or [])
    active_runs = list(db.scalars(
        select(ScanRun).where(
            ScanRun.owner_id == owner.id,
            ScanRun.status.in_(["QUEUED", "RUNNING", "CANCEL_REQUESTED"])
        )
    ))
    if kind == "PROJECTS" and payload.get("trigger") == "MANUAL":
        merged_at = datetime.now(timezone.utc)
        for active in active_runs:
            active_payload = dict(active.payload or {})
            is_automatic = (
                active.kind == "PROJECTS"
                and (
                    active_payload.get("trigger") == "AUTOMATIC"
                    or active_payload.get("scheduled") is True
                )
            )
            overlap = requested_projects & set(
                active_payload.get("project_ids") or []
            )
            if not is_automatic or active.status != "QUEUED" or not overlap:
                continue
            remaining = [
                project_id
                for project_id in active_payload.get("project_ids") or []
                if project_id not in overlap
            ]
            if remaining:
                active.payload = _project_payload_subset(active_payload, remaining)
            else:
                active.status = "CANCELLED"
                active.finished_at = merged_at
                active.payload = {
                    **active_payload,
                    "cancelled_at": merged_at.isoformat(),
                    "cancellation_reason": "MERGED_INTO_MANUAL_RUN",
                }
        db.flush()
        active_runs = [
            active
            for active in active_runs
            if active.status in {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}
        ]

    busy_projects = _busy_project_runs(db, active_runs, requested_projects)
    for active in active_runs:
        active_payload = active.payload or {}
        if requested_cars & set(active_payload.get("car_ids") or []):
            raise HTTPException(409, "car_scan_already_active")

    project_results: list[dict] = []
    if kind == "PROJECTS":
        free_project_ids = [
            project_id
            for project_id in requested_project_ids
            if project_id not in busy_projects
        ]
        project_results = [
            {
                "project_id": project_id,
                "status": "ALREADY_ACTIVE",
                "scan_run_id": busy_projects[project_id].id,
            }
            for project_id in requested_project_ids
            if project_id in busy_projects
        ]
        if not free_project_ids:
            existing = busy_projects[requested_project_ids[0]]
            return {
                "id": existing.id,
                "status": "ALREADY_ACTIVE",
                "created": False,
                "projects": project_results,
            }
        payload = _project_payload_subset(
            payload, project_execution_order(db, owner.id, payload, free_project_ids),
        )

    run = ScanRun(
        owner_id=owner.id,
        kind=kind,
        status="QUEUED",
        payload=payload,
    )
    db.add(run)
    audit(
        db,
        "SCAN_QUEUED",
        actor_user_id=owner.id,
        entity_type="SCAN",
        payload={"kind": kind},
    )
    db.commit()
    result = {"id": run.id, "status": run.status}
    if kind == "PROJECTS":
        queued = [
            {
                "project_id": project_id,
                "status": "QUEUED",
                "scan_run_id": run.id,
            }
            for project_id in payload.get("project_ids") or []
        ]
        by_project = {
            item["project_id"]: item for item in [*project_results, *queued]
        }
        result.update(
            {
                "created": True,
                "projects": [
                    by_project[project_id] for project_id in requested_project_ids
                ],
            }
        )
    return result


def _legacy_project_error(
    raw_error: str | None,
    project_name: str,
    other_project_names: list[str],
) -> str:
    if not raw_error:
        return ""
    marker = f"{project_name}:"
    start = raw_error.find(marker)
    if start < 0:
        return raw_error[:4000]
    end = len(raw_error)
    for other_name in other_project_names:
        if other_name == project_name:
            continue
        position = raw_error.find(f"\n{other_name}:", start + len(marker))
        if position >= 0:
            end = min(end, position)
    return raw_error[start:end][:4000]


def _report_change_details(item: dict, db: Session) -> dict:
    enriched = dict(item)
    car_id = enriched.get("car_id")
    source_key = item.get("source_key") or "encar"
    if "money_values" not in enriched:
        enriched["money_values"] = payload_money_values(
            {**item, "price_krw": item.get("price")}, source_key
        )
    if "previous_money_values" not in enriched:
        before = db.get(CarSnapshot, item["before_snapshot_id"]) if item.get("before_snapshot_id") else None
        enriched["previous_money_values"] = (
            payload_money_values(before.payload or {}, source_key)
            if before and before.car_id == car_id and before.integrity_status == "VALID"
            else []  # Do not guess an old quote's currency/type from today's price.
        )
    if enriched.get("change") != "MATERIAL_UPDATE" or enriched.get("changes"):
        return enriched
    before_id = enriched.get("before_snapshot_id")
    after_id = enriched.get("after_snapshot_id")
    if before_id and after_id and before_id != after_id:
        before = db.get(CarSnapshot, before_id)
        after = db.get(CarSnapshot, after_id)
        if (
            before
            and after
            and before.car_id == car_id
            and after.car_id == car_id
            and before.integrity_status == "VALID"
            and after.integrity_status == "VALID"
        ):
            enriched["changes"] = material_changes(
                before.payload or {},
                after.payload or {},
            )
            if enriched["changes"]:
                return enriched
    updated_at = enriched.get("updated_at")
    if not car_id or not updated_at:
        enriched["details_unavailable"] = True
        return enriched
    try:
        cutoff = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
    except ValueError:
        enriched["details_unavailable"] = True
        return enriched
    snapshots = list(
        db.scalars(
            select(CarSnapshot)
            .where(
                CarSnapshot.car_id == car_id,
                CarSnapshot.integrity_status == "VALID",
                CarSnapshot.created_at <= cutoff + timedelta(minutes=1),
            )
            .order_by(CarSnapshot.created_at.desc())
            .limit(2)
        )
    )
    if len(snapshots) >= 2:
        enriched["changes"] = material_changes(
            snapshots[1].payload or {},
            snapshots[0].payload or {},
        )
    if not enriched.get("changes"):
        enriched["details_unavailable"] = True
    return enriched


def _scan_source_payload(run: ScanRun, db: Session, source_key: str) -> dict:
    """A read-only projection of one shared run, including legacy history."""
    payload = dict(run.payload or {})
    if payload.get("project_id") and not payload.get("car_ids"):
        payload["project_ids"] = [payload["project_id"]]
    plans = list(payload.get("project_plans") or [])
    reports = list(payload.get("report") or [])
    failures = list(payload.get("failures") or [])
    project_sources = {plan["project_id"]: plan.get("source_key") or "encar" for plan in plans}
    project_ids = set(payload.get("project_ids") or []) | set(project_sources)
    if payload.get("project_id"):
        project_ids.add(payload["project_id"])
    project_ids.update(item["project_id"] for item in [*reports, *failures] if item.get("project_id"))
    for project in db.scalars(select(Project).where(Project.id.in_(project_ids), Project.owner_id == run.owner_id)):
        project_sources.setdefault(project.id, project.source_key)
    # Tagged observations keep deleted projects' history readable. Frozen plans
    # take precedence over a later change to the stored project.
    for item in [*reports, *failures]:
        if item.get("project_id") and item.get("source_key"):
            project_sources.setdefault(item["project_id"], item["source_key"])
    if len(payload.get("source_keys") or []) == 1:
        for project_id in project_ids:
            project_sources.setdefault(project_id, payload["source_keys"][0])
    selected = {project_id for project_id, key in project_sources.items() if key == source_key}
    car_ids = set(payload.get("car_ids") or [])
    car_ids.update(item["car_id"] for item in [*reports, *failures] if item.get("car_id"))
    selected_cars = set(db.scalars(select(Car.id).where(Car.id.in_(car_ids), Car.source_key == source_key)))
    if not selected and not selected_cars and source_key not in (payload.get("source_keys") or []):
        raise HTTPException(404, "scan_not_found")

    def belongs(item):
        if item.get("source_key"):
            return item["source_key"] == source_key
        if item.get("project_id"):
            return item["project_id"] in selected
        if item.get("car_id"):
            return item["car_id"] in selected_cars
        return True  # An unscoped driver/job error affects all selected parts.

    payload.update({
        "project_ids": [project_id for project_id in payload.get("project_ids") or [] if project_id in selected],
        "project_plans": [plan for plan in plans if plan["project_id"] in selected],
        "car_ids": [car_id for car_id in payload.get("car_ids") or [] if car_id in selected_cars],
        "source_keys": [source_key], "report": [item for item in reports if belongs(item)],
        "failures": [item for item in failures if belongs(item)],
    })
    for field in ("project_statuses", "pagination", "detail_progress"):
        payload[field] = {key: value for key, value in (payload.get(field) or {}).items() if int(key) in selected}
    for field in ("cancelled_project_ids", "previous_project_ids"):
        if field in payload:
            payload[field] = [value for value in payload[field] if value in selected]
    if payload.get("current_project_id") not in selected:
        payload.pop("current_project_id", None)
    if payload.get("project_id") not in selected:
        payload.pop("project_id", None)
    if "launch_scope" in payload:
        payload["launch_scope"] = {"mode": "SITES", "source_keys": [source_key], "project_ids": payload["project_ids"]}
    if payload.get("execution_versions"):
        payload["execution_versions"] = {**payload["execution_versions"], "sources": {
            key: value for key, value in payload["execution_versions"].get("sources", {}).items() if key == source_key}}
    return payload


def scan_out(run: ScanRun, db: Session, source_key: str | None = None, *, report_car_ids: set[int] | None = None) -> dict:
    payload = dict(run.payload or {}) if source_key is None else _scan_source_payload(run, db, source_key)
    raw_report = list(payload.get("report") or [])
    if report_car_ids is not None:
        raw_report = [item for item in raw_report if item.get("car_id") in report_car_ids]
    invalidated_report = [
        item
        for item in raw_report
        if item.get("integrity_status") == "INVALIDATED"
    ]
    raw_report = [
        item
        for item in raw_report
        if item.get("integrity_status") != "INVALIDATED"
        and item.get("change") != "RELISTED"
    ]
    payload["invalidated_report_count"] = len(invalidated_report)
    candidate_pairs = {
        (item.get("project_id"), item.get("car_id"))
        for item in raw_report
        if item.get("project_id") and item.get("car_id")
    }
    existing_project_ids = {
        project_id
        for project_id in db.scalars(
            select(Project.id).where(
                Project.id.in_({project_id for project_id, _ in candidate_pairs})
            )
        )
    } if candidate_pairs else set()
    active_pairs = (
        {
            (project_id, car_id)
            for project_id, car_id in db.execute(
                select(ProjectCar.project_id, ProjectCar.car_id).where(
                    ProjectCar.tracking_enabled.is_(True),
                    ProjectCar.project_id.in_(
                        {project_id for project_id, _ in candidate_pairs}
                    ),
                    ProjectCar.car_id.in_({car_id for _, car_id in candidate_pairs}),
                )
            ).all()
        }
        if candidate_pairs
        else set()
    )
    raw_report = [
        item
        for item in raw_report
        if not item.get("project_id")
        or not item.get("car_id")
        # A completed run is still useful history after its project was
        # deleted. Keep the report's last known project name in that case.
        or item["project_id"] not in existing_project_ids
        or (item.get("project_id"), item.get("car_id")) in active_pairs
    ]
    report_project_ids = {
        item.get("project_id") for item in raw_report if item.get("project_id")
    }
    report_car_ids = {item.get("car_id") for item in raw_report if item.get("car_id")}
    report_images = {
        car_id: (image_payloads(car_id, db).get("MAIN") or {}).get("url")
        for car_id in report_car_ids
    }
    report_registrations = (
        {
            car.id: (car.details or {}).get("registration_number")
            for car in db.scalars(select(Car).where(Car.id.in_(report_car_ids)))
        }
        if report_car_ids
        else {}
    )
    favorite_pairs = (
        {
            (project_id, car_id)
            for project_id, car_id in db.execute(
                select(ProjectCar.project_id, ProjectCar.car_id).where(
                    ProjectCar.favorite.is_(True),
                    ProjectCar.tracking_enabled.is_(True),
                    ProjectCar.project_id.in_(report_project_ids),
                    ProjectCar.car_id.in_(report_car_ids),
                )
            ).all()
        }
        if report_project_ids and report_car_ids
        else set()
    )
    projects = {
        project.id: project.name
        for project in db.scalars(
            select(Project).where(
                Project.id.in_(
                    list(
                        dict.fromkeys(
                            [
                                *(payload.get("project_ids") or []),
                                *[
                                    item.get("project_id")
                                    for item in payload.get("report") or []
                                    if item.get("project_id")
                                ],
                            ]
                        )
                    )
                )
            )
        )
    }
    report = [
        _report_change_details(
            {
                **item,
                # History keeps the original name only when a project was
                # deleted. Existing projects must always show their current
                # name after a rename.
                "project_name": projects.get(item.get("project_id"))
                or item.get("project_name"),
                "registration_number": item.get("registration_number")
                or report_registrations.get(item.get("car_id")),
                "image": report_images.get(item.get("car_id")),
                "favorite": (item.get("project_id"), item.get("car_id"))
                in favorite_pairs,
            },
            db,
        )
        for item in raw_report
    ]
    def report_priority(item: dict) -> int:
        if item.get("change") == "NEW":
            return 0
        if item.get("favorite"):
            return 1
        return {
            "PRICE_DROP": 2,
            "PRICE_INCREASE": 2,
            "MATERIAL_UPDATE": 3,
        }.get(item.get("change"), 9)

    payload["report"] = dedupe_report(report, priority=report_priority)
    if payload.get("summary"):
        payload["summary"] = {
            **payload["summary"],
            "changed": len(payload["report"]),
            "new": sum(item.get("change") == "NEW" for item in payload["report"]),
            "price_changes": sum(
                item.get("change") in {"PRICE_DROP", "PRICE_INCREASE"}
                for item in payload["report"]
            ),
        }
    failures = list(payload.get("failures") or [])
    if not failures:
        project_runs = db.execute(
            select(ProjectScanRun, Project)
            .join(Project, Project.id == ProjectScanRun.project_id)
            .where(
                ProjectScanRun.scan_run_id == run.id,
                ProjectScanRun.status == "FAILED",
            )
        ).all()
        project_names = [row.Project.name for row in project_runs]
        for row in project_runs:
            if source_key is not None and row.Project.id not in payload["project_ids"]:
                continue
            unavailable = source_key is not None and len(project_names) > 1 and (
                project_names.count(row.Project.name) > 1 or f"{row.Project.name}:" not in (run.error or ""))
            technical = (
                "Legacy error details cannot be attributed to this project"
                if unavailable else _legacy_project_error(run.error, row.Project.name, project_names)
            )
            code = row.ProjectScanRun.error_code or "READ_ERROR"
            if "ERR_TIMED_OUT" in technical or "Timeout" in technical:
                code = "TIMEOUT"
            failures.append(
                {
                    "scope": "PROJECT",
                    "project_id": row.Project.id,
                    "project_name": row.Project.name,
                    "car_id": None,
                    "encar_id": None,
                    "code": code,
                    "technical": technical,
                    **({"details_unavailable": True} if unavailable else {}),
                }
            )
    payload["failures"] = failures
    result = {
        "id": run.id,
        "kind": run.kind,
        "status": run.status,
        "progress": run.progress,
        "payload": payload,
        "error": run.error,
        "attempt_count": run.attempt_count,
        "started_at": run.started_at,
        "heartbeat_at": run.heartbeat_at,
        "finished_at": run.finished_at,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }
    if source_key is not None:
        portions = list(db.scalars(select(ProjectScanRun).where(
            ProjectScanRun.scan_run_id == run.id, ProjectScanRun.project_id.in_(payload["project_ids"]))))
        project_statuses = {**payload["project_statuses"], **{str(portion.project_id): portion.status for portion in portions}}
        # Legacy/interrupted jobs can retain unfinished project rows. They
        # cannot make a terminal job active again, or hide whole-job cancellation.
        active_statuses = {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}
        if run.status not in active_statuses or run.status == "CANCEL_REQUESTED":
            project_statuses = {
                key: run.status if status in active_statuses else status
                for key, status in project_statuses.items()
            }
        payload["project_statuses"] = project_statuses
        statuses = set(project_statuses.values())
        scope_status = next((status for status in ("CANCEL_REQUESTED", "RUNNING", "QUEUED") if status in statuses), None)
        scope_status = scope_status or (next(iter(statuses)) if len(statuses) == 1 else "PARTIAL" if statuses else run.status)
        terminal = statuses - {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}
        scope_progress = round(100 * sum(status in terminal for status in project_statuses.values()) / len(project_statuses)) if project_statuses else run.progress
        payload["summary"] = {**(payload.get("summary") or {}), "failed": len(failures),
                              "cancelled": sum(status in {"CANCELLED", "INTERRUPTED_SLEEP"} for status in project_statuses.values())}
        result.update({"source_key": source_key, "overall_status": run.status, "overall_progress": run.progress,
                       "status": scope_status, "progress": scope_progress,
                       "progress_kind": "PROJECT_COMPLETION" if project_statuses else "JOB",
                       "error": "; ".join(item.get("technical") or item.get("code") or "READ_ERROR" for item in failures) or None})
    return result


@app.get("/api/scans/launch-options")
def scan_launch_options(current: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    """Read-only launch preview; POST still rechecks license, scope and duplicates."""
    from .desktop_license import evaluate_search_entitlement
    from .maintenance import maintenance_active
    from .sources.registry import list_sources, source_catalog

    decision = evaluate_search_entitlement(update_trusted_time=False)
    maintenance = maintenance_active()
    sources, adapters = {}, {}
    for descriptor in list_sources():
        reason = None
        if "SEARCH" not in descriptor.capabilities:
            reason = "SOURCE_SEARCH_UNSUPPORTED"
        else:
            try:
                adapters[descriptor.key] = get_source(descriptor.key)
            except (ValueError, SourceUnavailableError):
                reason = "SOURCE_UNAVAILABLE"
        if reason is None:
            if maintenance:
                reason = "DESKTOP_MAINTENANCE_ACTIVE"
            elif not decision.can_search:
                reason = decision.reason or "LICENSE_REQUIRED"
            elif decision.mode == "required" and descriptor.key not in decision.sources:
                reason = "LICENSE_SOURCE_NOT_ALLOWED"
        sources[descriptor.key] = {"source_key": descriptor.key, "family": descriptor.family,
                                   "can_search": reason is None, "reason": reason}
    records = list(db.scalars(select(Project).where(Project.owner_id == current.id).order_by(Project.id)))
    active_runs = list(db.scalars(select(ScanRun).where(
        ScanRun.owner_id == current.id, ScanRun.status.in_(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]))))
    busy = _busy_project_runs(db, active_runs, {project.id for project in records})
    projects = []
    for project in records:
        availability = sources.get(project.source_key, {"can_search": False, "reason": "SOURCE_NOT_CONNECTED"})
        reason = availability["reason"]
        if availability["can_search"]:
            try:
                adapters[project.source_key].validate_search_url(project.search_url)
            except InvalidSourceUrlError:
                reason = "INVALID_SOURCE_URL"
        run = busy.get(project.id)
        # A queued project in a running batch is not itself running yet.
        active_status = None
        if run:
            payload = run.payload or {}
            active_status = (payload.get("project_statuses") or {}).get(str(project.id))
            if active_status and active_status not in {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}:
                active_status = "ALREADY_ACTIVE"
            elif active_status is None:
                active_status = run.status
                if run.status == "RUNNING" and payload.get("current_project_id") not in (None, project.id):
                    active_status = "QUEUED"
        projects.append({"id": project.id, "name": project.name, "source_key": project.source_key,
                         "can_search": reason is None, "reason": reason,
                         "active_status": active_status, "scan_run_id": run.id if run else None})
    return {"sources": list(sources.values()), "projects": projects,
            "licensed_sources": list(decision.sources) if decision.mode == "required"
            else [source["key"] for source in source_catalog()]}


@app.post("/api/scans/projects", status_code=202)
def scan_projects(
    body: ScanProjectsIn,
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    requested_ids = list(dict.fromkeys(body.project_ids))
    requested_sources = list(dict.fromkeys(body.source_keys))
    if body.all_sources and (requested_ids or requested_sources):
        raise HTTPException(422, "ambiguous_scan_scope")

    if requested_ids:
        records = list(
            db.scalars(
                select(Project).where(
                    Project.id.in_(requested_ids),
                    Project.owner_id == current.id,
                )
            )
        )
        by_id = {project.id: project for project in records}
        if set(by_id) != set(requested_ids):
            raise HTTPException(404, "project_not_found")
        records = [by_id[project_id] for project_id in requested_ids]
        if requested_sources and any(
            project.source_key not in requested_sources for project in records
        ):
            raise HTTPException(409, "project_source_mismatch")
        scope_mode = "PROJECTS"
    elif body.all_sources:
        records = list(
            db.scalars(
                select(Project)
                .where(Project.owner_id == current.id)
                .order_by(Project.id)
            )
        )
        scope_mode = "ALL_SITES"
    elif requested_sources:
        records = list(
            db.scalars(
                select(Project)
                .where(
                    Project.owner_id == current.id,
                    Project.source_key.in_(requested_sources),
                )
                .order_by(Project.id)
            )
        )
        scope_mode = "SITES"
    else:
        raise HTTPException(422, "project_scan_scope_required")

    if not records:
        raise HTTPException(404, "projects_not_found")

    source_keys = list(dict.fromkeys(project.source_key for project in records))
    sources = {}
    for source_key in source_keys:
        try:
            sources[source_key] = get_source(source_key)
        except ValueError as exc:
            raise HTTPException(422, "unsupported_source") from exc
        except SourceUnavailableError as exc:
            raise HTTPException(503, "source_unavailable") from exc

    project_plans = []
    for project in records:
        try:
            search_url = sources[project.source_key].validate_search_url(
                project.search_url
            )
        except InvalidSourceUrlError as exc:
            raise HTTPException(422, "invalid_source_url") from exc
        project_plans.append(
            {
                "project_id": project.id,
                "source_key": project.source_key,
                "name": project.name,
                "search_url": search_url,
                "search_config": deepcopy(project.search_config),
                "scan_mode": project.scan_mode,
                "search_page_mode": project.search_page_mode,
                "price_filter_mode": project.price_filter_mode,
                "price_min_krw": project.price_min_krw,
                "price_max_krw": project.price_max_krw,
                "price_filter_revision": project.price_filter_revision,
                "price_filter_baseline_revision": (
                    project.price_filter_baseline_revision
                ),
            }
        )
    return enqueue(
        "PROJECTS",
        {
            "project_ids": [project.id for project in records],
            "source_keys": source_keys,
            "project_plans": project_plans,
            "launch_scope": {
                "mode": scope_mode,
                "project_ids": requested_ids,
                "source_keys": requested_sources,
            },
            "trigger": "MANUAL",
        },
        current,
        db,
    )


@app.post("/api/scans/cars", status_code=202)
def scan_cars(
    body: ScanCarsIn,
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    car_ids = list(dict.fromkeys(body.car_ids))
    if not car_ids:
        raise HTTPException(422, "car_ids_required")
    for car_id in car_ids:
        accessible_car_relation(
            car_id,
            current,
            db,
            body.project_id,
            require_tracking=False,
        )
    return enqueue(
        "CARS",
        {"car_ids": car_ids, "project_id": body.project_id},
        current,
        db,
    )


@app.get("/api/scans")
def scans(
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
    source_key: str | None = None,
) -> list[dict]:
    query = select(ScanRun).where(ScanRun.owner_id == current.id).order_by(ScanRun.created_at.desc())
    if source_key is None:
        query = query.limit(100)
    else:
        # ponytail: scan legacy JSON until 100 matches; index source membership
        # if large local histories make this slow. Stream to bound ORM memory.
        query = query.execution_options(yield_per=100)
    result = []
    for run in db.scalars(query):
        try:
            result.append(scan_out(run, db, source_key))
        except HTTPException as exc:
            if source_key is None or exc.status_code != 404:
                raise
            continue
        if len(result) == 100:
            break
    return result


@app.get("/api/scans/active")
def active_scans(current: User = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    """Owner's complete active queue, not the 100-entry history window."""
    runs = list(db.scalars(select(ScanRun).where(
        ScanRun.owner_id == current.id,
        ScanRun.status.in_(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]),
    ).order_by(ScanRun.created_at, ScanRun.id)))
    portions = {}
    for portion in db.scalars(select(ProjectScanRun).where(ProjectScanRun.scan_run_id.in_([run.id for run in runs]))):
        portions.setdefault(portion.scan_run_id, {})[str(portion.project_id)] = portion.status
    result = []
    for run in runs:
        item = scan_out(run, db)
        item["payload"]["project_statuses"] = {
            **item["payload"].get("project_statuses", {}), **portions.get(run.id, {}),
        }
        result.append(item)
    return result


@app.get("/api/events")
def live_events(current: User = Depends(require_user)):
    """Push a lightweight invalidation after a committed scan update."""

    subscriber = live_updates.subscribe(current.id)

    def stream():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    event = subscriber.get(timeout=20)
                    if event is None:
                        break
                    yield f"event: scan\ndata: {json.dumps(event)}\n\n"
                except Empty:
                    yield ": keep-alive\n\n"
        finally:
            live_updates.unsubscribe(subscriber)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/scans/{scan_id}")
@app.get("/api/scans/{scan_id}/progress")
def scan(
    scan_id: int,
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
    source_key: str | None = None,
) -> dict:
    run = db.scalar(
        select(ScanRun).where(
            ScanRun.id == scan_id,
            ScanRun.owner_id == current.id,
        )
    )
    if not run:
        raise HTTPException(404, "scan_not_found")
    return scan_out(run, db, source_key)


@app.get("/api/scans/{scan_id}/auction-report")
def auction_report(
    scan_id: int,
    source_key: str,
    car_ids: list[int] = Query(..., min_length=1, max_length=20),
    computer_timezone: str = Depends(auction_computer_timezone),
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    run = db.scalar(select(ScanRun).where(ScanRun.id == scan_id, ScanRun.owner_id == current.id))
    if not run:
        raise HTTPException(404, "scan_not_found")
    if not is_auction_source(source_key):
        raise HTTPException(400, "not_an_auction_source")
    report = {item["car_id"]: item for item in scan_out(run, db, source_key, report_car_ids=set(car_ids))["payload"].get("report", [])}
    requested = list(dict.fromkeys(car_ids))
    if any(car_id not in report for car_id in requested):
        raise HTTPException(404, "report_car_not_found")
    snapshot_ids = {item.get("after_snapshot_id") for item in report.values()
                    if item["car_id"] in requested and type(item.get("after_snapshot_id")) is int}
    snapshots = {snapshot.id: snapshot for snapshot in db.scalars(select(CarSnapshot).where(CarSnapshot.id.in_(snapshot_ids)))}
    observations = {observation.car_id: observation.round_id for observation in db.scalars(select(AuctionObservation).where(
        AuctionObservation.car_id.in_(requested),
        AuctionObservation.observation_key.in_([f"scan:{scan_id}:{car_id}" for car_id in requested])))
        if observation.observation_key == f"scan:{scan_id}:{observation.car_id}"}
    items = []
    for car_id in requested:
        item, view = report[car_id], None
        snapshot_id = item.get("after_snapshot_id")
        try:
            view = report_snapshot_view(snapshots.get(snapshot_id) if type(snapshot_id) is int else None, item,
                scan_id=scan_id, source_key=source_key, computer_timezone=computer_timezone,
                captured_round_id=observations.get(car_id))
        except (ValueError, TypeError, KeyError, OverflowError):
            pass  # A missing/corrupt historical observation must never become today's Car.
        items.append(dict(car_id=car_id, snapshot_id=snapshot_id if view else None,
                          recorded_at=view["generated_at"] if view else None,
                          view=view, issue=None if view else "SNAPSHOT_UNAVAILABLE"))
    return dict(scan_id=scan_id, source_key=source_key, computer_timezone=computer_timezone, items=items)


@app.post("/api/scans/{scan_id}/cancel", status_code=202)
def cancel_scan(
    scan_id: int,
    body: CancelScanIn | None = None,
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    with SCAN_COMMAND_LOCK:
        return _cancel_scan_unlocked(scan_id, body, current, db)


def _cancel_scan_unlocked(
    scan_id: int, body: CancelScanIn | None, current: User, db: Session,
) -> dict:
    run = db.scalar(
        select(ScanRun).where(
            ScanRun.id == scan_id,
            ScanRun.owner_id == current.id,
        )
    )
    if not run:
        raise HTTPException(404, "scan_not_found")
    if run.status in {"CANCELLED", "CANCEL_REQUESTED"}:
        return {"id": run.id, "status": run.status}
    if run.status not in {"QUEUED", "RUNNING"}:
        raise HTTPException(409, "scan_already_finished")

    cancellation_time = datetime.now(timezone.utc)
    timestamp = cancellation_time.isoformat()
    payload = dict(run.payload or {})
    requested_project_ids = list(dict.fromkeys(body.project_ids if body else []))
    requested_sources = list(dict.fromkeys(body.source_keys if body else []))
    scoped = bool(body and (requested_project_ids or requested_sources))
    if body and (
        (body.all_projects and scoped)
        or (requested_project_ids and requested_sources)
    ):
        raise HTTPException(422, "ambiguous_cancel_scope")
    if scoped:
        if run.kind != "PROJECTS":
            raise HTTPException(409, "project_cancel_not_supported")
        run_project_ids = list(dict.fromkeys(payload.get("project_ids") or []))
        if requested_project_ids and not set(requested_project_ids) <= set(
            run_project_ids
        ):
            raise HTTPException(404, "project_not_in_scan")
        if requested_sources:
            source_by_project = {
                plan.get("project_id"): plan.get("source_key") or "encar"
                for plan in payload.get("project_plans") or []
            }
            missing_source_ids = [
                project_id
                for project_id in run_project_ids
                if project_id not in source_by_project
            ]
            if missing_source_ids:
                source_by_project.update(
                    {
                        project.id: project.source_key
                        for project in db.scalars(
                            select(Project).where(
                                Project.id.in_(missing_source_ids),
                                Project.owner_id == current.id,
                            )
                        )
                    }
                )
            requested_project_ids.extend(
                project_id
                for project_id in run_project_ids
                if source_by_project.get(project_id) in requested_sources
                and project_id not in requested_project_ids
            )
            if not requested_project_ids:
                raise HTTPException(404, "source_not_in_scan")

        project_runs = {
            item.project_id: item
            for item in db.scalars(
                select(ProjectScanRun).where(ProjectScanRun.scan_run_id == run.id)
            )
        }
        terminal = {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "INTERRUPTED", "INTERRUPTED_SLEEP"}
        already_cancelled = set(payload.get("cancelled_project_ids") or [])
        cancellable = [
            project_id
            for project_id in requested_project_ids
            if project_id not in already_cancelled
            and (
                project_id not in project_runs
                or project_runs[project_id].status not in terminal
            )
        ]
        unfinished = {
            project_id
            for project_id in run_project_ids
            if project_id not in already_cancelled
            and (
                project_id not in project_runs
                or project_runs[project_id].status not in terminal
            )
        }
        if not cancellable:
            return {
                "id": run.id,
                "status": run.status,
                "projects": [
                    {
                        "project_id": project_id,
                        "status": (
                            project_runs[project_id].status
                            if project_id in project_runs
                            else "ALREADY_CANCELLED"
                        ),
                    }
                    for project_id in requested_project_ids
                ],
            }
        if set(cancellable) != unfinished:
            statuses = dict(payload.get("project_statuses") or {})
            cancellation_results = []
            for project_id in requested_project_ids:
                project_run = project_runs.get(project_id)
                if project_id not in cancellable:
                    status = (
                        project_run.status if project_run else "ALREADY_CANCELLED"
                    )
                elif project_run and project_run.status == "RUNNING":
                    project_run.status = "CANCEL_REQUESTED"
                    status = "CANCEL_REQUESTED"
                else:
                    if project_run is None and db.get(Project, project_id):
                        project_run = ProjectScanRun(
                            scan_run_id=run.id, project_id=project_id,
                        )
                        db.add(project_run)
                    if project_run is not None:
                        project_run.status = "CANCELLED"
                        project_run.error_code = None
                    status = "CANCELLED"
                statuses[str(project_id)] = status
                cancellation_results.append(
                    {"project_id": project_id, "status": status}
                )
            payload.update(
                {
                    "cancelled_project_ids": list(
                        dict.fromkeys([*payload.get("cancelled_project_ids", []), *cancellable])
                    ),
                    "project_statuses": statuses,
                    "partial_cancellation_requested_at": timestamp,
                }
            )
            run.payload = payload
            audit(
                db,
                "SCAN_PARTIAL_CANCEL_REQUESTED",
                actor_user_id=current.id,
                entity_type="SCAN",
                entity_id=run.id,
                payload={"project_ids": requested_project_ids},
            )
            db.commit()
            return {
                "id": run.id,
                "status": run.status,
                "projects": cancellation_results,
            }

    payload["cancellation_requested_at"] = timestamp
    if run.status == "QUEUED":
        run.status = "CANCELLED"
        run.finished_at = cancellation_time
        payload.update(
            {
                "cancelled_at": timestamp,
                "current_project_id": None,
                "report": list(payload.get("report") or []),
                "failures": list(payload.get("failures") or []),
                "summary": payload.get("summary")
                or {
                    "changed": 0,
                    "new": 0,
                    "price_changes": 0,
                    "failed": 0,
                },
            }
        )
    else:
        run.status = "CANCEL_REQUESTED"
    for project_run in db.scalars(
        select(ProjectScanRun).where(
            ProjectScanRun.scan_run_id == run.id,
            ProjectScanRun.status.in_(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]),
        )
    ):
        if run.status == "CANCELLED":
            project_run.status = "CANCELLED"
            project_run.error_code = None
    run.payload = payload
    audit(
        db,
        "SCAN_CANCEL_REQUESTED",
        actor_user_id=current.id,
        entity_type="SCAN",
        entity_id=run.id,
    )
    db.commit()
    return {"id": run.id, "status": run.status}


def scheduler_out(user_id: int, db: Session) -> dict:
    setting = db.scalar(
        select(SchedulerSetting).where(SchedulerSetting.user_id == user_id)
    )
    if not setting:
        return {
            "enabled": False,
            "paused": False,
            "interval_minutes": 180,
            "performance_mode": "FAST",
            "project_ids": [],
            "next_run_at": None,
            "last_completed_run_at": None,
        }
    project_ids = list(
        db.scalars(
            select(ScheduledProject.project_id).where(
                ScheduledProject.scheduler_id == setting.id
            )
        )
    )
    return {
        "enabled": setting.enabled and bool(project_ids),
        "paused": setting.paused,
        "interval_minutes": setting.interval_minutes,
        "performance_mode": getattr(setting, "performance_mode", "FAST"),
        "project_ids": project_ids,
        "next_run_at": setting.next_run_at if project_ids else None,
        "last_completed_run_at": (
            setting.last_completed_run_at if project_ids else None
        ),
    }


@app.get("/api/scheduler")
@app.get("/api/settings")
def scheduler(
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    return scheduler_out(current.id, db)


@app.put("/api/scheduler")
@app.put("/api/settings")
def set_scheduler(
    body: SchedulerIn,
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    project_ids = list(dict.fromkeys(body.project_ids))
    existing_ids = set(
        db.scalars(
            select(Project.id).where(
                Project.id.in_(project_ids),
                Project.owner_id == current.id,
            )
        )
    )
    if existing_ids != set(project_ids):
        raise HTTPException(404, "project_not_found")
    if body.enabled and not project_ids:
        raise HTTPException(422, "scheduled_projects_required")
    setting = db.scalar(
        select(SchedulerSetting).where(SchedulerSetting.user_id == current.id)
    ) or SchedulerSetting(user_id=current.id)
    db.add(setting)
    db.flush()
    previous_project_ids = set(
        db.scalars(
            select(ScheduledProject.project_id).where(
                ScheduledProject.scheduler_id == setting.id
            )
        )
    )
    reanchor = (
        not setting.enabled
        or setting.interval_minutes != body.interval_minutes
        or previous_project_ids != set(project_ids)
        or setting.next_run_at is None
    )
    setting.enabled = body.enabled
    setting.paused = body.paused if body.enabled else False
    setting.interval_minutes = body.interval_minutes
    setting.performance_mode = body.performance_mode
    if not body.enabled:
        setting.next_run_at = None
    elif reanchor:
        setting.next_run_at = datetime.now(timezone.utc) + timedelta(
            minutes=body.interval_minutes
        )
    db.query(ScheduledProject).filter_by(scheduler_id=setting.id).delete()
    for project_id in project_ids:
        db.add(
            ScheduledProject(
                scheduler_id=setting.id, project_id=project_id
            )
        )
    db.commit()
    return scheduler_out(current.id, db)


@app.post("/api/scheduler/pause")
def pause_scheduler(
    body: BoolPatch,
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    setting = db.scalar(
        select(SchedulerSetting).where(SchedulerSetting.user_id == current.id)
    )
    if not setting or not setting.enabled:
        raise HTTPException(409, "scheduler_not_enabled")
    setting.paused = body.value
    db.commit()
    return scheduler_out(current.id, db)


@app.get("/api/import/legacy")
@app.post("/api/import/legacy/validate")
def validate_import(
    path: str = Query("/legacy"),
    _: User = Depends(require_admin),
) -> dict:
    return inspect_source(Path(path))


@app.post("/api/import/legacy/run")
def commit_import(
    path: str = Query("/legacy"),
    admin: User = Depends(require_csrf),
) -> dict:
    if admin.role != "ADMIN":
        raise HTTPException(403, "admin_required")
    return run_import(Path(path), True, owner_id=admin.id)


def desktop_data_root() -> Path:
    configured = os.environ.get("SEARCHCAR_DESKTOP_DATA_DIR")
    if not configured:
        raise HTTPException(404, "desktop_runtime_required")
    return Path(configured).expanduser().resolve()


@app.get("/api/desktop/onboarding")
def desktop_onboarding_status(db: Session = Depends(get_db)) -> dict:
    """Expose desktop first-run state without creating a local user."""

    desktop_data_root()
    from .desktop_onboarding import has_local_users

    return {"required": not has_local_users(db)}


def _complete_desktop_onboarding(
    *,
    preferred_locale: str,
    request: Request,
    response: Response,
    db: Session,
) -> dict:
    """Create the local workspace only after remote licensing succeeds."""

    from .desktop_onboarding import create_desktop_workspace

    try:
        user = create_desktop_workspace(
            db,
            preferred_locale=preferred_locale,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    token, _, session = create_session(db, user, request)
    csrf_token, _ = sync_csrf_cookie(request, response, session)
    user.last_login_at = now_utc()
    user.last_activity_at = user.last_login_at
    db.commit()
    response.set_cookie(
        settings.auth_cookie_name,
        token,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        max_age=settings.auth_session_days * 24 * 60 * 60,
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"user": user_out(user, db), "csrf_token": csrf_token}


@app.post("/api/desktop/onboarding/activate")
def activate_desktop_workspace(
    body: DesktopOnboardingActivateIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    """Redeem an owner-issued code and create one non-interactive workspace."""

    verify_request_origin(request)
    desktop_data_root()
    from .desktop_onboarding import has_local_users

    if has_local_users(db):
        raise HTTPException(409, "desktop_workspace_already_initialized")

    # The Worker has semantic replay protection for the same device.  Thus a
    # local SQLite failure after redeeming can safely be retried with the same
    # code instead of consuming the customer license.
    _desktop_license_operation(
        "desktop_onboarding_redeem",
        lambda client: client.redeem(body.activation_code),
    )
    return _complete_desktop_onboarding(
        preferred_locale=body.preferred_locale,
        request=request,
        response=response,
        db=db,
    )


@app.post("/api/desktop/onboarding/transfer/request")
def request_desktop_onboarding_transfer(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Create a destination-device transfer before a local user exists."""

    verify_request_origin(request)
    desktop_data_root()
    from .desktop_onboarding import has_local_users

    if has_local_users(db):
        raise HTTPException(409, "desktop_workspace_already_initialized")
    return _desktop_license_operation(
        "desktop_onboarding_transfer_request",
        lambda client: client.request_transfer(),
    )


@app.post("/api/desktop/onboarding/transfer/claim")
def claim_desktop_onboarding_transfer(
    body: DesktopOnboardingTransferClaimIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    """Claim an approved transfer and create the destination workspace."""

    verify_request_origin(request)
    desktop_data_root()
    from .desktop_onboarding import has_local_users

    if has_local_users(db):
        raise HTTPException(409, "desktop_workspace_already_initialized")
    _desktop_license_operation(
        "desktop_onboarding_transfer_claim",
        lambda client: client.claim_transfer(body.transfer_code, body.claim_token),
    )
    return _complete_desktop_onboarding(
        preferred_locale=body.preferred_locale,
        request=request,
        response=response,
        db=db,
    )


@app.get("/api/desktop/license")
def desktop_license_status(_: User = Depends(require_user)) -> dict:
    desktop_data_root()
    from .desktop_license import enforcement_mode, evaluate_search_entitlement
    from .desktop_license_client import service_is_configured

    configured = service_is_configured()
    required = enforcement_mode() == "required"
    return {
        **evaluate_search_entitlement(
            mode="required" if configured else enforcement_mode()
        ).as_dict(),
        "service_configured": configured,
        "enforcement_required": required,
    }


def _desktop_license_admin(current: User) -> None:
    if current.role != "ADMIN":
        raise HTTPException(403, "admin_required")


def _desktop_license_operation(action: str, operation) -> dict:
    from .desktop_license_client import LicenseClientError, LicenseServiceClient

    try:
        client = LicenseServiceClient(desktop_data_root())
        return operation(client)
    except LicenseClientError as exc:
        status = exc.http_status
        if status in {401, 403}:
            status = 409
        logger.warning(
            "Desktop license operation failed: action=%s code=%s http_status=%s cause_type=%s",
            action,
            exc.code,
            status,
            type(exc.__cause__).__name__ if exc.__cause__ is not None else "none",
        )
        raise HTTPException(status, exc.code.lower()) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Desktop license operation failed unexpectedly: action=%s error_type=%s",
            action,
            type(exc).__name__,
        )
        raise HTTPException(500, "license_internal_error") from None


@app.post("/api/desktop/license/trial")
def activate_desktop_trial(
    body: DesktopLicenseTrialIn,
    current: User = Depends(require_csrf),
) -> dict:
    _desktop_license_admin(current)
    return _desktop_license_operation(
        "trial", lambda client: client.activate_trial(body.subject)
    )


@app.post("/api/desktop/license/refresh")
def refresh_desktop_license(current: User = Depends(require_csrf)) -> dict:
    return _desktop_license_operation("refresh", lambda client: client.refresh())


@app.post("/api/desktop/license/redeem")
def redeem_desktop_license(
    body: DesktopLicenseRedeemIn,
    current: User = Depends(require_csrf),
) -> dict:
    return _desktop_license_operation(
        "redeem",
        lambda client: client.redeem(body.activation_code)
    )


@app.post("/api/desktop/license/transfer/request")
def request_desktop_license_transfer(current: User = Depends(require_csrf)) -> dict:
    """Start a device transfer from the destination desktop only."""

    return _desktop_license_operation("transfer_request", lambda client: client.request_transfer())


@app.post("/api/desktop/license/transfer/claim")
def claim_desktop_license_transfer(
    body: DesktopLicenseTransferClaimIn,
    current: User = Depends(require_csrf),
) -> dict:
    return _desktop_license_operation(
        "transfer_claim",
        lambda client: client.claim_transfer(body.transfer_code, body.claim_token),
    )


@app.post("/api/desktop/open-external", status_code=202)
def open_desktop_external_url(
    body: ExternalUrlIn,
    _: User = Depends(require_csrf),
) -> dict:
    from .external_links import validated_external_url

    desktop_data_root()
    try:
        url = validated_external_url(body.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not webbrowser.open_new_tab(url):
        raise HTTPException(503, "system_browser_unavailable")
    return {"status": "opened"}


def desktop_backup_path(name: str) -> Path:
    if Path(name).name != name or not name.endswith(".searchcar-backup"):
        raise HTTPException(400, "invalid_backup_name")
    path = desktop_data_root() / "backups" / name
    if path.is_symlink():
        raise HTTPException(400, "backup_symlink_not_allowed")
    return path


def desktop_diagnostics_path(name: str) -> Path:
    from .desktop_diagnostics import support_report_path

    try:
        return support_report_path(desktop_data_root() / "diagnostics", name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "diagnostic_report_not_found") from exc


def _require_desktop_data_access(current: User, db: Session) -> None:
    """Allow data tools to an admin or the one passwordless desktop workspace."""

    desktop_data_root()
    if current.role == "ADMIN":
        return
    from .desktop_onboarding import desktop_workspace_user

    workspace = desktop_workspace_user(db)
    if workspace is None or workspace.id != current.id:
        raise HTTPException(403, "desktop_data_access_required")


@app.get("/api/desktop/data")
def desktop_data_status(
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    _require_desktop_data_access(current, db)
    root = desktop_data_root()
    backups = root / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    restore_result_path = root / "runtime" / "last-restore-result.json"
    restore_result = None
    if restore_result_path.is_file():
        try:
            import json

            restore_result = json.loads(restore_result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            restore_result = {"status": "unreadable"}
    return {
        "data_directory": str(root),
        "backups": [
            {
                "name": backup.name,
                "bytes": backup.stat().st_size,
                "updated_at": datetime.fromtimestamp(
                    backup.stat().st_mtime,
                    tz=timezone.utc,
                ),
            }
            for backup in sorted(
                (
                    item
                    for item in backups.glob("*.searchcar-backup")
                    if item.is_file() and not item.is_symlink()
                ),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        ],
        "restore_result": restore_result,
    }


@app.post("/api/desktop/diagnostics/reports")
def create_desktop_support_report(
    hours: int = Query(24),
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    _require_desktop_data_access(current, db)
    from .desktop_diagnostics import build_support_report

    root = desktop_data_root()
    try:
        report = build_support_report(
            root / "logs",
            root / "diagnostics",
            app_version=app.version,
            hours=hours,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    audit(
        db,
        action="DESKTOP_SUPPORT_REPORT_CREATED",
        actor_user_id=current.id,
        entity_type="DIAGNOSTIC_REPORT",
        entity_id=report.report_id,
    )
    db.commit()
    return report.as_dict()


@app.get("/api/desktop/diagnostics/reports/{name}/download")
def download_desktop_support_report(
    name: str,
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _require_desktop_data_access(current, db)
    path = desktop_diagnostics_path(name)
    return FileResponse(
        path,
        media_type="application/zip",
        filename=path.name,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post("/api/desktop/diagnostics/reports/{name}/reveal", status_code=202)
def reveal_desktop_support_report(
    name: str,
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    _require_desktop_data_access(current, db)
    from .desktop_diagnostics import reveal_support_report

    try:
        reveal_support_report(desktop_data_root() / "diagnostics", name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "diagnostic_report_not_found") from exc
    except OSError as exc:
        raise HTTPException(503, "diagnostic_folder_unavailable") from exc
    audit(
        db,
        action="DESKTOP_SUPPORT_REPORT_REVEALED",
        actor_user_id=current.id,
        entity_type="DIAGNOSTIC_REPORT",
        entity_id=name,
    )
    db.commit()
    return {"status": "opened"}


@app.post("/api/desktop/backups")
def create_desktop_backup(
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    _require_desktop_data_access(current, db)
    from .desktop_backup import BackupBusyError, export_backup

    root = desktop_data_root()
    backup_path = root / "backups" / (
        "manual-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-"
        f"{uuid4().hex[:8]}.searchcar-backup"
    )
    try:
        result = export_backup(
            root / "data" / "searchcar.sqlite3",
            root / "storage",
            backup_path,
            app_version=app.version,
        )
    except BackupBusyError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(
        db,
        action="DESKTOP_BACKUP_CREATED",
        actor_user_id=current.id,
        entity_type="BACKUP",
        entity_id=backup_path.name,
    )
    db.commit()
    return result.as_dict()


@app.get("/api/desktop/backups/{name}/download")
def download_desktop_backup(
    name: str,
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Stream only a complete, verified backup to the user's chosen location."""

    _require_desktop_data_access(current, db)
    from .desktop_backup import BackupValidationError, validate_backup

    path = desktop_backup_path(name)
    try:
        validate_backup(path)
    except BackupValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=name,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post("/api/desktop/backups/import")
async def import_desktop_backup(
    request: Request,
    name: str = Query(..., min_length=1, max_length=200),
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    """Receive a portable backup, validate it, then atomically keep it locally."""

    _require_desktop_data_access(current, db)
    from .desktop_backup import BackupValidationError, validate_backup

    if Path(name).name != name or not name.endswith(".searchcar-backup"):
        raise HTTPException(400, "invalid_backup_name")
    raw_length = request.headers.get("content-length")
    if raw_length:
        try:
            content_length = int(raw_length)
        except ValueError as exc:
            raise HTTPException(400, "invalid_backup_size") from exc
        if content_length <= 0:
            raise HTTPException(400, "empty_backup_upload")
        if content_length > MAX_DESKTOP_BACKUP_UPLOAD_BYTES:
            raise HTTPException(413, "backup_upload_too_large")

    backups = desktop_data_root() / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    temporary = backups / f".backup-import-{uuid4().hex}.tmp"
    destination = backups / (
        "imported-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-"
        f"{uuid4().hex[:8]}.searchcar-backup"
    )
    received = 0
    try:
        with temporary.open("xb") as output:
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            async for chunk in request.stream():
                received += len(chunk)
                if received > MAX_DESKTOP_BACKUP_UPLOAD_BYTES:
                    raise HTTPException(413, "backup_upload_too_large")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if received == 0:
            raise HTTPException(400, "empty_backup_upload")
        validation = validate_backup(temporary)
        os.replace(temporary, destination)
    except BackupValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        if temporary.exists():
            temporary.unlink()

    audit(
        db,
        action="DESKTOP_BACKUP_IMPORTED",
        actor_user_id=current.id,
        entity_type="BACKUP",
        entity_id=destination.name,
        payload={"source_name": name, "bytes": received},
    )
    db.commit()
    return {
        "name": destination.name,
        "bytes": destination.stat().st_size,
        "updated_at": datetime.fromtimestamp(
            destination.stat().st_mtime,
            tz=timezone.utc,
        ),
        "validation": {
            **validation.as_dict(),
            "path": str(destination),
        },
    }


@app.post("/api/desktop/backups/{name}/validate")
def validate_desktop_backup(
    name: str,
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    _require_desktop_data_access(current, db)
    from .desktop_backup import BackupValidationError, validate_backup

    path = desktop_backup_path(name)
    try:
        return validate_backup(path).as_dict()
    except BackupValidationError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/desktop/backups/{name}/restore")
def stage_desktop_restore(
    name: str,
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    _require_desktop_data_access(current, db)
    from .desktop_backup import BackupValidationError, validate_backup

    source = desktop_backup_path(name)
    try:
        validation = validate_backup(source)
    except BackupValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    runtime = desktop_data_root() / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    pending = runtime / "pending-restore.searchcar-backup"
    temporary = runtime / f".{pending.name}.{uuid4().hex}.tmp"
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, pending)
    finally:
        if temporary.exists():
            temporary.unlink()
    (runtime / "last-restore-result.json").write_text(
        '{"status":"pending"}\n',
        encoding="utf-8",
    )
    audit(
        db,
        action="DESKTOP_RESTORE_STAGED",
        actor_user_id=current.id,
        entity_type="BACKUP",
        entity_id=name,
    )
    db.commit()
    return {
        "status": "restart_required",
        "backup": validation.as_dict(),
    }


@app.post("/api/desktop/migrations")
def migrate_desktop_database(
    body: DesktopMigrationIn,
    admin: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    if admin.role != "ADMIN":
        raise HTTPException(403, "admin_required")
    from .desktop_backup import BackupError
    from .postgres_converter import ConversionError, convert_to_backup

    root = desktop_data_root()
    destination = root / "backups" / (
        "migration-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-"
        f"{uuid4().hex[:8]}.searchcar-backup"
    )
    try:
        report = convert_to_backup(
            body.source_database_url,
            Path(body.source_storage_path),
            destination,
            app_version=app.version,
        )
    except (BackupError, ConversionError, OSError, SQLAlchemyError, ValueError) as exc:
        raise HTTPException(400, f"desktop_migration_failed:{type(exc).__name__}") from exc
    audit(
        db,
        action="DESKTOP_MIGRATION_CREATED",
        actor_user_id=admin.id,
        entity_type="BACKUP",
        entity_id=destination.name,
        payload={
            "source_dialect": report.source_dialect,
            "source_counts": report.source_counts,
            "storage_files": report.storage_files,
        },
    )
    db.commit()
    return report.as_dict()
