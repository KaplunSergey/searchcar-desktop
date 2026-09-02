import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
import threading
import time
from types import SimpleNamespace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
import httpx
import pytest

from app.desktop_license import LEASE_MESSAGE_PREFIX, canonical_json
from app.desktop_license_client import (
    DEVICE_MESSAGE_PREFIX,
    LicenseClientError,
    LicenseServiceClient,
    MacOSDeviceSecretStore,
    MacOSKeychainStore,
    MacOSPreviewFileSecretStore,
    load_or_create_device_identity,
    has_local_license_binding,
    native_secret_store,
    run_periodic_license_sync,
)


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


class MemoryStore:
    def __init__(self):
        self.value = None

    def load(self):
        return self.value

    def save(self, value):
        self.value = value


def signing_state(monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    monkeypatch.setenv(
        "SEARCHCAR_LICENSE_PUBLIC_KEYS_JSON",
        json.dumps({"worker-test-v1": b64url(public_key)}),
    )
    return private_key


def signed_lease(private_key, request_body, *, license_id, device_id, now):
    payload = {
        "type": "searchcar-license-lease",
        "protocol_version": 1,
        "key_id": "worker-test-v1",
        "server_time": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "issued_at": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "license_id": license_id,
        "device_id": device_id,
        "license_type": "TRIAL",
        "subscription_expires_at": (now + timedelta(days=30))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "lease_expires_at": (now + timedelta(hours=48))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "entitlements": {
            "search": True,
            "data_access": True,
            "backup_restore": True,
            "sources": ["encar"],
        },
        "app_version": request_body["app_version"],
    }
    signature = private_key.sign(
        (LEASE_MESSAGE_PREFIX + canonical_json(payload)).encode("utf-8")
    )
    return {"payload": payload, "signature": b64url(signature)}


def verify_device_request(body):
    proof = body.pop("proof")
    public_key = base64.urlsafe_b64decode(
        body["device"]["public_key"] + "="
    )
    Ed25519PublicKey.from_public_bytes(public_key).verify(
        base64.urlsafe_b64decode(proof + "=="),
        (DEVICE_MESSAGE_PREFIX + canonical_json(body)).encode("utf-8"),
    )


def test_device_identity_is_stable_and_private_key_stays_in_store(tmp_path) -> None:
    store = MemoryStore()

    first = load_or_create_device_identity(tmp_path, store=store, device_label="Test Mac")
    second = load_or_create_device_identity(tmp_path, store=store, device_label="Test Mac")

    assert first.public_key == second.public_key
    assert first.fingerprint_hash == second.fingerprint_hash
    assert len(store.value) == 32
    assert not (tmp_path / "license" / "device-key.dpapi").exists()


def test_concurrent_first_run_creates_one_device_identity(tmp_path) -> None:
    class SlowFirstLoadStore:
        def __init__(self):
            self.value: bytes | None = None
            self.save_count = 0

        def load(self) -> bytes | None:
            snapshot = self.value
            if snapshot is None:
                time.sleep(0.05)
            return snapshot

        def save(self, value: bytes) -> None:
            self.save_count += 1
            self.value = value

    store = SlowFirstLoadStore()
    start = threading.Barrier(3)
    identities = []
    failures = []

    def create() -> None:
        try:
            start.wait(timeout=2)
            identities.append(load_or_create_device_identity(tmp_path, store=store))
        except Exception as exc:  # pragma: no cover - assertion reports the cause.
            failures.append(exc)

    threads = [threading.Thread(target=create) for _ in range(2)]
    for thread in threads:
        thread.start()
    start.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=2)

    assert failures == []
    assert len(identities) == 2
    assert identities[0].public_key == identities[1].public_key
    assert store.save_count == 1


def test_macos_keychain_uses_native_bridge() -> None:
    secret = b"k" * 32

    class MemoryKeychain:
        value: bytes | None = None

        def load(self) -> bytes | None:
            return self.value

        def save(self, value: bytes) -> None:
            self.value = value

    api = MemoryKeychain()
    store = MacOSKeychainStore(api=api)

    assert store.load() is None
    store.save(secret)
    assert store.load() == secret
    assert api.value == secret


