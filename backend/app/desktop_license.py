"""Offline-verifiable desktop license state and search entitlement checks.

The Cloudflare service signs leases.  The desktop backend treats those leases
as capabilities: data stays readable, while search is allowed only when the
cached capability is authentic, device-bound and still inside both expiry
boundaries.  License state lives outside the SQLite backup on purpose.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import secrets
import threading
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


LEASE_MESSAGE_PREFIX = "SEARCHCAR-LICENSE-LEASE-V1\n"
LICENSE_DIRECTORY_NAME = "license"
LICENSE_BINDING_NAME = "binding.json"
LICENSE_LEASE_NAME = "lease.json"
TRUSTED_TIME_NAME = "trusted-time.json"
CLOCK_ROLLBACK_TOLERANCE = timedelta(minutes=5)
_BASE64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_SOURCE_KEY = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
_TRUSTED_TIME_LOCK = threading.Lock()


class LicenseStateError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class SearchEntitlementError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class EntitlementDecision:
    mode: str
    status: str
    can_search: bool
    reason: str | None = None
    license_id: str | None = None
    device_id: str | None = None
    license_type: str | None = None
    subscription_expires_at: str | None = None
    lease_expires_at: str | None = None
    effective_time: str | None = None
    sources: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def enforcement_mode() -> str:
    raw = os.environ.get("SEARCHCAR_LICENSE_ENFORCEMENT", "").strip().lower()
    if not raw:
        raw = str(_configured_license_document().get("enforcement", "disabled")).lower()
    return "required" if raw in {"1", "true", "required", "enforced"} else "disabled"


def _desktop_data_dir(data_dir: Path | None) -> Path:
    if data_dir is not None:
        return data_dir.expanduser().resolve()
    configured = os.environ.get("SEARCHCAR_DESKTOP_DATA_DIR")
    if not configured:
        raise LicenseStateError("LICENSE_DESKTOP_RUNTIME_REQUIRED")
    return Path(configured).expanduser().resolve()


def _license_paths(data_dir: Path) -> tuple[Path, Path, Path]:
    directory = data_dir / LICENSE_DIRECTORY_NAME
    return (
        directory / LICENSE_BINDING_NAME,
        directory / LICENSE_LEASE_NAME,
        directory / TRUSTED_TIME_NAME,
    )


def canonical_json(value: Any) -> str:
    if value is None or isinstance(value, (bool, int, str)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, float):
        if not value.is_integer():
            raise LicenseStateError("LICENSE_PAYLOAD_NUMBER_INVALID")
        return str(int(value))
    if isinstance(value, list):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise LicenseStateError("LICENSE_PAYLOAD_KEY_INVALID")
        return "{" + ",".join(
            f"{json.dumps(key, ensure_ascii=False)}:{canonical_json(value[key])}"
            for key in sorted(value)
        ) + "}"
    raise LicenseStateError("LICENSE_PAYLOAD_VALUE_INVALID")


def _decode_base64url(value: str, *, expected_bytes: int) -> bytes:
    if not isinstance(value, str) or not value or not _BASE64URL.fullmatch(value):
        raise LicenseStateError("LICENSE_ENCODING_INVALID")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, binascii.Error) as exc:
        raise LicenseStateError("LICENSE_ENCODING_INVALID") from exc
    if len(decoded) != expected_bytes:
        raise LicenseStateError("LICENSE_ENCODING_INVALID")
    return decoded


def _read_json(path: Path, *, required: bool = True) -> dict[str, Any]:
    if path.is_symlink():
        raise LicenseStateError("LICENSE_STATE_SYMLINK_NOT_ALLOWED")
    if not path.is_file():
        if required:
            raise LicenseStateError("LICENSE_NOT_CONFIGURED")
        return {}
    if path.stat().st_size > 128 * 1024:
        raise LicenseStateError("LICENSE_STATE_TOO_LARGE")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LicenseStateError("LICENSE_STATE_INVALID") from exc
    if not isinstance(value, dict):
        raise LicenseStateError("LICENSE_STATE_INVALID")
    return value


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
    )
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    os.replace(temporary, path)


def _timestamp(value: Any, code: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise LicenseStateError(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LicenseStateError(code) from exc
    if parsed.tzinfo is None:
        raise LicenseStateError(code)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _configured_public_keys() -> dict[str, str]:
    keys: dict[str, str] = {}
    configured_keys = _configured_license_document().get("public_keys", {})
    if configured_keys:
        if not isinstance(configured_keys, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in configured_keys.items()
        ):
            raise LicenseStateError("LICENSE_PUBLIC_KEY_CONFIG_INVALID")
        keys.update(configured_keys)
    raw = os.environ.get("SEARCHCAR_LICENSE_PUBLIC_KEYS_JSON", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LicenseStateError("LICENSE_PUBLIC_KEY_CONFIG_INVALID") from exc
        if not isinstance(parsed, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in parsed.items()
        ):
            raise LicenseStateError("LICENSE_PUBLIC_KEY_CONFIG_INVALID")
        keys.update(parsed)
    public_key_file = os.environ.get("SEARCHCAR_LICENSE_PUBLIC_KEY_FILE", "").strip()
    if public_key_file:
        document = _read_json(Path(public_key_file).expanduser().resolve())
        if document.get("algorithm") != "Ed25519":
            raise LicenseStateError("LICENSE_PUBLIC_KEY_CONFIG_INVALID")
        key_id = document.get("key_id")
        public_key = document.get("public_key")
        if not isinstance(key_id, str) or not isinstance(public_key, str):
            raise LicenseStateError("LICENSE_PUBLIC_KEY_CONFIG_INVALID")
        keys[key_id] = public_key
    return keys


def _configured_license_document() -> dict[str, Any]:
    config_file = os.environ.get("SEARCHCAR_LICENSE_CONFIG_FILE", "").strip()
    if not config_file:
        return {}
    document = _read_json(Path(config_file).expanduser().resolve())
    if document.get("protocol_version") != 1:
        raise LicenseStateError("LICENSE_CONFIG_INVALID")
    return document


def _verify_lease_document(
    document: Mapping[str, Any],
    public_keys: Mapping[str, str],
) -> dict[str, Any]:
    payload = document.get("payload")
    signature_value = document.get("signature")
    if not isinstance(payload, dict) or not isinstance(signature_value, str):
        raise LicenseStateError("LICENSE_LEASE_INVALID")
    if payload.get("type") != "searchcar-license-lease" or payload.get("protocol_version") != 1:
        raise LicenseStateError("LICENSE_LEASE_PROTOCOL_INVALID")
    key_id = payload.get("key_id")
    if not isinstance(key_id, str) or key_id not in public_keys:
        raise LicenseStateError("LICENSE_SIGNING_KEY_UNKNOWN")
    public_key_bytes = _decode_base64url(public_keys[key_id], expected_bytes=32)
    signature = _decode_base64url(signature_value, expected_bytes=64)
    message = (LEASE_MESSAGE_PREFIX + canonical_json(payload)).encode("utf-8")
    try:
        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(signature, message)
    except (InvalidSignature, ValueError) as exc:
        raise LicenseStateError("LICENSE_SIGNATURE_INVALID") from exc
    return payload


def _trusted_effective_time(
    trusted_path: Path,
    *,
    observed: datetime,
    server_time: datetime,
    license_id: str,
    device_id: str,
    update: bool,
) -> datetime:
    with _TRUSTED_TIME_LOCK:
        trusted = _read_json(trusted_path, required=False)
        previous_server = _timestamp(
            trusted.get("server_time", _iso(server_time)),
            "LICENSE_TRUSTED_TIME_INVALID",
        )
        highest_observed = _timestamp(
            trusted.get("highest_observed_at", _iso(server_time)),
            "LICENSE_TRUSTED_TIME_INVALID",
        )
        if server_time < previous_server:
            raise LicenseStateError("LICENSE_SERVER_TIME_ROLLBACK")
        if (
            observed + CLOCK_ROLLBACK_TOLERANCE < highest_observed
            and server_time <= highest_observed
        ):
            raise LicenseStateError("LICENSE_CLOCK_ROLLBACK")
        effective_time = max(observed, server_time, highest_observed)
        if update:
            _atomic_json(
                trusted_path,
                {
                    "protocol_version": 1,
                    "license_id": license_id,
                    "device_id": device_id,
                    "server_time": _iso(max(server_time, previous_server)),
                    "highest_observed_at": _iso(effective_time),
                },
            )
        return effective_time


def evaluate_search_entitlement(
    *,
    now: datetime | None = None,
    data_dir: Path | None = None,
    public_keys: Mapping[str, str] | None = None,
    mode: str | None = None,
    update_trusted_time: bool = True,
) -> EntitlementDecision:
    selected_mode = mode or enforcement_mode()
    if selected_mode != "required":
        return EntitlementDecision(mode="disabled", status="pilot", can_search=True)
    try:
        root = _desktop_data_dir(data_dir)
        binding_path, lease_path, trusted_path = _license_paths(root)
        binding = _read_json(binding_path)
        document = _read_json(lease_path)
        payload = _verify_lease_document(
            document,
            public_keys if public_keys is not None else _configured_public_keys(),
        )

        license_id = payload.get("license_id")
        device_id = payload.get("device_id")
        if (
            not isinstance(license_id, str)
            or not isinstance(device_id, str)
            or binding.get("license_id") != license_id
            or binding.get("device_id") != device_id
        ):
            raise LicenseStateError("LICENSE_DEVICE_MISMATCH")

        server_time = _timestamp(payload.get("server_time"), "LICENSE_SERVER_TIME_INVALID")
        issued_at = _timestamp(payload.get("issued_at"), "LICENSE_ISSUED_AT_INVALID")
        lease_expires_at = _timestamp(
            payload.get("lease_expires_at"),
            "LICENSE_LEASE_EXPIRY_INVALID",
        )
        if server_time != issued_at or lease_expires_at <= issued_at:
            raise LicenseStateError("LICENSE_LEASE_TIME_INVALID")

        observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        effective_time = _trusted_effective_time(
            trusted_path,
            observed=observed,
            server_time=server_time,
            license_id=license_id,
            device_id=device_id,
            update=update_trusted_time,
        )

        entitlements = payload.get("entitlements")
        if not isinstance(entitlements, dict) or entitlements.get("search") is not True:
            raise LicenseStateError("LICENSE_SEARCH_DISABLED")
        # Leases issued before the source catalog existed are valid only for
        # the originally supported Encar source.  New leases always contain a
        # signed explicit list, so adding another source never silently grants
        # it to an older customer.
        raw_sources = entitlements.get("sources", ["encar"])
        if (
            not isinstance(raw_sources, list)
            or not raw_sources
            or len(raw_sources) > 32
            or any(
                not isinstance(source, str) or not _SOURCE_KEY.fullmatch(source)
                for source in raw_sources
            )
        ):
            raise LicenseStateError("LICENSE_SOURCES_INVALID")
        sources = tuple(sorted(set(raw_sources)))
        if effective_time >= lease_expires_at:
            raise LicenseStateError("LICENSE_LEASE_EXPIRED")
        subscription_value = payload.get("subscription_expires_at")
        subscription_expires_at = None
        if subscription_value is not None:
            subscription_expires_at = _timestamp(
                subscription_value,
                "LICENSE_SUBSCRIPTION_EXPIRY_INVALID",
            )
            if effective_time >= subscription_expires_at:
                raise LicenseStateError("LICENSE_SUBSCRIPTION_EXPIRED")

        return EntitlementDecision(
            mode="required",
            status="active",
            can_search=True,
            license_id=license_id,
            device_id=device_id,
            license_type=str(payload.get("license_type") or "UNKNOWN"),
            subscription_expires_at=(
                _iso(subscription_expires_at) if subscription_expires_at else None
            ),
            lease_expires_at=_iso(lease_expires_at),
            effective_time=_iso(effective_time),
            sources=sources,
        )
    except LicenseStateError as exc:
        return EntitlementDecision(
            mode="required",
            status="blocked",
            can_search=False,
            reason=exc.code,
        )


def require_search_entitlement(
    source_key: str | None = None,
    **kwargs: Any,
) -> EntitlementDecision:
    decision = evaluate_search_entitlement(**kwargs)
    if not decision.can_search:
        raise SearchEntitlementError(decision.reason or "LICENSE_REQUIRED")
    if (
        source_key is not None
        and decision.mode == "required"
        and source_key not in decision.sources
    ):
        raise SearchEntitlementError("LICENSE_SOURCE_NOT_ALLOWED")
    return decision
