import enum
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base
from .db_types import UTCDateTime

class ScanMode(str, enum.Enum): FAST="FAST"; ACCURATE="ACCURATE"
class User(Base):
    __tablename__="users"
    id: Mapped[int]=mapped_column(primary_key=True)
    username: Mapped[str]=mapped_column(String(64))
    username_key: Mapped[str]=mapped_column(String(64), unique=True)
    password_hash: Mapped[str]=mapped_column(Text)
    role: Mapped[str]=mapped_column(String(16), default="USER")
    status: Mapped[str]=mapped_column(String(24), default="ACTIVE")
    project_limit: Mapped[int|None]=mapped_column(Integer, default=1)
    must_change_password: Mapped[bool]=mapped_column(Boolean, default=True)
    preferred_locale: Mapped[str]=mapped_column(String(2), default="ru")
    last_login_at: Mapped[datetime|None]=mapped_column(UTCDateTime())
    last_activity_at: Mapped[datetime|None]=mapped_column(UTCDateTime())
    created_at: Mapped[datetime]=mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[datetime]=mapped_column(UTCDateTime(), server_default=func.now(), onupdate=func.now())
class AuthSession(Base):
    __tablename__="auth_sessions"
    id: Mapped[int]=mapped_column(primary_key=True)
    user_id: Mapped[int]=mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str]=mapped_column(String(64), unique=True, index=True)
    csrf_hash: Mapped[str]=mapped_column(String(64))
    expires_at: Mapped[datetime]=mapped_column(UTCDateTime(), index=True)
    last_seen_at: Mapped[datetime]=mapped_column(UTCDateTime(), server_default=func.now())
    revoked_at: Mapped[datetime|None]=mapped_column(UTCDateTime())
    ip_hash: Mapped[str|None]=mapped_column(String(64))
    user_agent_hash: Mapped[str|None]=mapped_column(String(64))
    created_at: Mapped[datetime]=mapped_column(UTCDateTime(), server_default=func.now())
class AuthAttempt(Base):
    __tablename__="auth_attempts"
    id: Mapped[int]=mapped_column(primary_key=True)
    username_key: Mapped[str]=mapped_column(String(64), index=True)
    ip_hash: Mapped[str|None]=mapped_column(String(64), index=True)
    success: Mapped[bool]=mapped_column(Boolean, default=False)
    created_at: Mapped[datetime]=mapped_column(UTCDateTime(), server_default=func.now(), index=True)
class AuditLog(Base):
    __tablename__="audit_logs"
    id: Mapped[int]=mapped_column(primary_key=True)
    actor_user_id: Mapped[int|None]=mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    target_user_id: Mapped[int|None]=mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str]=mapped_column(String(64), index=True)
    entity_type: Mapped[str|None]=mapped_column(String(40))
    entity_id: Mapped[str|None]=mapped_column(String(64))
    outcome: Mapped[str]=mapped_column(String(16), default="SUCCESS")
    payload: Mapped[dict|None]=mapped_column(JSON)
    created_at: Mapped[datetime]=mapped_column(UTCDateTime(), server_default=func.now(), index=True)
class Project(Base):
    __tablename__="projects"
    __table_args__=(
        UniqueConstraint("owner_id","name_key",name="uq_projects_owner_name_key"),
        UniqueConstraint("owner_id","search_url",name="uq_projects_owner_search_url"),
    )
    id: Mapped[int]=mapped_column(primary_key=True)
    owner_id: Mapped[int]=mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str]=mapped_column(String(140)); name_key: Mapped[str]=mapped_column(String(140))
    search_url: Mapped[str]=mapped_column(Text); telegram_url: Mapped[str|None]=mapped_column(Text)
    scan_mode: Mapped[str]=mapped_column(String(16), default="FAST"); search_page_mode: Mapped[str]=mapped_column(String(16), default="FIRST_PAGE")
    auto_update: Mapped[bool]=mapped_column(Boolean, default=True); created_at: Mapped[datetime]=mapped_column(UTCDateTime(), server_default=func.now()); updated_at: Mapped[datetime]=mapped_column(UTCDateTime(), server_default=func.now(), onupdate=func.now())
class Car(Base):
    __tablename__="cars"
    id: Mapped[int]=mapped_column(primary_key=True); canonical_encar_id: Mapped[str]=mapped_column(String(40), unique=True)
    url: Mapped[str]=mapped_column(Text); title: Mapped[str|None]=mapped_column(Text); details: Mapped[dict|None]=mapped_column(JSON)
    current_price: Mapped[int|None]=mapped_column(BigInteger); status: Mapped[str]=mapped_column(String(40), default="NEW")
    fingerprint: Mapped[str|None]=mapped_column(String(64)); comment: Mapped[str|None]=mapped_column(Text); rating: Mapped[int|None]=mapped_column(Integer)
    integrity_status: Mapped[str]=mapped_column(String(30), default="VALID"); integrity_reason: Mapped[str|None]=mapped_column(Text)
    excluded: Mapped[bool]=mapped_column(Boolean, default=False); created_at: Mapped[datetime]=mapped_column(UTCDateTime(), server_default=func.now()); updated_at: Mapped[datetime]=mapped_column(UTCDateTime(), server_default=func.now(), onupdate=func.now())