def test_macos_keychain_migrates_legacy_base64_seed_without_identity_change() -> None:
    secret = b"m" * 32

    class LegacyKeychain:
        value = b64url(secret).encode("ascii")

        def load(self) -> bytes | None:
            return self.value

        def save(self, value: bytes) -> None:
            self.value = value

    api = LegacyKeychain()
    store = MacOSKeychainStore(api=api)

    assert store.load() == secret
    assert api.value == secret
    assert store.load() == secret


@pytest.mark.skipif(os.name == "nt", reason="macOS mode-0600 fallback semantics")
def test_macos_keychain_authorization_failure_uses_private_file(tmp_path) -> None:
    secret = b"f" * 32

    class DeniedKeychain:
        def load(self) -> bytes | None:
            raise LicenseClientError("LICENSE_KEYCHAIN_UNAVAILABLE")

        def save(self, value: bytes) -> None:
            raise LicenseClientError("LICENSE_KEYCHAIN_UNAVAILABLE")

    store = MacOSDeviceSecretStore(
        tmp_path,
        keychain=DeniedKeychain(),
        allow_file_fallback=True,
    )

    assert store.load() is None
    store.save(secret)
    assert store.load() == secret
    assert store.path.read_bytes() == secret
    assert store.path.stat().st_mode & 0o077 == 0


@pytest.mark.skipif(os.name == "nt", reason="macOS mode-0600 fallback semantics")
def test_macos_preview_file_store_is_stable_without_keychain(tmp_path) -> None:
    store = MacOSPreviewFileSecretStore(tmp_path)

    first = load_or_create_device_identity(tmp_path, store=store)
    second = load_or_create_device_identity(tmp_path, store=store)

    assert first.public_key == second.public_key
    assert store.path.is_file()
    assert store.path.stat().st_size == 32
    assert store.path.stat().st_mode & 0o077 == 0


def test_native_macos_store_uses_explicit_preview_config(tmp_path, monkeypatch) -> None:
    config = tmp_path / "license-service.json"
    config.write_text(
        json.dumps(
            {
                "protocol_version": 1,
                "service_url": "https://license.example.test",
                "public_keys": {"test-key": "a" * 43},
                "enforcement": "disabled",
                "device_key_storage": "file-preview",
            }
        )
    )
    monkeypatch.setenv("SEARCHCAR_LICENSE_CONFIG_FILE", str(config))
    monkeypatch.setattr("app.desktop_license_client.sys.platform", "darwin")

    store = native_secret_store(tmp_path / "data")

    assert isinstance(store, MacOSPreviewFileSecretStore)


def test_macos_readable_keychain_does_not_write_raw_fallback(tmp_path) -> None:
    class ReliableKeychain:
        value: bytes | None = None

        def load(self) -> bytes | None:
            return self.value

        def save(self, value: bytes) -> None:
            self.value = value

    store = MacOSDeviceSecretStore(tmp_path, keychain=ReliableKeychain())

    first = load_or_create_device_identity(tmp_path, store=store)
    second = load_or_create_device_identity(tmp_path, store=store)

    assert first.public_key == second.public_key
    assert not store.path.exists()


@pytest.mark.skipif(os.name == "nt", reason="macOS mode-0600 fallback semantics")
def test_macos_keychain_success_with_missing_readback_keeps_stable_fallback(
    tmp_path,
) -> None:
    class WriteOnlyKeychain:
        def load(self) -> bytes | None:
            return None

        def save(self, value: bytes) -> None:
            assert len(value) == 32

    store = MacOSDeviceSecretStore(
        tmp_path,
        keychain=WriteOnlyKeychain(),
        allow_file_fallback=True,
    )

    first = load_or_create_device_identity(tmp_path, store=store)
    second = load_or_create_device_identity(tmp_path, store=store)

    assert first.public_key == second.public_key
    assert store.path.is_file()
    assert store.path.stat().st_size == 32
    assert store.path.stat().st_mode & 0o077 == 0


