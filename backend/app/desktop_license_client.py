"""Native desktop identity and Cloudflare license-service synchronization.

The device signing seed is never stored in SQLite or a SearchCar backup.  On
macOS it normally lives in the user's Keychain, with a mode-0600 file fallback
for local ad-hoc builds that macOS refuses to authorize.  On Windows only a
DPAPI-protected blob is written under the excluded ``license`` directory.
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
MACOS_FILE_KEY_NAME = "device-key.macos"
MAX_LICENSE_RESPONSE_BYTES = 128 * 1024
_LICENSE_OPERATION_LOCK = threading.Lock()
_DEVICE_IDENTITY_LOCK = threading.Lock()
_ACTIVATION_CODE = re.compile(
    r"^SC-[23456789A-HJ-NP-Z]{5}(?:-[23456789A-HJ-NP-Z]{5}){3}$"
)
_REMOTE_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
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
    if re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is None:
        raise LicenseClientError("LICENSE_DEVICE_KEY_INVALID")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, UnicodeError) as exc:
        raise LicenseClientError("LICENSE_DEVICE_KEY_INVALID") from exc
    if len(decoded) != 32:
        raise LicenseClientError("LICENSE_DEVICE_KEY_INVALID")
    return decoded


class MacOSKeychainApi:
    """ctypes bridge for a login-Keychain generic password, without a CLI prompt."""

    _ERR_SEC_SUCCESS = 0
    _ERR_SEC_ITEM_NOT_FOUND = -25300

    def __init__(self) -> None:
        import ctypes

        self.ctypes = ctypes
        self.security = ctypes.CDLL(
            "/System/Library/Frameworks/Security.framework/Security"
        )
        self.core_foundation = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        self._configure_functions()
        self.service = KEYCHAIN_SERVICE.encode("utf-8")
        self.account = KEYCHAIN_ACCOUNT.encode("utf-8")

    def _configure_functions(self) -> None:
        ctypes = self.ctypes
        self.security.SecKeychainCopyDefault.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        self.security.SecKeychainCopyDefault.restype = ctypes.c_int32
        self.security.SecKeychainFindGenericPassword.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self.security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
        self.security.SecKeychainAddGenericPassword.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self.security.SecKeychainAddGenericPassword.restype = ctypes.c_int32
        self.security.SecKeychainItemModifyAttributesAndData.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        self.security.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
        self.security.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.security.SecKeychainItemFreeContent.restype = ctypes.c_int32
        self.core_foundation.CFRelease.argtypes = [ctypes.c_void_p]

    def _find(self, *, include_password: bool) -> tuple[int, int | None, bytes | None]:
        length = self.ctypes.c_uint32()
        password = self.ctypes.c_void_p()
        item = self.ctypes.c_void_p()
        keychain = self.ctypes.c_void_p()
        status = self.security.SecKeychainCopyDefault(self.ctypes.byref(keychain))
        if status != self._ERR_SEC_SUCCESS:
            return status, None, None
        try:
            status = self.security.SecKeychainFindGenericPassword(
                keychain,
                len(self.service),
                self.service,
                len(self.account),
                self.account,
                self.ctypes.byref(length) if include_password else None,
                self.ctypes.byref(password) if include_password else None,
                self.ctypes.byref(item),
            )
            if status != self._ERR_SEC_SUCCESS:
                return status, None, None
            value = self.ctypes.string_at(password, length.value) if include_password else None
            return status, item.value, value
        finally:
            if include_password and password.value:
                self.security.SecKeychainItemFreeContent(None, password)
            self._release_item(keychain.value)

    def _release_item(self, item: int | None) -> None:
        if item:
            self.core_foundation.CFRelease(item)

    def load(self) -> bytes | None:
        status, item, value = self._find(include_password=True)
        try:
            if status == self._ERR_SEC_ITEM_NOT_FOUND:
                return None
            if status != self._ERR_SEC_SUCCESS:
                raise OSError(f"SecKeychainFindGenericPassword failed: {status}")
            return value
        finally:
            self._release_item(item)

    def save(self, value: bytes) -> None:
        status, item, _ = self._find(include_password=False)
        buffer = self.ctypes.create_string_buffer(value)
        try:
            if status == self._ERR_SEC_ITEM_NOT_FOUND:
                new_item = self.ctypes.c_void_p()
                keychain = self.ctypes.c_void_p()
                status = self.security.SecKeychainCopyDefault(self.ctypes.byref(keychain))
                if status != self._ERR_SEC_SUCCESS:
                    raise OSError(f"SecKeychainCopyDefault failed: {status}")
                status = self.security.SecKeychainAddGenericPassword(
                    keychain,
                    len(self.service),
                    self.service,
                    len(self.account),
                    self.account,
                    len(value),
                    buffer,
                    self.ctypes.byref(new_item),
                )
                self._release_item(new_item.value)
                self._release_item(keychain.value)
            elif status == self._ERR_SEC_SUCCESS:
                status = self.security.SecKeychainItemModifyAttributesAndData(
                    item, None, len(value), buffer
                )
            if status != self._ERR_SEC_SUCCESS:
                raise OSError(f"SecKeychain add/update failed: {status}")
        finally:
            self._release_item(item)


class MacOSKeychainStore:
    """Store the Ed25519 seed using the native macOS Keychain API."""

    def __init__(self, api: MacOSKeychainApi | None = None):
        self.api = api or MacOSKeychainApi()

    def load(self) -> bytes | None:
        try:
            value = self.api.load()
        except OSError as exc:
            raise LicenseClientError("LICENSE_KEYCHAIN_UNAVAILABLE") from exc
        if value is None or len(value) == 32:
            return value
        # Builds before the native Security.framework bridge stored the same
        # seed as 43 ASCII base64url bytes. Migrate it in place so an upgrade
        # never changes the device identity or loses an existing binding.
        try:
            decoded = _decode_secret(value.decode("ascii"))
            self.api.save(decoded)
            persisted = self.api.load()
        except UnicodeDecodeError as exc:
            raise LicenseClientError("LICENSE_DEVICE_KEY_INVALID") from exc
        except OSError as exc:
            raise LicenseClientError("LICENSE_KEYCHAIN_UNAVAILABLE") from exc
        if persisted != decoded:
            raise LicenseClientError("LICENSE_DEVICE_KEY_PERSISTENCE_FAILED")
        value = decoded
        return value

    def save(self, value: bytes) -> None:
        try:
            self.api.save(value)
        except OSError as exc:
            raise LicenseClientError("LICENSE_KEYCHAIN_UNAVAILABLE") from exc


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        remaining = memoryview(value)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("device key write failed")
            remaining = remaining[written:]
        os.fsync(descriptor)
        if os.name != "nt" and os.fstat(descriptor).st_mode & 0o077:
            raise OSError("device key permissions are not private")
    except Exception:
        os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise
    os.close(descriptor)
    try:
        os.replace(temporary, path)
        if os.name != "nt" and path.stat().st_mode & 0o077:
            path.unlink(missing_ok=True)
            raise OSError("device key permissions are not private")
        if os.name != "nt":
            directory_descriptor = os.open(
                path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


class MacOSDeviceSecretStore:
    """Prefer Keychain and keep a private fallback for unreliable local builds."""

    def __init__(
        self,
        data_dir: Path,
        *,
        keychain: DeviceSecretStore | None = None,
        allow_file_fallback: bool = False,
    ):
        self.keychain = keychain or MacOSKeychainStore()
        self.path = data_dir / LICENSE_DIRECTORY_NAME / MACOS_FILE_KEY_NAME
        self.allow_file_fallback = allow_file_fallback

    def _load_file(self) -> bytes | None:
        return _load_private_device_key_file(self.path)

    def load(self) -> bytes | None:
        # Once the fallback was needed it becomes authoritative. Otherwise a
        # temporarily inaccessible old Keychain item could reappear later and
        # silently switch this installation to a different device identity.
        file_value = self._load_file()
        if file_value is not None:
            if self.allow_file_fallback:
                return file_value
            try:
                keychain_value = self.keychain.load()
            except LicenseClientError as exc:
                if exc.code != "LICENSE_KEYCHAIN_UNAVAILABLE":
                    raise
                raise LicenseClientError(
                    "LICENSE_FILE_FALLBACK_DISABLED"
                ) from exc
            if keychain_value is None:
                try:
                    self.keychain.save(file_value)
                    keychain_value = self.keychain.load()
                except LicenseClientError as exc:
                    if exc.code != "LICENSE_KEYCHAIN_UNAVAILABLE":
                        raise
                    raise LicenseClientError(
                        "LICENSE_FILE_FALLBACK_DISABLED"
                    ) from exc
            if keychain_value != file_value:
                raise LicenseClientError("LICENSE_FILE_FALLBACK_DISABLED")
            try:
                self.path.unlink()
            except OSError as exc:
                raise LicenseClientError(
                    "LICENSE_DEVICE_KEY_PERSISTENCE_FAILED"
                ) from exc
            return keychain_value
        try:
            value = self.keychain.load()
        except LicenseClientError as exc:
            if exc.code != "LICENSE_KEYCHAIN_UNAVAILABLE":
                raise
        else:
            if value is not None:
                return value
        return None

    def save(self, value: bytes) -> None:
        try:
            self.keychain.save(value)
        except LicenseClientError as exc:
            if exc.code != "LICENSE_KEYCHAIN_UNAVAILABLE":
                raise
        else:
            try:
                persisted = self.keychain.load()
            except LicenseClientError as exc:
                if exc.code != "LICENSE_KEYCHAIN_UNAVAILABLE":
                    raise
            else:
                if persisted == value:
                    return
                if persisted is not None:
                    raise LicenseClientError(
                        "LICENSE_DEVICE_KEY_PERSISTENCE_FAILED"
                    )
        # An ad-hoc signed macOS build can report a successful Keychain write
        # and then deny or hide the same item on readback. Only that unreliable
        # path receives the excluded mode-0600 fallback; a working production
        # Keychain never leaves the raw seed on disk.
        if not self.allow_file_fallback:
            raise LicenseClientError("LICENSE_KEYCHAIN_UNAVAILABLE")
        try:
            _atomic_bytes(self.path, value)
        except OSError as exc:
            raise LicenseClientError(
                "LICENSE_DEVICE_KEY_PERSISTENCE_FAILED"
            ) from exc


def _load_private_device_key_file(path: Path) -> bytes | None:
    if not path.exists():
        return None
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("device key path is not a regular file")
        path.chmod(0o600)
        metadata = path.stat()
        if metadata.st_size != 32 or metadata.st_mode & 0o077:
            raise OSError("device key file is not private")
        value = path.read_bytes()
    except OSError as exc:
        raise LicenseClientError("LICENSE_DEVICE_KEY_INVALID") from exc
    if len(value) != 32:
        raise LicenseClientError("LICENSE_DEVICE_KEY_INVALID")
    return value


class MacOSPreviewFileSecretStore:
    """Preview-only macOS store independent of unsigned-app Keychain access."""

    def __init__(self, data_dir: Path):
        self.path = data_dir / LICENSE_DIRECTORY_NAME / MACOS_FILE_KEY_NAME

    def load(self) -> bytes | None:
        return _load_private_device_key_file(self.path)

    def save(self, value: bytes) -> None:
        if len(value) != 32:
            raise LicenseClientError("LICENSE_DEVICE_KEY_INVALID")
        try:
            _atomic_bytes(self.path, value)
        except OSError as exc:
            raise LicenseClientError(
                "LICENSE_DEVICE_KEY_PERSISTENCE_FAILED"
            ) from exc


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
        try:
            configured_storage = _configured_license_document().get(
                "device_key_storage", "keychain"
            )
        except LicenseStateError as exc:
            raise LicenseClientError(exc.code) from exc
        if configured_storage == "file-preview":
            return MacOSPreviewFileSecretStore(data_dir)
        if configured_storage != "keychain":
            raise LicenseClientError("LICENSE_CONFIG_INVALID")
        return MacOSDeviceSecretStore(
            data_dir,
            allow_file_fallback=(
                os.environ.get("SEARCHCAR_ALLOW_DEVICE_KEY_FILE_FALLBACK") == "1"
            ),
        )
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


def _load_or_create_device_identity_unlocked(
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


def load_or_create_device_identity(
    data_dir: Path,
    *,
    store: DeviceSecretStore | None = None,
    device_label: str | None = None,
) -> DeviceIdentity:
    # Client construction happens before the higher-level operation lock. Keep
    # first-run identity creation atomic so concurrent API requests cannot
    # activate one key while another key wins the persistence race.
    with _DEVICE_IDENTITY_LOCK:
        return _load_or_create_device_identity_unlocked(
            data_dir,
            store=store,
            device_label=device_label,
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
        content = json.dumps(body, separators=(",", ":")).encode("utf-8")
        try:
            with httpx.Client(
                timeout=httpx.Timeout(12.0, connect=5.0),
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                for attempt in range(2):
                    try:
                        response = client.post(
                            self.service_url + path,
                            headers={
                                "Content-Type": "application/json",
                                "Idempotency-Key": body["request_id"],
                            },
                            content=content,
                        )
                        break
                    except (httpx.TransportError, OSError):
                        if attempt == 1:
                            raise
                else:  # pragma: no cover - the loop always returns or raises.
                    raise LicenseClientError("LICENSE_SERVICE_UNAVAILABLE")
        except httpx.ConnectTimeout as exc:
            raise LicenseClientError("LICENSE_SERVICE_CONNECT_TIMEOUT") from exc
        except httpx.ConnectError as exc:
            raise LicenseClientError("LICENSE_SERVICE_CONNECT_FAILED") from exc
        except httpx.ReadTimeout as exc:
            raise LicenseClientError("LICENSE_SERVICE_RESPONSE_TIMEOUT") from exc
        except httpx.TimeoutException as exc:
            raise LicenseClientError("LICENSE_SERVICE_TIMEOUT") from exc
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
                code
                if isinstance(code, str) and _REMOTE_ERROR_CODE.fullmatch(code)
                else "LICENSE_SERVICE_REJECTED",
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
