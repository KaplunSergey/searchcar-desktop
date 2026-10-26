"""Desktop-owned Telegram credentials and read-only Bot API verification.

No publications, delivery queue, license-key format or plaintext fallback.
"""
import hashlib
import json
import os
import re
import sys
from http.client import HTTPException as HttpClientError
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request as UrlRequest, build_opener

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .auth import require_csrf, require_user
from .desktop_license_client import (
    LicenseClientError, MacOSKeychainApi, WindowsDpapiStore, _atomic_bytes,
)
from .models import User

TOKEN = re.compile(r"[1-9][0-9]{0,19}:[A-Za-z0-9_-]{20,128}\Z")
USERNAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{3,31}\Z")
MAX_BYTES = 32768
LOCK = RLock()
router = APIRouter(prefix="/api/desktop/telegram", tags=["telegram"])


class TelegramError(Exception):
    def __init__(self, code: str, status: int = 503):
        self.code, self.status = code, status
        super().__init__(code)


class TelegramSecretStore:
    def __init__(self, root: Path, owner_id: int):
        # A different profile/owner never inherits another profile's connection.
        scope = hashlib.sha256(f"{root.resolve()}:{owner_id}".encode()).hexdigest()
        self.context = f"SearchCar Telegram connection v1:{scope}".encode()
        self.keychain = None
        self.path = root / "secrets" / f"telegram-{owner_id}.dpapi"
        try:
            if sys.platform == "darwin":
                self.keychain = MacOSKeychainApi(
                    service="com.searchcar.desktop.telegram.v1", account=scope,
                )
            elif sys.platform != "win32":
                raise TelegramError("telegram_secure_storage_unavailable")
        except OSError:
            raise TelegramError("telegram_secure_storage_unavailable") from None

    def _check_path(self):
        if self.path.parent.is_symlink() or self.path.is_symlink():
            raise OSError("unsafe secret path")

    def load(self) -> bytes | None:
        try:
            if self.keychain is not None:
                value = self.keychain.load()
            else:
                self._check_path()
                if not self.path.exists():
                    return None
                if self.path.stat().st_size > MAX_BYTES:
                    raise ValueError()
                value = WindowsDpapiStore._crypt(
                    self.path.read_bytes(), protect=False, context=self.context,
                )
            if value and len(value) > MAX_BYTES:
                raise ValueError()
            return value or None
        except (OSError, ValueError, LicenseClientError):
            raise TelegramError("telegram_secure_storage_unavailable") from None

    def save(self, value: bytes):
        try:
            if len(value) > MAX_BYTES:
                raise ValueError()
            if self.keychain is not None:
                self.keychain.save(value)
            else:
                self._check_path()
                _atomic_bytes(self.path, WindowsDpapiStore._crypt(
                    value, protect=True, context=self.context,
                    description_text="SearchCar Telegram connection",
                ))
            if self.load() != value:
                raise ValueError()
        except (OSError, ValueError, LicenseClientError):
            raise TelegramError("telegram_secure_storage_unavailable") from None

    def clear(self):
        try:
            if self.keychain is not None:
                # Erase credential bytes, retaining only the empty scoped entry.
                self.keychain.save(b"")
            else:
                self._check_path()
                self.path.unlink(missing_ok=True)
            if self.load() is not None:
                raise ValueError()
        except (OSError, ValueError, LicenseClientError):
            raise TelegramError("telegram_secure_storage_unavailable") from None


def connection_store(owner_id: int):
    configured = os.environ.get("SEARCHCAR_DESKTOP_DATA_DIR")
    if not configured:
        raise TelegramError("desktop_runtime_required", 404)
    return TelegramSecretStore(Path(configured).expanduser().resolve(), owner_id)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def bot_api(token: str, method: str, parameters: dict) -> dict:
    if not TOKEN.fullmatch(token) or method not in {"getMe", "getChat", "getChatMember"}:
        raise TelegramError("telegram_request_invalid", 422)
    # urllib has no automatic request-URL logger. Never log/rethrow its exceptions:
    # the Telegram authentication token is necessarily part of the request URL.
    request = UrlRequest(f"https://api.telegram.org/bot{token}/{method}",
                         data=json.dumps(parameters).encode(),
                         headers={"Content-Type": "application/json"}, method="POST")
    try:
        with build_opener(NoRedirect()).open(request, timeout=12) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError()
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("ok") is not True or not isinstance(value.get("result"), dict):
            raise ValueError()
        return value["result"]
    except HTTPError as error:
        code = error.code
        error.close()
        if code == 401 or code == 404 and method == "getMe":
            raise TelegramError("telegram_token_rejected", 422) from None
        if code == 403:
            raise TelegramError("telegram_channel_access_denied", 422) from None
        if code == 400 and method != "getMe":
            raise TelegramError("telegram_channel_unavailable", 422) from None
        if code == 429:
            raise TelegramError("telegram_rate_limited", 429) from None
        raise TelegramError("telegram_api_unavailable") from None
    except (OSError, URLError, ValueError, HttpClientError):
        raise TelegramError("telegram_api_unavailable") from None


