"""Native desktop identity and Cloudflare license-service synchronization.

The device signing seed is never stored in SQLite or a SearchCar backup.  On
macOS it lives in the user's Keychain.  On Windows only a DPAPI-protected blob
is written under the excluded ``license`` directory.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import re
import secrets
import subprocess
import sys
import threading
from typing import Any, Protocol
from urllib.parse import urlparse
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx

from .database import settings
from .desktop_license import (
    LICENSE_BINDING_NAME,
    LICENSE_DIRECTORY_NAME,
    LICENSE_LEASE_NAME,
    LicenseStateError,
    _atomic_json,
    _configured_license_document,
    _configured_public_keys,
    _license_paths,
    _read_json,
    _verify_lease_document,
    canonical_json,
    evaluate_search_entitlement,
)


DEVICE_MESSAGE_PREFIX = "SEARCHCAR-DEVICE-REQUEST-V1\n"
KEYCHAIN_SERVICE = "com.searchcar.desktop.device-signing"
KEYCHAIN_ACCOUNT = "device-signing-key-v1"
DPAPI_KEY_NAME = "device-key.dpapi"
MAX_LICENSE_RESPONSE_BYTES = 128 * 1024
_LICENSE_OPERATION_LOCK = threading.Lock()
_ACTIVATION_CODE = re.compile(
    r"^SC-[23456789A-HJ-NP-Z]{5}(?:-[23456789A-HJ-NP-Z]{5}){3}$"
)
logger = logging.getLogger(__name__)


class LicenseClientError(RuntimeError):
    def __init__(self, code: str, *, http_status: int = 503):
        super().__init__(code)
        self.code = code
        self.http_status = http_status


class DeviceSecretStore(Protocol):
    def load(self) -> bytes | None: ...

    def save(self, value: bytes) -> None: ...


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_secret(value: str) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, UnicodeError) as exc:
        raise LicenseClientError("LICENSE_DEVICE_KEY_INVALID") from exc
    if len(decoded) != 32:
        raise LicenseClientError("LICENSE_DEVICE_KEY_INVALID")
    return decoded


class MacOSKeychainStore:
    """Store the Ed25519 seed in the login Keychain without argv disclosure."""

    executable = "/usr/bin/security"

    def load(self) -> bytes | None:
        result = subprocess.run(
            [
                self.executable,
                "find-generic-password",
                "-a",
                KEYCHAIN_ACCOUNT,
                "-s",
                KEYCHAIN_SERVICE,
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 44:
            return None
        if result.returncode != 0:
            raise LicenseClientError("LICENSE_KEYCHAIN_UNAVAILABLE")
        return _decode_secret(result.stdout.strip())

    def save(self, value: bytes) -> None:
        # Omitting the value after -w makes `security` read it from stdin, so
        # the private seed never appears in the process list.
        result = subprocess.run(
            [
                self.executable,
                "add-generic-password",
                "-U",
                "-a",
                KEYCHAIN_ACCOUNT,
                "-s",
                KEYCHAIN_SERVICE,
                "-l",
                "SearchCar device signing key",
                "-w",
            ],
            input=_base64url(value) + "\n",
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise LicenseClientError("LICENSE_KEYCHAIN_UNAVAILABLE")


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
    temporary.write_bytes(value)
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    os.replace(temporary, path)


class WindowsDpapiStore:
    """Persist only a current-user DPAPI ciphertext in the license directory."""

    def __init__(self, data_dir: Path):
        self.path = data_dir / LICENSE_DIRECTORY_NAME / DPAPI_KEY_NAME

    @staticmethod
    def _crypt(value: bytes, *, protect: bool) -> bytes:
        if sys.platform != "win32":
            raise LicenseClientError("LICENSE_DPAPI_UNAVAILABLE")
        import ctypes
        from ctypes import wintypes

        class DataBlob(ctypes.Structure):
            _fields_ = [
                ("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
            ]

        def blob(data: bytes) -> tuple[DataBlob, Any]:
            buffer = ctypes.create_string_buffer(data)
            return (
                DataBlob(
                    len(data),
                    ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
                ),
                buffer,
            )

        source, source_buffer = blob(value)
        entropy, entropy_buffer = blob(
            hashlib.sha256(b"SearchCar DPAPI device key v1").digest()
        )
        output = DataBlob()
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        if protect:
            function = crypt32.CryptProtectData
            function.argtypes = [
                ctypes.POINTER(DataBlob),
                wintypes.LPCWSTR,
                ctypes.POINTER(DataBlob),
                ctypes.c_void_p,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(DataBlob),
            ]
            description: Any = "SearchCar device signing key"
        else:
            function = crypt32.CryptUnprotectData
            function.argtypes = [
                ctypes.POINTER(DataBlob),
                ctypes.POINTER(wintypes.LPWSTR),
                ctypes.POINTER(DataBlob),
                ctypes.c_void_p,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(DataBlob),
            ]
            description = None
        function.restype = wintypes.BOOL
        arguments: list[Any] = [
            ctypes.byref(source),
            description,
            ctypes.byref(entropy),
            None,
            None,
            0x1,
            ctypes.byref(output),
        ]
        if not function(*arguments):
            raise LicenseClientError("LICENSE_DPAPI_UNAVAILABLE")
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
            kernel32.LocalFree.restype = wintypes.HLOCAL
            kernel32.LocalFree(ctypes.cast(output.pbData, wintypes.HLOCAL))
            del source_buffer, entropy_buffer

    def load(self) -> bytes | None:
        if not self.path.is_file():
            return None
        if self.path.is_symlink() or self.path.stat().st_size > 4096:
            raise LicenseClientError("LICENSE_DEVICE_KEY_INVALID")
        value = self._crypt(self.path.read_bytes(), protect=False)
        if len(value) != 32:
            raise LicenseClientError("LICENSE_DEVICE_KEY_INVALID")
        return value

    def save(self, value: bytes) -> None:
        _atomic_bytes(self.path, self._crypt(value, protect=True))


def native_secret_store(data_dir: Path) -> DeviceSecretStore:
    if sys.platform == "darwin":
        return MacOSKeychainStore()
    if sys.platform == "win32":
        return WindowsDpapiStore(data_dir)
    raise LicenseClientError("LICENSE_SECURE_STORE_UNAVAILABLE")


@dataclass(frozen=True)
class DeviceIdentity:
    private_key: Ed25519PrivateKey
    public_key: str
    fingerprint_hash: str
    label: str

    def signed_request(self, extra: dict[str, Any]) -> dict[str, Any]:
        requested_at = (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        unsigned: dict[str, Any] = {
            "protocol_version": 1,
            "request_id": str(uuid4()),
            "requested_at": requested_at,
            "app_version": settings.app_version,
            "device": {
                "public_key": self.public_key,
                "fingerprint_hash": self.fingerprint_hash,
                "label": self.label,
            },
            **extra,
        }
        signature = self.private_key.sign(
            (DEVICE_MESSAGE_PREFIX + canonical_json(unsigned)).encode("utf-8")
        )
        return {**unsigned, "proof": _base64url(signature)}


def load_or_create_device_identity(
    data_dir: Path,
    *,
    store: DeviceSecretStore | None = None,
    device_label: str | None = None,
) -> DeviceIdentity:
    selected_store = store or native_secret_store(data_dir)
    seed = selected_store.load()
    if seed is None:
        key = Ed25519PrivateKey.generate()
        seed = key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        selected_store.save(seed)
        persisted = selected_store.load()
        if persisted != seed:
            raise LicenseClientError("LICENSE_DEVICE_KEY_PERSISTENCE_FAILED")
    if len(seed) != 32:
        raise LicenseClientError("LICENSE_DEVICE_KEY_INVALID")
    private_key = Ed25519PrivateKey.from_private_bytes(seed)
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    fingerprint = hashlib.sha256(
        b"SEARCHCAR-DEVICE-FINGERPRINT-V1\n" + public_bytes
    ).hexdigest()
    label = (device_label or platform.node() or platform.system() or "SearchCar device")
    label = " ".join(label.split())[:100] or "SearchCar device"
    return DeviceIdentity(
        private_key=private_key,
        public_key=_base64url(public_bytes),
        fingerprint_hash=fingerprint,
        label=label,
    )


def configured_service_url() -> str:
    value = os.environ.get("SEARCHCAR_LICENSE_SERVICE_URL", "").strip().rstrip("/")
    if not value:
        configured_value = _configured_license_document().get("service_url", "")
        value = str(configured_value).strip().rstrip("/")
    if not value:
        raise LicenseClientError("LICENSE_SERVICE_NOT_CONFIGURED")
    parsed = urlparse(value)
    is_loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if (
        parsed.scheme not in ({"http", "https"} if is_loopback else {"https"})
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise LicenseClientError("LICENSE_SERVICE_URL_INVALID")
    return value


class LicenseServiceClient:
    def __init__(
        self,
        data_dir: Path,
        *,
        service_url: str | None = None,
        store: DeviceSecretStore | None = None,
        transport: httpx.BaseTransport | None = None,
        device_label: str | None = None,
    ):
        self.data_dir = data_dir.expanduser().resolve()
        self.service_url = service_url.rstrip("/") if service_url else configured_service_url()
        self.identity = load_or_create_device_identity(
            self.data_dir,
            store=store,
            device_label=device_label,
        )
        self.transport = transport

    def _post(self, path: str, extra: dict[str, Any]) -> dict[str, Any]:
        body = self.identity.signed_request(extra)
        try:
            with httpx.Client(
                timeout=httpx.Timeout(12.0, connect=5.0),
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                response = client.post(
                    self.service_url + path,
                    headers={
                        "Content-Type": "application/json",
                        "Idempotency-Key": body["request_id"],
                    },
                    content=json.dumps(body, separators=(",", ":")).encode("utf-8"),
                )
        except (httpx.HTTPError, OSError) as exc:
            raise LicenseClientError("LICENSE_SERVICE_UNAVAILABLE") from exc
        if len(response.content) > MAX_LICENSE_RESPONSE_BYTES:
            raise LicenseClientError("LICENSE_SERVICE_RESPONSE_TOO_LARGE", http_status=502)
        try:
            envelope = response.json()
        except (ValueError, UnicodeDecodeError) as exc:
            raise LicenseClientError("LICENSE_SERVICE_RESPONSE_INVALID", http_status=502) from exc
        if not isinstance(envelope, dict):
            raise LicenseClientError("LICENSE_SERVICE_RESPONSE_INVALID", http_status=502)
        if response.status_code != 200 or envelope.get("ok") is not True:
            error = envelope.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            raise LicenseClientError(
                code if isinstance(code, str) else "LICENSE_SERVICE_REJECTED",
                http_status=response.status_code if 400 <= response.status_code < 500 else 503,
            )
        data = envelope.get("data")
        if not isinstance(data, dict):
            raise LicenseClientError("LICENSE_SERVICE_RESPONSE_INVALID", http_status=502)
        return data

    def _binding(self) -> tuple[str, str]:
        binding_path, _, _ = _license_paths(self.data_dir)
        try:
            binding = _read_json(binding_path)
        except LicenseStateError as exc:
            raise LicenseClientError(exc.code, http_status=409) from exc
        license_id = binding.get("license_id")
        device_id = binding.get("device_id")
        if not isinstance(license_id, str) or not isinstance(device_id, str):
            raise LicenseClientError("LICENSE_NOT_CONFIGURED", http_status=409)
        return license_id, device_id

    def _install(self, data: dict[str, Any], *, fallback_binding: tuple[str, str] | None = None) -> dict[str, Any]:
        lease = data.get("lease")
        if not isinstance(lease, dict):
            raise LicenseClientError("LICENSE_SERVICE_RESPONSE_INVALID", http_status=502)
        try:
            public_keys = _configured_public_keys()
            payload = _verify_lease_document(lease, public_keys)
        except LicenseStateError as exc:
            raise LicenseClientError(exc.code, http_status=502) from exc
        license_id = data.get("license_id")
        device_id = data.get("device_id")
        if fallback_binding is not None:
            license_id = license_id or fallback_binding[0]
            device_id = device_id or fallback_binding[1]
        if (
            not isinstance(license_id, str)
            or not isinstance(device_id, str)
            or payload.get("license_id") != license_id
            or payload.get("device_id") != device_id
        ):
            raise LicenseClientError("LICENSE_DEVICE_MISMATCH", http_status=502)
        binding_path, lease_path, _ = _license_paths(self.data_dir)
        _atomic_json(
            binding_path,
            {"protocol_version": 1, "license_id": license_id, "device_id": device_id},
        )
        _atomic_json(lease_path, lease)
        return evaluate_search_entitlement(
            data_dir=self.data_dir,
            public_keys=public_keys,
            mode="required",
        ).as_dict()

    def activate_trial(self, subject: str) -> dict[str, Any]:
        normalized = subject.strip().casefold()
        if len(normalized) < 3 or len(normalized) > 254:
            raise LicenseClientError("LICENSE_TRIAL_SUBJECT_INVALID", http_status=400)
        subject_hash = hashlib.sha256(
            ("SEARCHCAR-TRIAL-SUBJECT-V1\n" + normalized).encode("utf-8")
        ).hexdigest()
        with _LICENSE_OPERATION_LOCK:
            data = self._post("/v1/trials/activate", {"subject_hash": subject_hash})
            return self._install(data)

    def refresh(self) -> dict[str, Any]:
        with _LICENSE_OPERATION_LOCK:
            binding = self._binding()
            data = self._post(
                "/v1/licenses/check",
                {"license_id": binding[0], "device_id": binding[1]},
            )
            return self._install(data, fallback_binding=binding)

    def redeem(self, activation_code: str) -> dict[str, Any]:
        normalized = "".join(activation_code.upper().split())
        if not _ACTIVATION_CODE.fullmatch(normalized):
            raise LicenseClientError("INVALID_CODE", http_status=400)
        with _LICENSE_OPERATION_LOCK:
            data = self._post(
                "/v1/licenses/redeem",
                {"activation_code": normalized},
            )
            return self._install(data)


def service_is_configured() -> bool:
    try:
        configured_service_url()
        return bool(_configured_public_keys())
    except (LicenseClientError, LicenseStateError):
        return False


def run_periodic_license_sync(
    stop_event: threading.Event,
    data_dir: Path,
    *,
    interval_seconds: float = 6 * 60 * 60,
    initial_delay_seconds: float = 10,
) -> None:
    """Refresh an existing binding without creating an unsolicited identity."""

    binding_path = data_dir / LICENSE_DIRECTORY_NAME / LICENSE_BINDING_NAME
    if not binding_path.is_file() or not service_is_configured():
        return
    if stop_event.wait(max(0, initial_delay_seconds)):
        return
    while not stop_event.is_set():
        try:
            status = LicenseServiceClient(data_dir).refresh()
            logger.info(
                "Desktop license synchronized: status=%s lease_expires_at=%s",
                status.get("status"),
                status.get("lease_expires_at"),
            )
        except LicenseClientError as exc:
            logger.warning("Desktop license synchronization failed: %s", exc.code)
        if stop_event.wait(max(60, interval_seconds)):
            return
