"""Telegram connection only: disposable credentials, no live API or publications."""
import json

import pytest
from fastapi.testclient import TestClient

from app.auth import require_csrf, require_user
from app.main import app
from app.models import User


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("SEARCHCAR_DESKTOP_DATA_DIR", str(tmp_path))
    previous = dict(app.dependency_overrides)
    user = User(id=7, username="fixture", username_key="fixture", password_hash="!fixture")
    app.dependency_overrides[require_user] = lambda: user
    app.dependency_overrides[require_csrf] = lambda: user
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_invalid_token_is_rejected_without_echoing_the_secret(api):
    secret = "invalid-private-value"
    result = api.post("/api/desktop/telegram/connect", json={"token": secret})
    assert result.status_code == 422, result.text
    assert result.json() == {"detail": "telegram_token_invalid"}
    assert secret not in result.text


TOKEN = "123456:" + "fixture-secret-not-a-real-token_123"
BOT = {"id": 123456, "is_bot": True, "username": "fixture_search_bot"}


class MemoryStore:
    def __init__(self):
        self.raw = None
        self.fail_save = False

    def load(self):
        return self.raw

    def save(self, value):
        if self.fail_save:
            from app.telegram_connection import TelegramError
            raise TelegramError("telegram_secure_storage_unavailable")
        self.raw = value

    def clear(self):
        self.raw = None


@pytest.fixture
def connection(monkeypatch):
    from app import telegram_connection as telegram
    stores, calls = {}, []
    monkeypatch.setattr(telegram, "connection_store", lambda owner: stores.setdefault(owner, MemoryStore()))

    def bot_api(token, method, parameters):
        assert token == TOKEN
        calls.append((method, parameters))
        return {"getMe": BOT,
                "getChat": {"id": -1001234567890, "type": "channel", "title": "Fixture channel"},
                "getChatMember": {"user": BOT, "status": "administrator", "can_post_messages": True}}[method]

    monkeypatch.setattr(telegram, "bot_api", bot_api)
    return telegram, stores, calls


def test_connect_verify_restart_and_disconnect_never_return_token_or_send(api, connection, caplog):
    telegram, stores, calls = connection
    result = api.post("/api/desktop/telegram/connect", json={"token": TOKEN})
    assert result.status_code == 200, result.text
    assert result.json() == {"connected": True, "bot": {"id": BOT["id"], "username": BOT["username"]},
                             "channels": [], "delivery_enabled": False}
    assert TOKEN.encode() in stores[7].raw
    result = api.post("/api/desktop/telegram/channels/verify", json={"channel": "https://t.me/fixture_channel"})
    assert result.status_code == 200, result.text
    channel = result.json()["channels"][0]
    assert channel["chat_id"] == -1001234567890
    assert channel["title"] == "Fixture channel"
    assert channel["checked_at"]
    assert calls == [("getMe", {}), ("getChat", {"chat_id": "@fixture_channel"}),
                     ("getChatMember", {"chat_id": -1001234567890, "user_id": BOT["id"]})]
    assert api.get("/api/desktop/telegram").json() == result.json()
    assert len(calls) == 3  # Opening settings does not recheck or publish.
    assert TOKEN not in result.text + caplog.text
    assert telegram.status(8)["connected"] is False
    assert api.delete("/api/desktop/telegram").json()["connected"] is False
    assert stores[7].raw is None
    assert api.get("/api/desktop/telegram").json()["channels"] == []


@pytest.mark.parametrize("value", ["https://t.me/+PrivateInvite", "https://t.me/joinchat/secret", "http://t.me/channel",
    "https://user:pass@t.me/channel", "https://t.me:443/channel", "https://t.me/channel?redirect=x",
    "https://t.me/channel#x", "https://t.me/channel/123", "https://t.me.evil.org/channel", "../../license",
    "@name/path", "12345", "-9999999999999999", "@", "", "@" + "a" * 33,
    "https://[broken/channel", "https://t.me/chan\nnel"])
def test_invalid_channel_never_makes_a_network_request(api, connection, value):
    _, _, calls = connection
    response = api.post("/api/desktop/telegram/channels/verify", json={"channel": value})
    assert response.status_code == 422
    assert calls == []