def normalize_channel(value: str) -> str | int:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise TelegramError("telegram_channel_invalid", 422)
    value = value.strip()
    if len(value) > 160:
        raise TelegramError("telegram_channel_invalid", 422)
    if value.startswith("https://"):
        try:
            url = urlsplit(value)
        except ValueError:
            raise TelegramError("telegram_channel_invalid", 422) from None
        if (url.netloc != "t.me" or url.query or url.fragment or url.username
                or url.path.count("/") != 1):
            raise TelegramError("telegram_channel_invalid", 422)
        value = "@" + url.path[1:]
    if value.startswith("@") and USERNAME.fullmatch(value[1:]):
        return value
    if re.fullmatch(r"-[1-9][0-9]{1,15}", value) and abs(int(value)) < 2**52:
        return int(value)
    # Private invite links are not chat IDs. Never resolve arbitrary URLs.
    raise TelegramError("telegram_channel_invalid", 422)


def _saved(store) -> dict | None:
    raw = store.load()
    if raw is None:
        return None
    try:
        value = json.loads(raw)
        if (type(value["v"]) is not int or value["v"] != 1 or not TOKEN.fullmatch(value["token"])
                or type(value["bot"]["id"]) is not int or not 0 < value["bot"]["id"] < 2**52
                or not USERNAME.fullmatch(value["bot"]["username"])
                or not isinstance(value["channels"], list) or len(value["channels"]) > 20):
            raise ValueError()
        for channel in value["channels"]:
            if (type(channel["chat_id"]) is not int or not -2**52 < channel["chat_id"] < 0
                    or not isinstance(channel["title"], str) or len(channel["title"]) > 128
                    or not isinstance(channel["checked_at"], str)
                    or datetime.fromisoformat(channel["checked_at"]).tzinfo is None):
                raise ValueError()
        return value
    except (KeyError, TypeError, ValueError):
        raise TelegramError("telegram_connection_invalid") from None


def public_status(record: dict | None) -> dict:
    return {"connected": record is not None,
            "bot": {key: record["bot"][key] for key in ("id", "username")} if record else None,
            "channels": [{key: item[key] for key in ("chat_id", "title", "checked_at")}
                         for item in record["channels"]] if record else [], "delivery_enabled": False}


def status(owner_id: int) -> dict:
    with LOCK:
        return public_status(_saved(connection_store(owner_id)))


def connect(owner_id: int, token: str) -> dict:
    if not TOKEN.fullmatch(token):
        raise TelegramError("telegram_token_invalid", 422)
    with LOCK:
        store = connection_store(owner_id)
        bot = bot_api(token, "getMe", {})
        if (bot.get("is_bot") is not True or type(bot.get("id")) is not int or not 0 < bot["id"] < 2**52
                or str(bot["id"]) != token.split(":")[0]
                or not isinstance(bot.get("username"), str) or not USERNAME.fullmatch(bot["username"])):
            raise TelegramError("telegram_bot_response_invalid")
        record = {"v": 1, "token": token, "bot": {"id": bot["id"], "username": bot["username"]}, "channels": []}
        # A replacement is saved only after verification. Old credentials survive failure.
        store.save(json.dumps(record).encode())
        return public_status(record)


def verify_channel(owner_id: int, channel: str) -> dict:
    target = normalize_channel(channel)
    with LOCK:
        store = connection_store(owner_id)
        record = _saved(store)
        if record is None:
            raise TelegramError("telegram_bot_required", 409)
        token, bot = record["token"], record["bot"]
        chat = bot_api(token, "getChat", {"chat_id": target})
        chat_id = chat.get("id")
        if chat.get("type") != "channel" or type(chat_id) is not int or not -2**52 < chat_id < 0:
            raise TelegramError("telegram_channel_required", 422)
        if isinstance(target, int) and chat_id != target:
            raise TelegramError("telegram_channel_response_invalid")
        member = bot_api(token, "getChatMember", {"chat_id": chat_id, "user_id": bot["id"]})
        if (not isinstance(member.get("user"), dict) or member["user"].get("id") != bot["id"]
                or member.get("status") != "administrator" or member.get("can_post_messages") is not True):
            raise TelegramError("telegram_post_permission_required", 422)
        verified = {"chat_id": chat_id, "title": str(chat.get("title") or chat_id).replace(token, "[redacted]")[:128],
                    "checked_at": datetime.now(timezone.utc).isoformat()}
        channels = [item for item in record["channels"] if item["chat_id"] != chat_id]
        if len(channels) >= 20:
            raise TelegramError("telegram_channel_limit", 422)
        record["channels"] = [*channels, verified]
        store.save(json.dumps(record).encode())
        return public_status(record)


def disconnect(owner_id: int) -> dict:
    with LOCK:
        connection_store(owner_id).clear()
        return public_status(None)


async def _field(request: Request, name: str) -> str:
    # Parse locally: standard validation errors echo invalid input, including tokens.
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 4096:
            raise HTTPException(413, "telegram_request_too_large")
    try:
        body = json.loads(raw)
        if not isinstance(body, dict) or set(body) != {name} or not isinstance(body[name], str):
            raise ValueError()
        return body[name]
    except (ValueError, TypeError):
        raise HTTPException(422, "telegram_request_invalid") from None


async def _operation(function, *args):
    try:
        return await run_in_threadpool(function, *args)
    except TelegramError as error:
        raise HTTPException(error.status, error.code) from None


@router.get("")
async def connection_status(current: User = Depends(require_user)):
    return await _operation(status, current.id)


@router.post("/connect")
async def connect_bot(request: Request, current: User = Depends(require_csrf)):
    return await _operation(connect, current.id, await _field(request, "token"))


@router.post("/channels/verify")
async def check_channel(request: Request, current: User = Depends(require_csrf)):
    return await _operation(verify_channel, current.id, await _field(request, "channel"))


@router.delete("")
async def disconnect_bot(current: User = Depends(require_csrf)):
    return await _operation(disconnect, current.id)