@pytest.mark.skipif(os.name == "nt", reason="macOS mode-0600 fallback semantics")
def test_macos_fallback_remains_authoritative_after_keychain_recovers(tmp_path) -> None:
    old_keychain_seed = b"o" * 32

    class RecoveringKeychain:
        denied = True

        def load(self) -> bytes | None:
            if self.denied:
                raise LicenseClientError("LICENSE_KEYCHAIN_UNAVAILABLE")
            return old_keychain_seed

        def save(self, _value: bytes) -> None:
            if self.denied:
                raise LicenseClientError("LICENSE_KEYCHAIN_UNAVAILABLE")

    keychain = RecoveringKeychain()
    store = MacOSDeviceSecretStore(
        tmp_path,
        keychain=keychain,
        allow_file_fallback=True,
    )
    first = load_or_create_device_identity(tmp_path, store=store)
    fallback_seed = store.path.read_bytes()

    keychain.denied = False
    second = load_or_create_device_identity(tmp_path, store=store)

    assert fallback_seed != old_keychain_seed
    assert first.public_key == second.public_key
    assert store.path.read_bytes() == fallback_seed


@pytest.mark.skipif(os.name == "nt", reason="macOS mode-0600 fallback semantics")
def test_macos_production_store_migrates_matching_fallback_to_keychain(
    tmp_path,
) -> None:
    seed = b"p" * 32

    class EmptyKeychain:
        value: bytes | None = None

        def load(self) -> bytes | None:
            return self.value

        def save(self, value: bytes) -> None:
            self.value = value

    pilot_store = MacOSDeviceSecretStore(
        tmp_path,
        keychain=EmptyKeychain(),
        allow_file_fallback=True,
    )
    pilot_store.path.parent.mkdir(parents=True)
    pilot_store.path.write_bytes(seed)
    pilot_store.path.chmod(0o600)
    production_keychain = EmptyKeychain()
    production_store = MacOSDeviceSecretStore(
        tmp_path,
        keychain=production_keychain,
    )

    assert production_store.load() == seed
    assert production_keychain.value == seed
    assert not production_store.path.exists()


def test_macos_production_store_fails_closed_when_keychain_is_denied(tmp_path) -> None:
    class DeniedKeychain:
        def load(self) -> bytes | None:
            raise LicenseClientError("LICENSE_KEYCHAIN_UNAVAILABLE")

        def save(self, _value: bytes) -> None:
            raise LicenseClientError("LICENSE_KEYCHAIN_UNAVAILABLE")

    store = MacOSDeviceSecretStore(tmp_path, keychain=DeniedKeychain())

    with pytest.raises(LicenseClientError) as caught:
        load_or_create_device_identity(tmp_path, store=store)

    assert caught.value.code == "LICENSE_KEYCHAIN_UNAVAILABLE"
    assert not store.path.exists()


def test_trial_request_is_signed_and_installs_verified_lease(tmp_path, monkeypatch) -> None:
    worker_key = signing_state(monkeypatch)
    license_id = "018f6ac2-8c44-7df0-8f6d-2d34af37b337"
    device_id = "028f6ac2-8c44-7df0-8f6d-2d34af37b337"
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        observed.update(body)
        unsigned = dict(body)
        verify_device_request(unsigned)
        assert request.headers["Idempotency-Key"] == body["request_id"]
        assert body["subject_hash"] != hashlib.sha256(b"owner@example.test").hexdigest()
        now = datetime.now(timezone.utc)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "data": {
                    "license_id": license_id,
                    "device_id": device_id,
                    "lease": signed_lease(
                        worker_key,
                        body,
                        license_id=license_id,
                        device_id=device_id,
                        now=now,
                    ),
                },
            },
        )

    client = LicenseServiceClient(
        tmp_path,
        service_url="https://license.example.test",
        store=MemoryStore(),
        transport=httpx.MockTransport(handler),
        device_label="Test Mac",
    )

    result = client.activate_trial("Owner@Example.Test")

    assert result["can_search"] is True
    assert result["license_id"] == license_id
    assert observed["device"]["label"] == "Test Mac"
    binding = json.loads((tmp_path / "license" / "binding.json").read_text())
    assert binding["device_id"] == device_id
    assert (tmp_path / "license" / "lease.json").is_file()
    assert (tmp_path / "license" / "trusted-time.json").is_file()