@pytest.mark.parametrize("value,expected", [("@fixture_channel", "@fixture_channel"),
    ("https://t.me/fixture_channel", "@fixture_channel"), (" -1001234567890 ", -1001234567890)])
def test_channel_normalization(value, expected):
    from app.telegram_connection import normalize_channel
    assert normalize_channel(value) == expected


@pytest.mark.parametrize("member", [{"user": BOT, "status": "member"},
    {"user": BOT, "status": "administrator", "can_post_messages": False},
    {"user": BOT, "status": "left"}, {"user": None},
    {"user": {"id": 5}, "status": "administrator", "can_post_messages": True}])
def test_missing_post_permissions_are_not_saved(api, connection, monkeypatch, member):
    telegram, stores, _ = connection
    api.post("/api/desktop/telegram/connect", json={"token": TOKEN})
    old = stores[7].raw
    original = telegram.bot_api
    monkeypatch.setattr(telegram, "bot_api", lambda t, m, p: member if m == "getChatMember" else original(t, m, p))
    response = api.post("/api/desktop/telegram/channels/verify", json={"channel": "-1001234567890"})
    assert response.status_code == 422
    assert response.json()["detail"] == "telegram_post_permission_required"
    assert stores[7].raw == old


def test_private_channel_id_and_repeat_verification_use_stable_id_without_duplicates(api, connection):
    _, _, calls = connection
    api.post("/api/desktop/telegram/connect", json={"token": TOKEN})
    for _ in range(2):
        result = api.post("/api/desktop/telegram/channels/verify", json={"channel": "-1001234567890"})
        assert result.status_code == 200
        assert len(result.json()["channels"]) == 1
    assert calls[-2] == ("getChat", {"chat_id": -1001234567890})


def test_failed_replacement_and_storage_failure_do_not_claim_success(api, connection, monkeypatch):
    telegram, stores, _ = connection
    api.post("/api/desktop/telegram/connect", json={"token": TOKEN})
    old = stores[7].raw

    def reject(*_args):
        raise telegram.TelegramError("telegram_token_rejected", 422)

    with monkeypatch.context() as patch:
        patch.setattr(telegram, "bot_api", reject)
        assert api.post("/api/desktop/telegram/connect", json={"token": TOKEN}).status_code == 422
    assert stores[7].raw == old
    stores[7].fail_save = True
    assert api.post("/api/desktop/telegram/connect", json={"token": TOKEN}).status_code == 503
    assert stores[7].raw == old


@pytest.mark.parametrize("body", [[], {"token": [TOKEN]}, {"token": TOKEN, "extra": TOKEN}, {"token": None}])
def test_malformed_secret_body_is_sanitized(api, body):
    response = api.post("/api/desktop/telegram/connect", json=body)
    assert response.status_code == 422
    assert TOKEN not in response.text


def test_oversized_and_non_json_body_are_sanitized(api):
    assert api.post("/api/desktop/telegram/connect", content=b"{broken").status_code == 422
    assert api.post("/api/desktop/telegram/connect", content=b"x" * 4097).status_code == 413


def test_routes_require_authentication_csrf_and_allowed_origin(api):
    overrides = dict(app.dependency_overrides)
    app.dependency_overrides.clear()
    try:
        for method, path in [("get", ""), ("delete", ""), ("post", "/connect"), ("post", "/channels/verify")]:
            assert getattr(api, method)("/api/desktop/telegram" + path).status_code == 401
    finally:
        app.dependency_overrides.update(overrides)


