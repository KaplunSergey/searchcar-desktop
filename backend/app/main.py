from datetime import datetime, timedelta, timezone
import json
import logging
import os
from queue import Empty
import shutil
import webbrowser
from pathlib import Path
from uuid import uuid4

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
from .importer import inspect_source, run as run_import
from .job_queue import detach_project_from_active_jobs
from .models import (
    Car,
    CarAlias,
    CarEvent,
    CarImage,
    CarSnapshot,
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
from .storage_paths import resolve_storage_path
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
    ScanProjectsIn,
    SchedulerIn,
    DesktopMigrationIn,
    DesktopLicenseRedeemIn,
    DesktopOnboardingActivateIn,
    DesktopOnboardingTransferClaimIn,
    DesktopLicenseTransferClaimIn,
    DesktopLicenseTrialIn,
    ExternalUrlIn,
    RegistrationIn,
)
from .services import merge_reliable_detail
from .reporting import dedupe_report
from .live_updates import live_updates

logger = logging.getLogger(__name__)

app = FastAPI(title="SearchCar API", version=settings.app_version)
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


@app.middleware("http")
async def attach_desktop_log_context(request: Request, call_next):
    """Keep a short request id across local API log records."""

    from .desktop_runtime import desktop_correlation_id

    token = desktop_correlation_id.set(f"request-{uuid4().hex[:12]}")
    response = None
    try:
        response = await call_next(request)
        return response
    except Exception:
        logger.exception("Local API request failed: method=%s path=%s", request.method, request.url.path)
        raise
    finally:
        logger.info(
            "Local API request completed: method=%s path=%s status=%s",
            request.method,
            request.url.path,
            getattr(response, "status_code", 500),
        )
        desktop_correlation_id.reset(token)


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
    cars = (
        db.scalar(
            select(func.count())
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
        or 0
    )
    last_run = db.execute(
        select(ProjectScanRun, ScanRun)
        .join(ScanRun, ScanRun.id == ProjectScanRun.scan_run_id)
        .where(ProjectScanRun.project_id == project.id)
        .order_by(ScanRun.created_at.desc())
        .limit(1)
    ).first()
    return {
        "id": project.id,
        "name": project.name,
        "search_url": project.search_url,
        "telegram_url": project.telegram_url,
        "scan_mode": project.scan_mode,
        "search_page_mode": project.search_page_mode,
        "auto_update": project.auto_update,
        "cars": cars,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
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
    return record.status


def image_payloads(car_id: int, db: Session) -> dict:
    result = {}
    for image in db.scalars(
        select(CarImage).where(
            CarImage.car_id == car_id,
            CarImage.integrity_status == "VALID",
        )
    ):
        payload = dict(image.payload or {})
        path = payload.get("path")
        if path:
            resolved_path = resolve_storage_path(path, storage_path)
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
    values: list[int] = []
    if record.current_price:
        values.append(record.current_price)
    for point in db.scalars(
        select(PriceHistory)
        .where(
            PriceHistory.car_id == record.id,
            PriceHistory.integrity_status == "VALID",
        )
        .order_by(PriceHistory.created_at.desc())
        .limit(30)
    ):
        value = int((point.payload or {}).get("price_krw") or 0)
        if value and value not in values:
            values.append(value)
        if len(values) == 2:
            break
    if len(values) < 2 or values[0] == values[1]:
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
    requested = (storage_path / file_path).resolve()
    root = storage_path.resolve()
    try:
        relative = requested.relative_to(root)
    except ValueError:
        raise HTTPException(404, "file_not_found")
    if not requested.is_file() or len(relative.parts) < 3 or relative.parts[0] != "cars":
        raise HTTPException(404, "file_not_found")
    encar_id = relative.parts[1]
    car_id = db.scalar(
        select(Car.id).where(Car.canonical_encar_id == encar_id)
    )
    if not car_id:
        raise HTTPException(404, "file_not_found")
    accessible_car_relation(
        car_id,
        current,
        db,
        require_tracking=False,
    )
    return FileResponse(requested)


@app.get("/api/projects")
def projects(
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    return [
        project_out(project, db)
        for project in db.scalars(
            select(Project)
            .where(Project.owner_id == current.id)
            .order_by(Project.updated_at.desc())
        )
    ]


@app.post("/api/projects", status_code=201)
def create_project(
    body: ProjectIn,
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
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
        name=body.name.strip(),
        name_key=body.name.strip().casefold(),
        search_url=body.search_url,
        telegram_url=body.telegram_url,
        scan_mode=body.scan_mode,
        search_page_mode=body.search_page_mode,
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


@app.get("/api/projects/{project_id}")
def get_project(
    project_id: int,
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    project = owned_project(project_id, current, db)
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
    for key, value in changes.items():
        setattr(
            project,
            key,
            value.strip() if key in {"name", "telegram_url"} and value else value,
        )
    if body.name:
        project.name_key = body.name.strip().casefold()
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
        else:
            query = query.where(Car.status == status)
    result = []
    for relation, car in db.execute(query.order_by(Car.updated_at.desc())):
        details = enriched_details(car)
        result.append(
            {
                "id": car.id,
                "encar_id": car.canonical_encar_id,
                "url": car.url,
                "title": car.title,
                "price": car.current_price,
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
    return result


@app.get("/api/favorites")
def favorite_cars(
    current: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    result = []
    rows = db.execute(
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
        .order_by(Car.updated_at.desc())
    ).all()
    for relation, car, project in rows:
        result.append(
            {
                "id": car.id,
                "project_id": project.id,
                "project_name": project.name,
                "encar_id": car.canonical_encar_id,
                "url": car.url,
                "title": car.title,
                "price": car.current_price,
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
    record = db.scalar(select(Car).where(Car.canonical_encar_id == encar_id))
    if not record:
        record = db.scalar(
            select(Car)
            .join(CarAlias, CarAlias.car_id == Car.id)
            .where(CarAlias.alias_id == encar_id)
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
        "encar_id": record.canonical_encar_id,
        "url": record.url,
        "title": record.title,
        "price": record.current_price,
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
    return [
        {
            "price": point.payload.get("price_krw"),
            "at": point.payload.get("checked_at") or point.created_at,
        }
        for point in db.scalars(
            select(PriceHistory)
            .where(
                PriceHistory.car_id == car_id,
                PriceHistory.integrity_status == "VALID",
            )
            .order_by(PriceHistory.created_at)
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
    from .maintenance import maintenance_active
    from .desktop_license import SearchEntitlementError, require_search_entitlement

    if maintenance_active():
        raise HTTPException(409, "desktop_maintenance_active")
    try:
        require_search_entitlement("encar")
    except SearchEntitlementError as exc:
        raise HTTPException(402, exc.code.lower()) from exc
    requested_projects = set(payload.get("project_ids") or [])
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
                active.payload = {**active_payload, "project_ids": remaining}
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

    for active in active_runs:
        active_payload = active.payload or {}
        if requested_projects & set(active_payload.get("project_ids") or []):
            raise HTTPException(409, "project_scan_already_active")
        if requested_cars & set(active_payload.get("car_ids") or []):
            raise HTTPException(409, "car_scan_already_active")
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
    return {"id": run.id, "status": run.status}


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


def scan_out(run: ScanRun, db: Session) -> dict:
    payload = dict(run.payload or {})
    raw_report = list(payload.get("report") or [])
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
            technical = _legacy_project_error(
                run.error,
                row.Project.name,
                project_names,
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
                }
            )
    payload["failures"] = failures
    return {
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


@app.post("/api/scans/projects", status_code=202)
def scan_projects(
    body: ScanProjectsIn,
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    ids = list(dict.fromkeys(body.project_ids))
    if not ids:
        raise HTTPException(422, "project_ids_required")
    existing = set(
        db.scalars(
            select(Project.id).where(
                Project.id.in_(ids),
                Project.owner_id == current.id,
            )
        )
    )
    if existing != set(ids):
        raise HTTPException(404, "project_not_found")
    return enqueue(
        "PROJECTS",
        {"project_ids": ids, "trigger": "MANUAL"},
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
) -> list[dict]:
    return [
        scan_out(run, db)
        for run in db.scalars(
            select(ScanRun)
            .where(ScanRun.owner_id == current.id)
            .order_by(ScanRun.created_at.desc())
            .limit(100)
        )
    ]


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
) -> dict:
    run = db.scalar(
        select(ScanRun).where(
            ScanRun.id == scan_id,
            ScanRun.owner_id == current.id,
        )
    )
    if not run:
        raise HTTPException(404, "scan_not_found")
    return scan_out(run, db)


@app.post("/api/scans/{scan_id}/cancel", status_code=202)
def cancel_scan(
    scan_id: int,
    current: User = Depends(require_csrf),
    db: Session = Depends(get_db),
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
