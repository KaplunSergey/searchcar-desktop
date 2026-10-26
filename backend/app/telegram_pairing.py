"""Native recipient pairing. No bot token, sender or implicit polling."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import RLock
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi import HTTPException, Request

from .desktop_license import _license_paths, _read_json, LicenseStateError
from .desktop_license_client import load_or_create_device_identity, LicenseClientError
from .telegram_connection import TelegramError
from .telegram_delivery import TelegramOutbox, destination_from_chat


LOCK = RLock()
ID = re.compile(r"[a-f0-9]{32}\Z")
UUID = re.compile(r"[a-f0-9-]{36}\Z")
STATES = {"WAITING_START", "AWAITING_CONFIRMATION", "CONNECTED", "EXPIRED", "REVOKED"}


def configuration():
    url = os.environ.get("SEARCHCAR_TELEGRAM_RELAY_URL", "").strip()
    if not url:
        return None, "telegram_relay_not_configured"
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or any(ord(c) < 33 or ord(c) == 127 for c in url)):
            raise ValueError()
    except ValueError:
        return None, "telegram_relay_invalid"
    root = os.environ.get("SEARCHCAR_DESKTOP_DATA_DIR")
    if not root:
        return None, "desktop_runtime_required"
    try:
        root = Path(root).expanduser().resolve()
        binding = _read_json(_license_paths(root)[0])
        if type(binding.get("protocol_version")) is not int or binding["protocol_version"] != 1 or any(
                not isinstance(binding.get(key), str) or not UUID.fullmatch(binding[key]) for key in ("license_id", "device_id")):
            raise ValueError()
    except (OSError, ValueError, LicenseStateError):
        return None, "telegram_device_binding_required"
    binding = {key: binding[key] for key in ("protocol_version", "license_id", "device_id")}
    return (root, url.rstrip("/"), binding), None


def _integer(value):
    return type(value) is int and 0 < value < 2**52


def checked_response(data, project_id, *, starting=False):
    try:
        if (not isinstance(data, dict) or not ID.fullmatch(data.get("pairing_id", ""))
                or type(data.get("project_id")) is not int or data["project_id"] != project_id
                or not _integer(data.get("bot_id")) or data.get("status") not in STATES
                or type(data.get("expires_at_ms")) is not int or not 0 <= data["expires_at_ms"] < 2**52):
            raise ValueError()
        status = data["status"]
        kind = data.get("target_kind", "private")
        if kind not in {"private", "group", "channel"} or (kind != "private" and status not in {"REVOKED", "EXPIRED"}
                and os.environ.get("SEARCHCAR_TELEGRAM_CHAT_PAIRING_ENABLED") != "1"):
            raise ValueError()
        recipient = None
        if status in {"CONNECTED", "AWAITING_CONFIRMATION"}:
            raw = data.get("recipient")
            types = {"private": {"private"}, "group": {"group", "supergroup"}, "channel": {"channel"}}
            if not isinstance(raw, dict) or raw.get("chat_type") not in types[kind]:
                raise ValueError()
            recipient = destination_from_chat({"id": raw.get("chat_id"), "type": raw["chat_type"],
                "first_name": raw.get("title"), "title": raw.get("title")})
        elif data.get("recipient") is not None:
            raise ValueError()
        binding = data.get("binding_id")
        if (status == "CONNECTED" and (not isinstance(binding, str) or not ID.fullmatch(binding))
                or status != "CONNECTED" and binding is not None):
            raise ValueError()
        result = {key: data[key] for key in ("pairing_id", "project_id", "bot_id", "status", "expires_at_ms")}
        result.update(recipient=recipient, binding_id=binding, target_kind=kind)
        if starting:
            target = data.get("start_url")
            if not isinstance(target, str) or len(target) > 160:
                raise ValueError()
            url = urlsplit(target)
            query = parse_qs(url.query, keep_blank_values=True)
            if (url.scheme != "https" or url.netloc != "t.me" or url.path != "/search_car_app_bot"
                    or url.fragment or set(query) != {"start"} or len(query["start"]) != 1
                    or not re.fullmatch(r"p_[A-Za-z0-9_-]{32}", query["start"][0]) or status != "WAITING_START"):
                raise ValueError()
            result["start_url"] = target
        return result
    except (ValueError, TypeError):
        raise TelegramError("telegram_pairing_response_invalid", 502) from None


def relay_call(config, owner_id, project_id, action, **extra):
    root, url, binding = config
    try:
        identity = load_or_create_device_identity(root)
        signed = identity.signed_request({**binding, "audience": "searchcar-telegram-pairing-v1",
            "telegram": {"action": action, "owner_id": owner_id, "project_id": project_id, **extra}})
        # One attempt only. Ambiguous confirmation is recovered by a later status read.
        with httpx.Client(timeout=httpx.Timeout(12, connect=5), follow_redirects=False, trust_env=False) as client:
            with client.stream("POST", url + "/v1/pairings", json=signed) as response:
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 16384:
                        raise TelegramError("telegram_pairing_response_invalid", 502)
                status = response.status_code
        envelope = json.loads(raw)
    except LicenseClientError:
        raise TelegramError("telegram_device_unavailable") from None
    except (httpx.HTTPError, OSError, ValueError):
        raise TelegramError("telegram_pairing_unavailable") from None
    if status != 200 or not isinstance(envelope, dict) or envelope.get("ok") is not True:
        code = (envelope.get("error") or {}).get("code") if isinstance(envelope, dict) and isinstance(envelope.get("error"), dict) else None
        if code == "DEVICE_NOT_AUTHORIZED":
            raise TelegramError("telegram_device_not_authorized", 403)
        if code in {"CHAT_PAIRING_DISABLED", "CHAT_PERMISSION_REQUIRED", "CHAT_VERIFICATION_UNAVAILABLE"}:
            raise TelegramError({"CHAT_PAIRING_DISABLED": "telegram_chat_pairing_disabled",
                "CHAT_PERMISSION_REQUIRED": "telegram_chat_permission_required",
                "CHAT_VERIFICATION_UNAVAILABLE": "telegram_chat_verification_unavailable"}[code], 403 if status == 403 else 503)
        if status == 429:
            raise TelegramError("telegram_pairing_rate_limited", 429)
        if code in {"REQUEST_REPLAYED", "PAIRING_CHANGED", "PAIRING_NOT_CONFIRMABLE", "PAIRING_NOT_FOUND"}:
            raise TelegramError("telegram_pairing_changed", 409)
        raise TelegramError("telegram_pairing_unavailable")
    result = checked_response(envelope.get("data"), project_id, starting=action == "start")
    if action == "start" and result["target_kind"] != extra.get("target_kind", "private"):
        raise TelegramError("telegram_pairing_response_invalid", 502)
    return result


class PairingSession:
    def __init__(self, config, owner_id, project_id, project_scope):
        self.config, self.owner, self.project = config, owner_id, project_id
        root, url, binding = config
        self.device_scope = hashlib.sha256(json.dumps([url, binding], sort_keys=True).encode()).hexdigest()
        self.scope = hashlib.sha256(json.dumps([url, binding, project_scope], sort_keys=True).encode()).hexdigest()
        self.box = TelegramOutbox(root / "notifications" / "telegram-outbox.sqlite3")
        with self.box._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS pairing_contexts (
                owner_id INTEGER,project_id INTEGER,scope TEXT,pairing_id TEXT,confirmed_id TEXT,snapshot TEXT,
                PRIMARY KEY(owner_id,project_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS saved_recipients (
                id TEXT PRIMARY KEY,owner_id INTEGER,scope TEXT,bot_id INTEGER,chat_id INTEGER,
                chat_type TEXT,title TEXT,pairing_id TEXT,verified_at_ms INTEGER,
                UNIQUE(owner_id,scope,bot_id,chat_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS recipient_reuses (
                owner_id INTEGER,project_id INTEGER,scope TEXT,recipient_id TEXT,pairing_id TEXT,source_pairing_id TEXT,
                PRIMARY KEY(owner_id,project_id))""")

    def remember(self, recipient, pairing_id, *, confirmed_now=False):
        with self.box._db() as db:
            db.execute("""INSERT INTO saved_recipients VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(owner_id,scope,bot_id,chat_id) DO UPDATE SET title=excluded.title,
                chat_type=excluded.chat_type,pairing_id=excluded.pairing_id,verified_at_ms=excluded.verified_at_ms
                WHERE ? OR excluded.verified_at_ms>saved_recipients.verified_at_ms""",
                (uuid.uuid4().hex, self.owner, self.device_scope, recipient['bot_id'], recipient['chat_id'],
                 recipient['chat_type'], recipient['title'], pairing_id, recipient['verified_at_ms'], confirmed_now))

    def context(self):
        with self.box._db() as db:
            row = db.execute("SELECT * FROM pairing_contexts WHERE owner_id=? AND project_id=? AND scope=?",
                             (self.owner, self.project, self.scope)).fetchone()
            return dict(row) if row else None

    def public(self):
        row = self.context()
        return dict(available=True, issue=None, delivery_enabled=os.environ.get('SEARCHCAR_TELEGRAM_DELIVERY_ENABLED') == '1',
                    chat_pairing_enabled=os.environ.get('SEARCHCAR_TELEGRAM_CHAT_PAIRING_ENABLED') == '1',
                    recipient=self.box.project_recipient(owner_id=self.owner, project_id=self.project) if row and row["confirmed_id"] else None,
                    session=json.loads(row["snapshot"]) if row and row["snapshot"] else None)

    def save(self, value):
        row = self.context()
        confirmed = row["confirmed_id"] if row else None
        if value["status"] == "CONNECTED":
            self.box.configure_project(owner_id=self.owner, project_id=self.project, bot_id=value["bot_id"],
                binding_id=value["binding_id"], destination=value["recipient"], now_ms=int(time.time() * 1000))
            confirmed = value["pairing_id"]
            self.remember(self.box.project_recipient(owner_id=self.owner, project_id=self.project), confirmed, confirmed_now=True)
        elif value["status"] == "REVOKED" and value["pairing_id"] == confirmed:
            self.clear_recipient()
            confirmed = None
        snapshot = {key: item for key, item in value.items() if key != "start_url"}
        with self.box._db() as db:
            db.execute("INSERT OR REPLACE INTO pairing_contexts VALUES(?,?,?,?,?,?)",
                (self.owner, self.project, self.scope, value["pairing_id"], confirmed, json.dumps(snapshot)))
        result = self.public()
        # Only this immediate response has the transient deep link; never stored.
        if "start_url" in value:
            result["session"]["start_url"] = value["start_url"]
        return result

    def clear_recipient(self):
        with self.box._db() as db:
            db.execute("""UPDATE projects SET active=0 WHERE project_id=? AND job_id IN
                (SELECT id FROM jobs WHERE owner_id=? AND state='QUEUED')""", (self.project, self.owner))
            db.execute("""UPDATE jobs SET state='CANCELLED',error_code='RECIPIENT_CHANGED' WHERE owner_id=?
                AND state='QUEUED' AND NOT EXISTS(SELECT 1 FROM projects WHERE projects.job_id=jobs.id AND active=1)""", (self.owner,))
            db.execute("DELETE FROM project_recipients WHERE owner_id=? AND project_id=?", (self.owner, self.project))

    def change(self, body):
        action = body["action"]
        row = self.context()
        if action in {"CONFIRM", "USE_SAVED"}:
            with self.box._db() as db:
                if db.execute("""SELECT 1 FROM projects p JOIN jobs j ON j.id=p.job_id WHERE p.project_id=?
                    AND j.owner_id=? AND p.active=1 AND j.state='IN_FLIGHT'""", (self.project, self.owner)).fetchone():
                    raise TelegramError("telegram_recipient_busy", 409)
        if action == "USE_SAVED":
            with self.box._db() as db:
                saved = db.execute("SELECT * FROM saved_recipients WHERE id=? AND owner_id=? AND scope=?",
                    (body['recipient_id'], self.owner, self.device_scope)).fetchone()
                if not saved:
                    raise TelegramError("telegram_pairing_changed", 409)
                if saved['chat_type'] != 'private' and os.environ.get('SEARCHCAR_TELEGRAM_CHAT_PAIRING_ENABLED') != '1':
                    raise TelegramError("telegram_chat_pairing_disabled")
                pending = db.execute("SELECT * FROM recipient_reuses WHERE owner_id=? AND project_id=? AND scope=? AND recipient_id=?",
                    (self.owner, self.project, self.scope, saved['id'])).fetchone()
                pair_id = pending['pairing_id'] if pending else uuid.uuid4().hex
                source_id = pending['source_pairing_id'] if pending else saved['pairing_id']
                db.execute("INSERT OR REPLACE INTO recipient_reuses VALUES(?,?,?,?,?,?)",
                    (self.owner, self.project, self.scope, saved['id'], pair_id, source_id))
            try:
                value = relay_call(self.config, self.owner, self.project, "reuse", pairing_id=pair_id, source_pairing_id=source_id)
            except TelegramError as error:
                if error.code == 'telegram_pairing_changed':
                    with self.box._db() as db:
                        db.execute("DELETE FROM recipient_reuses WHERE owner_id=? AND project_id=? AND pairing_id=?",
                            (self.owner, self.project, pair_id))
                raise
            if (value['status'] != 'CONNECTED' or value['pairing_id'] != pair_id or value['bot_id'] != saved['bot_id']
                    or value['recipient']['chat_id'] != saved['chat_id'] or value['recipient']['chat_type'] != saved['chat_type']):
                raise TelegramError("telegram_pairing_response_invalid", 502)
            result = self.save(value)
            with self.box._db() as db:
                db.execute("DELETE FROM recipient_reuses WHERE owner_id=? AND project_id=?", (self.owner, self.project))
            return result
        if action == "START":
            kind = body.get("target_kind", "private")
            if kind != "private" and os.environ.get('SEARCHCAR_TELEGRAM_CHAT_PAIRING_ENABLED') != '1':
                raise TelegramError("telegram_chat_pairing_disabled")
            with self.box._db() as db:
                db.execute("DELETE FROM recipient_reuses WHERE owner_id=? AND project_id=?", (self.owner, self.project))
            return self.save(relay_call(self.config, self.owner, self.project, "start", **({"target_kind": kind} if kind != "private" else {})))
        if not row or not row["pairing_id"]:
            raise TelegramError("telegram_pairing_changed", 409)
        if body.get("pairing_id", row["pairing_id"]) != row["pairing_id"]:
            raise TelegramError("telegram_pairing_changed", 409)
        if action == "DISCONNECT":
            recipient = self.box.project_recipient(owner_id=self.owner, project_id=self.project)
            if not recipient or body.get("binding_id") != recipient["binding_id"]:
                raise TelegramError("telegram_pairing_changed", 409)
            for pair_id in dict.fromkeys([row["confirmed_id"], row["pairing_id"]]):
                if pair_id:
                    result = relay_call(self.config, self.owner, self.project, "revoke", pairing_id=pair_id)
                    if result["status"] != "REVOKED" or result["pairing_id"] != pair_id:
                        raise TelegramError("telegram_pairing_response_invalid", 502)
            self.clear_recipient()
            with self.box._db() as db:
                db.execute("DELETE FROM pairing_contexts WHERE owner_id=? AND project_id=?", (self.owner, self.project))
            return self.public()
        if action == "CANCEL" and row["pairing_id"] == row["confirmed_id"]:
            raise TelegramError("telegram_pairing_changed", 409)
        previous = json.loads(row["snapshot"]) if row["snapshot"] else None
        if action in {"CHECK", "CONFIRM"} and previous and previous.get("target_kind", "private") != "private" and os.environ.get('SEARCHCAR_TELEGRAM_CHAT_PAIRING_ENABLED') != '1':
            raise TelegramError("telegram_chat_pairing_disabled")
        extra = {"pairing_id": row["pairing_id"]}
        if action == "CONFIRM":
            extra["chat_id"] = body["chat_id"]
        value = relay_call(self.config, self.owner, self.project,
                           {"CHECK": "status", "CONFIRM": "confirm", "CANCEL": "revoke"}[action], **extra)
        if value["pairing_id"] != row["pairing_id"]:
            raise TelegramError("telegram_pairing_response_invalid", 502)
        if previous and (value["bot_id"] != previous["bot_id"] or value["target_kind"] != previous.get("target_kind", "private")):
            raise TelegramError("telegram_pairing_response_invalid", 502)
        if action == "CONFIRM" and (value["status"] != "CONNECTED" or value["recipient"]["chat_id"] != body["chat_id"]):
            raise TelegramError("telegram_pairing_response_invalid", 502)
        if action == "CANCEL" and value["status"] != "REVOKED":
            raise TelegramError("telegram_pairing_response_invalid", 502)
        result = self.save(value)
        if action == "CANCEL":
            with self.box._db() as db:
                db.execute("UPDATE pairing_contexts SET pairing_id=confirmed_id,snapshot=NULL WHERE owner_id=? AND project_id=?",
                           (self.owner, self.project))
            result = self.public()
        return result