def test_web_profile_and_unsupported_os_do_not_fall_back_to_plaintext(api, monkeypatch, tmp_path):
    from app import telegram_connection as telegram
    monkeypatch.delenv("SEARCHCAR_DESKTOP_DATA_DIR")
    assert api.get("/api/desktop/telegram").status_code == 404
    monkeypatch.setenv("SEARCHCAR_DESKTOP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(telegram.sys, "platform", "linux")
    assert api.get("/api/desktop/telegram").status_code == 503
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("code,expected", [(401, "telegram_token_rejected"), (403, "telegram_channel_access_denied"),
    (400, "telegram_channel_unavailable"), (429, "telegram_rate_limited"), (302, "telegram_api_unavailable"), (500, "telegram_api_unavailable")])
def test_transport_never_exposes_token_urls_or_remote_error_descriptions(monkeypatch, caplog, code, expected):
    from io import BytesIO
    from urllib.error import HTTPError
    from app import telegram_connection as telegram

    class Opener:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, code, f"server echoed {TOKEN}", {}, BytesIO(TOKEN.encode()))

    monkeypatch.setattr(telegram, "build_opener", lambda *_args: Opener())
    with pytest.raises(telegram.TelegramError) as caught:
        telegram.bot_api(TOKEN, "getChat", {"chat_id": "@fixture"})
    assert str(caught.value) == expected
    assert caught.value.__suppress_context__
    assert TOKEN not in caplog.text + str(caught.value)


def test_transport_uses_https_fixed_methods_bounded_json_and_no_redirects(monkeypatch):
    from io import BytesIO
    from app import telegram_connection as telegram
    replies = [json.dumps({"ok": True, "result": BOT}).encode(), b"x" * 65537, b"{broken", b"[]",
               b'{"ok":false,"description":"secret"}', b'{"ok":true,"result":[]}']

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == f"https://api.telegram.org/bot{TOKEN}/getMe"
            assert request.method == "POST"
            assert timeout == 12
            return BytesIO(replies.pop(0))

    def opener(handler):
        assert handler.redirect_request(None, None, 302, None, {}, "https://evil.invalid") is None
        return Opener()

    monkeypatch.setattr(telegram, "build_opener", opener)
    assert telegram.bot_api(TOKEN, "getMe", {}) == BOT
    for _ in range(5):
        with pytest.raises(telegram.TelegramError, match="telegram_api_unavailable"):
            telegram.bot_api(TOKEN, "getMe", {})
    with pytest.raises(telegram.TelegramError, match="telegram_request_invalid"):
        telegram.bot_api(TOKEN, "sendMessage", {})


def test_broken_http_response_is_sanitized_not_logged_with_token(monkeypatch, caplog):
    from http.client import IncompleteRead
    from app import telegram_connection as telegram

    class Opener:
        def open(self, *_args, **_kwargs):
            raise IncompleteRead(TOKEN.encode())

    monkeypatch.setattr(telegram, "build_opener", lambda *_args: Opener())
    with pytest.raises(telegram.TelegramError, match="telegram_api_unavailable") as caught:
        telegram.bot_api(TOKEN, "getMe", {})
    assert caught.value.__suppress_context__
    assert TOKEN not in caplog.text


def test_keychain_uses_separate_profile_scoped_entry_with_no_license_format(monkeypatch, tmp_path):
    from app import telegram_connection as telegram
    entries, identities = {}, []

    class Keychain:
        def __init__(self, *, service, account):
            assert service == "com.searchcar.desktop.telegram.v1"
            self.identity = (service, account)
            identities.append(self.identity)

        def load(self):
            return entries.get(self.identity)

        def save(self, value):
            entries[self.identity] = value

    monkeypatch.setattr(telegram.sys, "platform", "darwin")
    monkeypatch.setattr(telegram, "MacOSKeychainApi", Keychain)
    first = telegram.TelegramSecretStore(tmp_path, 7)
    first.save(TOKEN.encode())  # Arbitrary-length secret, not a 32-byte signing seed.
    assert telegram.TelegramSecretStore(tmp_path, 7).load() == TOKEN.encode()
    assert telegram.TelegramSecretStore(tmp_path, 8).load() is None
    assert telegram.TelegramSecretStore(tmp_path / "other-profile", 7).load() is None
    assert len(set(identities)) == 3
    first.clear()
    assert first.load() is None
    assert not list(tmp_path.iterdir())