class ProjectCar(Base):
    __tablename__="project_cars"
    project_id: Mapped[int]=mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True); car_id: Mapped[int]=mapped_column(ForeignKey("cars.id"), primary_key=True)
    first_seen_at: Mapped[datetime|None]=mapped_column(UTCDateTime()); last_seen_at: Mapped[datetime|None]=mapped_column(UTCDateTime()); last_found_at: Mapped[datetime|None]=mapped_column(UTCDateTime())
    consecutive_missing_scans: Mapped[int]=mapped_column(default=0); search_status: Mapped[str]=mapped_column(String(30), default="FOUND"); favorite: Mapped[bool]=mapped_column(default=False); viewed: Mapped[bool]=mapped_column(default=False); viewed_at: Mapped[datetime|None]=mapped_column(UTCDateTime()); tracking_enabled: Mapped[bool]=mapped_column(Boolean, default=True)
    last_observed_snapshot_id: Mapped[int|None]=mapped_column(ForeignKey("car_snapshots.id", ondelete="SET NULL"))
    last_observed_price: Mapped[int|None]=mapped_column(BigInteger)
    last_observed_fingerprint: Mapped[str|None]=mapped_column(String(64))
    last_observed_status: Mapped[str|None]=mapped_column(String(40))
    last_observed_at: Mapped[datetime|None]=mapped_column(UTCDateTime())
class UserCarState(Base):
    __tablename__="user_car_states"
    user_id: Mapped[int]=mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    car_id: Mapped[int]=mapped_column(ForeignKey("cars.id", ondelete="CASCADE"), primary_key=True)
    comment: Mapped[str|None]=mapped_column(Text)
    rating: Mapped[int|None]=mapped_column(Integer)
    excluded: Mapped[bool]=mapped_column(Boolean, default=False)
    comment_updated_at: Mapped[datetime|None]=mapped_column(UTCDateTime())
    created_at: Mapped[datetime]=mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[datetime]=mapped_column(UTCDateTime(), server_default=func.now(), onupdate=func.now())
class CarAlias(Base):
    __tablename__="car_aliases"; id: Mapped[int]=mapped_column(primary_key=True); car_id: Mapped[int]=mapped_column(ForeignKey("cars.id", ondelete="CASCADE")); alias_id: Mapped[str]=mapped_column(String(40), unique=True); created_at: Mapped[datetime]=mapped_column(UTCDateTime(), server_default=func.now())
class CarRecord(Base):
    __abstract__=True; id: Mapped[int]=mapped_column(primary_key=True); car_id: Mapped[int]=mapped_column(ForeignKey("cars.id", ondelete="CASCADE")); kind: Mapped[str|None]=mapped_column(String(40)); payload: Mapped[dict|None]=mapped_column(JSON); integrity_status: Mapped[str]=mapped_column(String(30), default="VALID"); integrity_reason: Mapped[str|None]=mapped_column(Text); created_at: Mapped[datetime]=mapped_column(UTCDateTime(), server_default=func.now())
class CarSnapshot(CarRecord): __tablename__="car_snapshots"
class PriceHistory(CarRecord): __tablename__="price_history"
class CarEvent(CarRecord):
    __tablename__="car_events"
    user_id: Mapped[int|None]=mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    project_id: Mapped[int|None]=mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), index=True)
class CarImage(CarRecord): __tablename__="car_images"
class ScanRun(Base):
    __tablename__="scan_runs"
    id: Mapped[int]=mapped_column(primary_key=True)
    owner_id: Mapped[int]=mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str]=mapped_column(String(20))
    status: Mapped[str]=mapped_column(String(20), default="QUEUED")
    progress: Mapped[int]=mapped_column(default=0)
    payload: Mapped[dict|None]=mapped_column(JSON)
    error: Mapped[str|None]=mapped_column(Text)
    worker_id: Mapped[str|None]=mapped_column(String(64), index=True)
    attempt_count: Mapped[int]=mapped_column(Integer, default=0)
    started_at: Mapped[datetime|None]=mapped_column(UTCDateTime())
    heartbeat_at: Mapped[datetime|None]=mapped_column(UTCDateTime())
    finished_at: Mapped[datetime|None]=mapped_column(UTCDateTime())
    created_at: Mapped[datetime]=mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[datetime]=mapped_column(UTCDateTime(), server_default=func.now(), onupdate=func.now())
class ProjectScanRun(Base):
    __tablename__="project_scan_runs"; id: Mapped[int]=mapped_column(primary_key=True); scan_run_id: Mapped[int]=mapped_column(ForeignKey("scan_runs.id", ondelete="CASCADE")); project_id: Mapped[int]=mapped_column(ForeignKey("projects.id", ondelete="CASCADE")); status: Mapped[str|None]=mapped_column(String(20)); error_code: Mapped[str|None]=mapped_column(String(30))
class SchedulerSetting(Base):
    __tablename__="scheduler_settings"; id: Mapped[int]=mapped_column(primary_key=True); user_id: Mapped[int]=mapped_column(ForeignKey("users.id"), unique=True, index=True); enabled: Mapped[bool]=mapped_column(default=False); interval_minutes: Mapped[int]=mapped_column(default=180); next_run_at: Mapped[datetime|None]=mapped_column(UTCDateTime()); created_at: Mapped[datetime]=mapped_column(UTCDateTime(), server_default=func.now()); updated_at: Mapped[datetime]=mapped_column(UTCDateTime(), server_default=func.now(), onupdate=func.now())
class ScheduledProject(Base):
    __tablename__="scheduled_projects"; scheduler_id: Mapped[int]=mapped_column(ForeignKey("scheduler_settings.id", ondelete="CASCADE"), primary_key=True); project_id: Mapped[int]=mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
