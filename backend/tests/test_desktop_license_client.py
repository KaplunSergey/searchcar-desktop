import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import threading
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
    MacOSKeychainStore,
    load_or_create_device_identity,
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
        "entitlements": {"search": True, "data_access": True, "backup_restore": True},
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


def test_macos_keychain_secret_is_supplied_over_stdin(monkeypatch) -> None:
    calls = []
    secret = b"k" * 32

    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        if arguments[1] == "find-generic-password" and len(calls) == 1:
            return SimpleNamespace(returncode=44, stdout="", stderr="not found")
        if arguments[1] == "add-generic-password":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout=b64url(secret) + "\n", stderr="")

    monkeypatch.setattr("app.desktop_license_client.subprocess.run", run)
    store = MacOSKeychainStore()

    assert store.load() is None
    store.save(secret)
    assert store.load() == secret
    add_arguments, add_options = calls[1]
    assert b64url(secret) not in add_arguments
    assert add_arguments[-1] == "-w"
    assert add_options["input"] == b64url(secret) + "\n"


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