def test_lost_trial_response_retries_the_identical_signed_request(
    tmp_path, monkeypatch
) -> None:
    worker_key = signing_state(monkeypatch)
    observed: list[tuple[bytes, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append((request.content, request.headers["Idempotency-Key"]))
        if len(observed) == 1:
            raise httpx.ReadTimeout("response was lost", request=request)
        body = json.loads(request.content)
        now = datetime.now(timezone.utc)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "data": {
                    "license_id": "118f6ac2-8c44-7df0-8f6d-2d34af37b337",
                    "device_id": "128f6ac2-8c44-7df0-8f6d-2d34af37b337",
                    "lease": signed_lease(
                        worker_key,
                        body,
                        license_id="118f6ac2-8c44-7df0-8f6d-2d34af37b337",
                        device_id="128f6ac2-8c44-7df0-8f6d-2d34af37b337",
                        now=now,
                    ),
                },
            },
        )

    client = LicenseServiceClient(
        tmp_path,
        service_url="https://license.example.test",
        store=MemoryStore(),
        transport=httpx.MockTransport(handler),
    )

    result = client.activate_trial("retry@example.test")

    assert result["can_search"] is True
    assert len(observed) == 2
    assert observed[0] == observed[1]


def test_transfer_request_keeps_claim_token_out_of_license_state(tmp_path, monkeypatch) -> None:
    worker_key = signing_state(monkeypatch)
    license_id = "218f6ac2-8c44-7df0-8f6d-2d34af37b337"
    device_id = "228f6ac2-8c44-7df0-8f6d-2d34af37b337"
    transfer_code = "TR-23456-789AB-CDEFG-HJKLM"
    claim_token = "A" * 32
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        body = json.loads(request.content)
        unsigned = dict(body)
        verify_device_request(unsigned)
        if request.url.path == "/v1/transfers/request":
            assert body.get("license_id") is None
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "data": {
                        "transfer_code": transfer_code,
                        "claim_token": claim_token,
                        "expires_at": "2026-08-16T18:00:00.000Z",
                    },
                },
            )
        assert request.url.path == "/v1/transfers/claim"
        assert body["transfer_code"] == transfer_code
        assert body["claim_token"] == claim_token
        now = datetime.now(timezone.utc)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "data": {
                    "license_id": license_id,
                    "device_id": device_id,
                    "lease": signed_lease(
                        worker_key,
                        body,
                        license_id=license_id,
                        device_id=device_id,
                        now=now,
                    ),
                },
            },
        )

    client = LicenseServiceClient(
        tmp_path,
        service_url="https://license.example.test",
        store=MemoryStore(),
        transport=httpx.MockTransport(handler),
    )

    request = client.request_transfer()

    assert request["transfer_code"] == transfer_code
    assert request["claim_token"] == claim_token
    assert not (tmp_path / "license").exists()

    claimed = client.claim_transfer(request["transfer_code"], request["claim_token"])

    assert claimed["license_id"] == license_id
    assert requests == ["/v1/transfers/request", "/v1/transfers/claim"]
    binding_text = (tmp_path / "license" / "binding.json").read_text()
    assert claim_token not in binding_text
    assert claim_token not in (tmp_path / "license" / "lease.json").read_text()