def project_status(owner_id, project_id, project_scope):
    config, issue = configuration()
    if issue:
        return dict(available=False, issue=issue, recipient=None, session=None, delivery_enabled=False)
    with LOCK:
        return PairingSession(config, owner_id, project_id, project_scope).public()


def change_project(owner_id, project_id, project_scope, body):
    config, issue = configuration()
    if issue:
        raise TelegramError(issue)
    with LOCK:
        try:
            return PairingSession(config, owner_id, project_id, project_scope).change(body)
        except (OSError, ValueError, sqlite3.Error):
            raise TelegramError("telegram_pairing_storage_failed") from None


def saved_recipients(owner_id, projects):
    config, issue = configuration()
    if issue:
        return dict(available=False, issue=issue, recipients=[])
    with LOCK:
        catalogue = PairingSession(config, owner_id, 0, [])
        # Import earlier confirmed project bindings, but never metadata from another device/profile.
        for project_id, project_scope in projects:
            session = PairingSession(config, owner_id, project_id, project_scope)
            row = session.context()
            recipient = session.public()['recipient']
            if row and row['confirmed_id'] and recipient:
                session.remember(recipient, row['confirmed_id'])
        with catalogue.box._db() as db:
            rows = db.execute("""SELECT id,bot_id,chat_id,chat_type,title,verified_at_ms FROM saved_recipients
                WHERE owner_id=? AND scope=? ORDER BY verified_at_ms DESC,id""", (owner_id, catalogue.device_scope)).fetchall()
        return dict(available=True, issue=None, recipients=[dict(row) for row in rows])