def test_windows_writes_only_ciphertext_uses_separate_entropy_and_erases_on_disconnect(monkeypatch, tmp_path):
    from app import telegram_connection as telegram
    monkeypatch.setattr(telegram.sys, "platform", "win32")
    ciphertext = b"fixture-encrypted-record"
    contexts = []

    def crypt(value, *, protect, context, **kwargs):
        assert b"Telegram connection" in context
        contexts.append(context)
        if protect:
            assert value == TOKEN.encode()
            return ciphertext
        assert value == ciphertext
        return TOKEN.encode()

    monkeypatch.setattr(telegram.WindowsDpapiStore, "_crypt", crypt)
    store = telegram.TelegramSecretStore(tmp_path, 7)
    store.save(TOKEN.encode())
    assert store.path == tmp_path / "secrets" / "telegram-7.dpapi"
    assert store.path.read_bytes() == ciphertext
    assert store.load() == TOKEN.encode()
    assert len(set(contexts)) == 1
    assert telegram.TelegramSecretStore(tmp_path, 8).context != store.context
    store.clear()
    assert not store.path.exists()
    assert store.load() is None


def test_windows_refuses_symlink_secret_directory(monkeypatch, tmp_path):
    from app import telegram_connection as telegram
    monkeypatch.setattr(telegram.sys, "platform", "win32")
    other = tmp_path / "other"
    other.mkdir()
    (tmp_path / "secrets").symlink_to(other, target_is_directory=True)
    store = telegram.TelegramSecretStore(tmp_path, 7)
    with pytest.raises(telegram.TelegramError, match="secure_storage_unavailable"):
        store.save(TOKEN.encode())
    assert not list(other.iterdir())


def test_secret_files_are_excluded_from_backup_and_support_report(tmp_path):
    import zipfile
    from app.database import create_database_engine
    from app.sqlite_migrations import migrate_sqlite
    from app.desktop_backup import export_backup
    from app.desktop_diagnostics import build_support_report
    root = tmp_path / "profile"
    root.mkdir()
    database = root / "data.sqlite3"
    engine = create_database_engine(f"sqlite+pysqlite:///{database}")
    migrate_sqlite(engine)
    engine.dispose()
    for folder in [root / "secrets", root / "storage" / "secrets"]:
        folder.mkdir(parents=True)
        (folder / "telegram-7.dpapi").write_bytes(TOKEN.encode())
    backup = tmp_path / "export.searchcar-backup"
    export_backup(database, root / "storage", backup)
    logs = root / "logs"
    logs.mkdir()
    report = build_support_report(logs, root / "diagnostics", app_version="fixture", hours=1)
    for path in [backup, root / "diagnostics" / report.name]:
        with zipfile.ZipFile(path) as archive:
            assert all("telegram" not in name and "secrets" not in name for name in archive.namelist())
            assert all(TOKEN.encode() not in archive.read(name) for name in archive.namelist())


def test_real_session_requires_csrf_and_allowed_origin(api, connection, tmp_path):
    from fastapi import Request
    from sqlalchemy.orm import sessionmaker
    from app.auth import create_session
    from app.database import Base, create_database_engine, get_db, settings
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'auth.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)
    with factory() as db:
        user = User(id=7, username="fixture", username_key="fixture", password_hash="!fixture")
        db.add(user)
        db.flush()
        token, csrf, _ = create_session(db, user, Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)}))
        db.commit()

    def database():
        with factory() as db:
            yield db

    previous = dict(app.dependency_overrides)
    app.dependency_overrides.clear()
    app.dependency_overrides[get_db] = database
    try:
        api.cookies.set(settings.auth_cookie_name, token)
        for method, path, body in [("post", "/connect", {"token": TOKEN}),
                ("post", "/channels/verify", {"channel": "@fixture"}), ("delete", "", None)]:
            kwargs = {"json": body} if body else {}
            assert getattr(api, method)("/api/desktop/telegram" + path, **kwargs).status_code == 403
            assert getattr(api, method)("/api/desktop/telegram" + path, headers={"x-csrf-token": csrf, "origin": "https://evil.invalid"}, **kwargs).status_code == 403
        assert api.post("/api/desktop/telegram/connect", json={"token": TOKEN}, headers={"x-csrf-token": csrf}).status_code == 200
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        engine.dispose()