def test_tampered_worker_lease_is_not_persisted(tmp_path, monkeypatch) -> None:
    signing_state(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "data": {
                    "license_id": "018f6ac2-8c44-7df0-8f6d-2d34af37b337",
                    "device_id": "028f6ac2-8c44-7df0-8f6d-2d34af37b337",
                    "lease": {"payload": {"type": "searchcar-license-lease"}, "signature": "bad"},
                },
            },
        )

    client = LicenseServiceClient(
        tmp_path,
        service_url="https://license.example.test",
        store=MemoryStore(),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(LicenseClientError) as caught:
        client.activate_trial("owner@example.test")

    assert caught.value.code.startswith("LICENSE_")
    assert not (tmp_path / "license" / "binding.json").exists()
    assert not (tmp_path / "license" / "lease.json").exists()


def test_untrusted_worker_error_code_is_not_forwarded(tmp_path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "ok": False,
                "error": {"code": "<script>alert(1)</script>", "message": "bad"},
            },
        )

    client = LicenseServiceClient(
        tmp_path,
        service_url="https://license.example.test",
        store=MemoryStore(),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(LicenseClientError) as caught:
        client.activate_trial("owner@example.test")

    assert caught.value.code == "LICENSE_SERVICE_REJECTED"


def test_refresh_of_deleted_license_clears_license_but_keeps_device_identity(
    tmp_path,
) -> None:
    license_dir = tmp_path / "license"
    license_dir.mkdir()
    for name, payload in (
        ("binding.json", {"license_id": "018f6ac2-8c44-7df0-8f6d-2d34af37b337", "device_id": "028f6ac2-8c44-7df0-8f6d-2d34af37b337"}),
        ("lease.json", {"cached": "lease"}),
        ("trusted-time.json", {"cached": "time"}),
    ):
        (license_dir / name).write_text(json.dumps(payload), encoding="utf-8")
    store = MemoryStore()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            410,
            json={
                "ok": False,
                "error": {"code": "LICENSE_DELETED", "message": "deleted"},
            },
        )

    client = LicenseServiceClient(
        tmp_path,
        service_url="https://license.example.test",
        store=store,
        transport=httpx.MockTransport(handler),
    )
    device_seed = store.value

    with pytest.raises(LicenseClientError) as caught:
        client.refresh()

    assert caught.value.code == "LICENSE_DELETED"
    assert store.value == device_seed
    assert store.value is not None
    assert not (license_dir / "binding.json").exists()
    assert not (license_dir / "lease.json").exists()
    assert not (license_dir / "trusted-time.json").exists()


def test_existing_license_binding_is_detected_without_opening_secure_storage(tmp_path) -> None:
    license_dir = tmp_path / "license"
    license_dir.mkdir()
    (license_dir / "binding.json").write_text(
        json.dumps(
            {
                "license_id": "018f6ac2-8c44-7df0-8f6d-2d34af37b337",
                "device_id": "028f6ac2-8c44-7df0-8f6d-2d34af37b337",
            }
        ),
        encoding="utf-8",
    )

    assert has_local_license_binding(tmp_path)

    (license_dir / "binding.json").write_text("{}", encoding="utf-8")
    assert not has_local_license_binding(tmp_path)


def test_desktop_license_operation_logs_only_sanitized_diagnostics(
    tmp_path, monkeypatch, caplog
) -> None:
    from fastapi import HTTPException

    from app import desktop_license_client
    from app.database import settings

    monkeypatch.setattr(settings, "storage_root", str(tmp_path / "storage"))
    from app.main import _desktop_license_operation

    class BrokenClient:
        def __init__(self, _data_dir):
            try:
                raise OSError("do-not-log-this-sensitive-marker")
            except OSError as cause:
                raise LicenseClientError("LICENSE_SERVICE_CONNECT_FAILED") from cause

    monkeypatch.setattr(desktop_license_client, "LicenseServiceClient", BrokenClient)
    monkeypatch.setattr("app.main.desktop_data_root", lambda: tmp_path)

    with caplog.at_level(logging.WARNING, logger="app.main"):
        with pytest.raises(HTTPException) as caught:
            _desktop_license_operation("trial", lambda _client: {})

    assert caught.value.detail == "license_service_connect_failed"
    assert "action=trial" in caplog.text
    assert "code=LICENSE_SERVICE_CONNECT_FAILED" in caplog.text
    assert "cause_type=OSError" in caplog.text
    assert "do-not-log-this-sensitive-marker" not in caplog.text


def test_periodic_sync_does_not_create_identity_without_binding(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.desktop_license_client.service_is_configured", lambda: True)
    monkeypatch.setattr(
        "app.desktop_license_client.LicenseServiceClient",
        lambda *_args, **_kwargs: pytest.fail("client must not be created"),
    )

    run_periodic_license_sync(
        threading.Event(),
        tmp_path,
        initial_delay_seconds=0,
    )