async def pairing_body(request: Request):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 4096:
            raise HTTPException(413, "telegram_request_too_large")
    try:
        body = json.loads(raw)
        allowed = {"START": {"action", "target_kind"}, "CHECK": {"action", "pairing_id"},
            "USE_SAVED": {"action", "recipient_id"},
            "CONFIRM": {"action", "pairing_id", "chat_id"}, "CANCEL": {"action", "pairing_id"},
            "DISCONNECT": {"action", "pairing_id", "binding_id"}}
        action = body.get("action") if isinstance(body, dict) else None
        if not isinstance(action, str) or action not in allowed or not set(body) <= allowed[action]:
            raise ValueError()
        if action == "START" and (not isinstance(body.get("target_kind", "private"), str) or body.get("target_kind", "private") not in {"private", "group", "channel"}):
            raise ValueError()
        if any(not isinstance(body[key], str) or not ID.fullmatch(body[key]) for key in ("pairing_id", "binding_id", "recipient_id") if key in body):
            raise ValueError()
        if (action == "CONFIRM" and (type(body.get("chat_id")) is not int or not 0 < abs(body["chat_id"]) < 2**52 or "pairing_id" not in body)
                or action == "CANCEL" and "pairing_id" not in body or action == "DISCONNECT" and "binding_id" not in body
                or action == "USE_SAVED" and "recipient_id" not in body):
            raise ValueError()
        return body
    except (ValueError, TypeError):
        raise HTTPException(422, "telegram_request_invalid") from None
