import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json

from fastapi import HTTPException
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.database import Base, create_database_engine
from app.desktop_license import (
    LEASE_MESSAGE_PREFIX,
    canonical_json,
    evaluate_search_entitlement,
)
from app.models import ScanRun, SchedulerSetting, User


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def license_state(tmp_path, *, lease_hours=48, subscription_days=30):
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    server_time = datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc)
    license_id = "license-018f6ac2-8c44-7df0-8f6d-2d34af37b337"
    device_id = "device-018f6ac2-8c44-7df0-8f6d-2d34af37b337"
    payload = {
        "type": "searchcar-license-lease",
        "protocol_version": 1,
        "key_id": "test-key-v1",
        "server_time": timestamp(server_time),
        "issued_at": timestamp(server_time),
        "license_id": license_id,
        "device_id": device_id,
        "license_type": "SUBSCRIPTION",
        "subscription_expires_at": timestamp(
            server_time + timedelta(days=subscription_days)
        ),
        "lease_expires_at": timestamp(server_time + timedelta(hours=lease_hours)),
        "entitlements": {
            "search": True,
            "data_access": True,
            "backup_restore": True,
        },
        "app_version": "0.1.0",
    }
    signature = private_key.sign(
        (LEASE_MESSAGE_PREFIX + canonical_json(payload)).encode("utf-8")
    )
    directory = tmp_path / "license"
    directory.mkdir()
    (directory / "binding.json").write_text(
        json.dumps({"license_id": license_id, "device_id": device_id}),
        encoding="utf-8",
    )
    (directory / "lease.json").write_text(
        json.dumps({"payload": payload, "signature": b64url(signature)}),
        encoding="utf-8",
    )
    return {
        "now": server_time,
        "payload": payload,
        "public_keys": {"test-key-v1": b64url(public_key)},
        "directory": directory,
    }


def decision(state, now):
    return evaluate_search_entitlement(
        now=now,
        data_dir=state["directory"].parent,
        public_keys=state["public_keys"],
        mode="required",
    )


def test_valid_signed_lease_allows_offline_search(tmp_path) -> None:
    state = license_state(tmp_path)

    result = decision(state, state["now"] + timedelta(hours=12))

    assert result.can_search is True
    assert result.status == "active"
    assert result.license_type == "SUBSCRIPTION"
    assert (state["directory"] / "trusted-time.json").is_file()


def test_expired_cached_lease_enters_read_only_safe_mode(tmp_path) -> None:
    state = license_state(tmp_path, lease_hours=2)

    result = decision(state, state["now"] + timedelta(hours=3))

    assert result.can_search is False
    assert result.reason == "LICENSE_LEASE_EXPIRED"


def test_subscription_expiry_is_hard_boundary_inside_lease(tmp_path) -> None:
    state = license_state(tmp_path, lease_hours=48, subscription_days=1)

    result = decision(state, state["now"] + timedelta(hours=25))

    assert result.can_search is False
    assert result.reason == "LICENSE_SUBSCRIPTION_EXPIRED"


def test_tampered_lease_is_rejected(tmp_path) -> None:
    state = license_state(tmp_path)
    lease_path = state["directory"] / "lease.json"
    document = json.loads(lease_path.read_text(encoding="utf-8"))
    document["payload"]["entitlements"]["search"] = False
    lease_path.write_text(json.dumps(document), encoding="utf-8")

    result = decision(state, state["now"] + timedelta(hours=1))

    assert result.can_search is False
    assert result.reason == "LICENSE_SIGNATURE_INVALID"


def test_device_binding_mismatch_is_rejected(tmp_path) -> None:
    state = license_state(tmp_path)
    (state["directory"] / "binding.json").write_text(
        json.dumps(
            {
                "license_id": state["payload"]["license_id"],
                "device_id": "another-device",
            }
        ),
        encoding="utf-8",
    )

    result = decision(state, state["now"] + timedelta(hours=1))

    assert result.can_search is False
    assert result.reason == "LICENSE_DEVICE_MISMATCH"


def test_clock_rollback_does_not_extend_offline_access(tmp_path) -> None:
    state = license_state(tmp_path)
    assert decision(state, state["now"] + timedelta(hours=4)).can_search is True

    rolled_back = decision(state, state["now"] - timedelta(hours=2))

    assert rolled_back.can_search is False
    assert rolled_back.reason == "LICENSE_CLOCK_ROLLBACK"


def test_concurrent_checks_preserve_highest_observed_time(tmp_path) -> None:
    state = license_state(tmp_path)
    observed = [state["now"] + timedelta(minutes=value) for value in (1, 4, 2, 3)]

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda value: decision(state, value), observed))

    trusted = json.loads(
        (state["directory"] / "trusted-time.json").read_text(encoding="utf-8")
    )
    assert all(result.can_search for result in results)
    assert trusted["highest_observed_at"] == timestamp(max(observed))


def test_pilot_mode_does_not_require_license_state(tmp_path) -> None:
    result = evaluate_search_entitlement(
        data_dir=tmp_path,
        public_keys={},
        mode="disabled",
    )

    assert result.can_search is True
    assert result.status == "pilot"


def database_factory(tmp_path):
    engine = create_database_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'license-guard.sqlite3').as_posix()}"
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def add_user(db: Session) -> User:
    user = User(
        username="owner",
        username_key="owner",
        password_hash="test-only",
        role="USER",
        status="ACTIVE",
    )
    db.add(user)
    db.flush()
    return user


def require_missing_license(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SEARCHCAR_LICENSE_ENFORCEMENT", "required")
    monkeypatch.setenv("SEARCHCAR_DESKTOP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SEARCHCAR_LICENSE_PUBLIC_KEYS_JSON", raising=False)
    monkeypatch.delenv("SEARCHCAR_LICENSE_PUBLIC_KEY_FILE", raising=False)
    monkeypatch.setattr("app.database.settings.storage_root", str(tmp_path / "storage"))


def test_manual_enqueue_is_blocked_without_entitlement(tmp_path, monkeypatch) -> None:
    require_missing_license(monkeypatch, tmp_path)
    from app.main import enqueue

    engine, _ = database_factory(tmp_path)
    with Session(engine) as db:
        user = add_user(db)

        with pytest.raises(HTTPException) as caught:
            enqueue("PROJECTS", {"project_ids": [1]}, user, db)

        assert caught.value.status_code == 402
        assert caught.value.detail == "license_not_configured"
        assert db.scalar(select(ScanRun.id)) is None


def test_scheduler_reanchors_without_queue_when_license_is_blocked(
    tmp_path,
    monkeypatch,
) -> None:
    require_missing_license(monkeypatch, tmp_path)
    from app.worker import enqueue_scheduled

    engine, factory = database_factory(tmp_path)
    current = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    with Session(engine) as db:
        user = add_user(db)
        db.add(
            SchedulerSetting(
                user_id=user.id,
                enabled=True,
                interval_minutes=60,
                next_run_at=current - timedelta(minutes=1),
            )
        )
        db.commit()
    monkeypatch.setattr("app.worker.SessionLocal", factory)

    enqueue_scheduled(now=current)

    with Session(engine) as db:
        setting = db.scalar(select(SchedulerSetting))
        assert setting.next_run_at == current + timedelta(minutes=60)
        assert db.scalar(select(ScanRun.id)) is None


def test_worker_rechecks_entitlement_before_browser_start(tmp_path, monkeypatch) -> None:
    require_missing_license(monkeypatch, tmp_path)
    from app.worker import process_job

    engine, factory = database_factory(tmp_path)
    with Session(engine) as db:
        user = add_user(db)
        run = ScanRun(
            owner_id=user.id,
            kind="PROJECTS",
            status="QUEUED",
            payload={"project_ids": []},
        )
        db.add(run)
        db.commit()
        run_id = run.id
    monkeypatch.setattr("app.worker.SessionLocal", factory)

    process_job(run_id)

    with Session(engine) as db:
        run = db.get(ScanRun, run_id)
        assert run.status == "FAILED"
        assert run.error == "SearchEntitlementError: LICENSE_NOT_CONFIGURED"
        assert run.payload["failures"][0]["code"] == "LICENSE_NOT_CONFIGURED"
