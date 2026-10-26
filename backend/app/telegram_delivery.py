"""Telegram metadata and a durable outbox admission gate, not a sender or relay.

Each app schedules only its own attempts; external load is learned via 429.
There is no fleet-wide admission guarantee or server delivery queue. The shared
token stays in an authenticated relay, never in this DB or the desktop bundle.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import sqlite3
from uuid import uuid4


CHAT_TYPES = {"private", "group", "supergroup", "channel"}
BOT_INTERVAL_MS = 50  # 20/s, below Telegram's approximate free broadcast limit.
CHAT_INTERVAL_MS = 1200
GROUP_INTERVAL_MS = 3200  # Smooth pacing, no batch of20 followed by a burst.
RETRY_MARGIN_MS = 1000


def retry_after_from_response(status_code: int, payload: dict) -> int | None:
    if status_code != 429 or not isinstance(payload, dict) or payload.get("ok") is not False:
        return None
    parameters = payload.get("parameters")
    value = parameters.get("retry_after") if isinstance(parameters, dict) else None
    return max(1, value) if type(value) is int and 0 <= value <= 2**31 else None


def destination_from_chat(chat: dict) -> dict:
    """Parse trusted Bot API getChat/update data, NOT proof of ownership/access.

    The relay must separately bind the requesting account and verify permission.
    An arbitrary private username is not a deliverable private-chat binding.
    """
    if not isinstance(chat, dict):
        raise ValueError("telegram_destination_invalid")
    chat_id, kind = chat.get("id"), chat.get("type")
    if (type(chat_id) is not int or not 0 < abs(chat_id) < 2**52
            or not isinstance(kind, str) or kind not in CHAT_TYPES
            or (chat_id > 0) != (kind == "private")):
        raise ValueError("telegram_destination_invalid")
    if kind == "private":
        names = [chat.get("first_name"), chat.get("last_name")]
        title = " ".join(name for name in names if isinstance(name, str) and name)
    else:
        title = chat.get("title")
    if not isinstance(title, str) or not title.strip():
        title = str(chat_id)
    return {"chat_id": chat_id, "chat_type": kind, "title": title.strip()[:128]}


def _positive(value: int):
    if type(value) is not int or not 0 < value < 2**52:
        raise ValueError("telegram_identifier_invalid")


def _clock(value: int):
    if type(value) is not int or not 0 <= value < 2**52:
        raise ValueError("telegram_clock_invalid")


def _metadata(destination: dict) -> dict:
    if not isinstance(destination, dict) or not {"chat_id", "chat_type"} <= destination.keys():
        raise ValueError("telegram_destination_invalid")
    return destination_from_chat({"id": destination["chat_id"], "type": destination["chat_type"],
                                  "title": destination.get("title"), "first_name": destination.get("title")})


class TelegramOutbox:
    """Durable local references + admission state, outside portable backups.

    No thread, timer, HTTP call or implicit send is started by this class.
    Every outgoing attempt (including a photo-to-text fallback) needs a claim.
    """
    def __init__(self, path: Path):
        self.path = Path(path)
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise ValueError("telegram_outbox_path_invalid")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(descriptor)
        with self._db() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in {0, 1}:
                raise ValueError("telegram_outbox_version_unsupported")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS destinations (
                    bot_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
                    chat_type TEXT NOT NULL, title TEXT NOT NULL,
                    PRIMARY KEY(bot_id, chat_id));
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL,
                    bot_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
                    notification_key TEXT NOT NULL, state TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL, not_before_ms INTEGER NOT NULL,
                    attempt_id TEXT, message_id INTEGER, error_code TEXT,
                    UNIQUE(owner_id, bot_id, chat_id, notification_key),
                    FOREIGN KEY(bot_id, chat_id) REFERENCES destinations(bot_id, chat_id));
                CREATE TABLE IF NOT EXISTS projects (
                    job_id TEXT NOT NULL REFERENCES jobs(id), project_id INTEGER NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(job_id, project_id));
                CREATE TABLE IF NOT EXISTS limits (
                    bot_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
                    next_at_ms INTEGER NOT NULL, PRIMARY KEY(bot_id, chat_id));
                CREATE TABLE IF NOT EXISTS owner_turns (
                    bot_id INTEGER NOT NULL, owner_id INTEGER NOT NULL,
                    last_at_ms INTEGER NOT NULL, PRIMARY KEY(bot_id, owner_id));
                CREATE TABLE IF NOT EXISTS project_turns (
                    bot_id INTEGER NOT NULL, owner_id INTEGER NOT NULL, project_id INTEGER NOT NULL,
                    last_at_ms INTEGER NOT NULL, PRIMARY KEY(bot_id, owner_id, project_id));
                CREATE TABLE IF NOT EXISTS project_recipients (
                    owner_id INTEGER NOT NULL, project_id INTEGER NOT NULL, bot_id INTEGER NOT NULL,
                    binding_id TEXT NOT NULL, chat_id INTEGER NOT NULL, chat_type TEXT NOT NULL,
                    title TEXT NOT NULL, verified_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(owner_id, project_id));
                CREATE INDEX IF NOT EXISTS ix_telegram_ready ON jobs(state, not_before_ms);
                PRAGMA user_version=1;
            """)

    def configure_project(self, *, owner_id: int, project_id: int, bot_id: int,
                          binding_id: str, destination: dict, now_ms: int) -> dict:
        """Persist a relay-confirmed recipient, never a user-selected type hint.

        binding_id is a reference, not a bearer credential. The relay still has
        to authenticate the device and authorize this binding at every send.
        """
        for value in (owner_id, project_id, bot_id):
            _positive(value)
        _clock(now_ms)
        if not isinstance(binding_id, str) or not re.fullmatch(r"[a-f0-9]{32}", binding_id):
            raise ValueError("telegram_binding_invalid")
        checked = _metadata(destination)
        with self._db() as db:
            previous = db.execute("SELECT * FROM project_recipients WHERE owner_id=? AND project_id=?",
                                  (owner_id, project_id)).fetchone()
            if previous and any(previous[key] != value for key, value in {
                    "bot_id": bot_id, "binding_id": binding_id,
                    "chat_id": checked["chat_id"], "chat_type": checked["chat_type"]}.items()):
                active = db.execute("""SELECT 1 FROM projects p JOIN jobs j ON j.id=p.job_id
                    WHERE j.owner_id=? AND p.project_id=? AND p.active=1 AND j.state='IN_FLIGHT' LIMIT 1""",
                    (owner_id, project_id)).fetchone()
                if active:
                    raise ValueError("telegram_recipient_busy")
                queued = db.execute("""SELECT j.id FROM projects p JOIN jobs j ON j.id=p.job_id
                    WHERE j.owner_id=? AND p.project_id=? AND p.active=1 AND j.state='QUEUED'""",
                    (owner_id, project_id)).fetchall()
                for row in queued:
                    db.execute("UPDATE projects SET active=0 WHERE job_id=? AND project_id=?", (row[0], project_id))
                    if not db.execute("SELECT 1 FROM projects WHERE job_id=? AND active=1", (row[0],)).fetchone():
                        db.execute("UPDATE jobs SET state='CANCELLED',error_code='RECIPIENT_CHANGED' WHERE id=?", (row[0],))
            db.execute("""INSERT INTO project_recipients VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(owner_id,project_id) DO UPDATE SET
                bot_id=excluded.bot_id,binding_id=excluded.binding_id,chat_id=excluded.chat_id,
                chat_type=excluded.chat_type,title=excluded.title,verified_at_ms=excluded.verified_at_ms""",
                (owner_id, project_id, bot_id, binding_id, checked["chat_id"], checked["chat_type"], checked["title"], now_ms))
            return dict(db.execute("SELECT * FROM project_recipients WHERE owner_id=? AND project_id=?",
                                   (owner_id, project_id)).fetchone())

    def project_recipient(self, *, owner_id: int, project_id: int) -> dict | None:
        _positive(owner_id)
        _positive(project_id)
        with self._db() as db:
            row = db.execute("SELECT * FROM project_recipients WHERE owner_id=? AND project_id=?",
                             (owner_id, project_id)).fetchone()
            return dict(row) if row else None

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _job(db, job_id: str):
        row = db.execute("""SELECT j.*, d.chat_type, d.title FROM jobs j JOIN destinations d
            ON d.bot_id=j.bot_id AND d.chat_id=j.chat_id WHERE j.id=?""", (job_id,)).fetchone()
        if row is None:
            return None
        value = dict(row)
        projects = db.execute("SELECT project_id,active FROM projects WHERE job_id=? ORDER BY project_id", (job_id,)).fetchall()
        value["project_ids"] = [r[0] for r in projects]
        value["active_project_ids"] = [r[0] for r in projects if r[1]]
        return value

    def enqueue(self, *, owner_id: int, project_id: int, bot_id: int,
                destination: dict, notification_key: str, now_ms: int, _connection=None) -> dict:
        for value in (owner_id, project_id, bot_id):
            _positive(value)
        _clock(now_ms)
        if not isinstance(notification_key, str) or not re.fullmatch(r"[A-Za-z0-9_:/~.\-]{1,256}", notification_key):
            raise ValueError("telegram_notification_key_invalid")
        # Caller must have a verified relay binding; this is not a public endpoint.
        checked = _metadata(destination)
        if _connection is not None:
            return self._enqueue(_connection, owner_id, project_id, bot_id, checked, notification_key, now_ms)
        with self._db() as db:
            return self._enqueue(db, owner_id, project_id, bot_id, checked, notification_key, now_ms)

    def _enqueue(self, db, owner_id, project_id, bot_id, checked, notification_key, now_ms):
        chat_id, kind = checked["chat_id"], checked["chat_type"]
        configured = db.execute("SELECT bot_id,chat_id,chat_type FROM project_recipients WHERE owner_id=? AND project_id=?",
                                    (owner_id, project_id)).fetchone()
        if configured and tuple(configured) != (bot_id, chat_id, kind):
            raise ValueError("telegram_recipient_changed")
        db.execute("INSERT OR IGNORE INTO destinations VALUES(?,?,?,?)", (bot_id, chat_id, kind, checked["title"]))
        saved = db.execute("SELECT chat_type FROM destinations WHERE bot_id=? AND chat_id=?", (bot_id, chat_id)).fetchone()
        if saved[0] != kind:
            raise ValueError("telegram_destination_type_conflict")
        db.execute("UPDATE destinations SET title=? WHERE bot_id=? AND chat_id=?", (checked["title"], bot_id, chat_id))
        job_id = uuid4().hex
        db.execute("""INSERT OR IGNORE INTO jobs
                (id,owner_id,bot_id,chat_id,notification_key,state,created_at_ms,not_before_ms)
                VALUES(?,?,?,?,?,'QUEUED',?,?)""", (job_id, owner_id, bot_id, chat_id, notification_key, now_ms, now_ms))
        job_id = db.execute("SELECT id FROM jobs WHERE owner_id=? AND bot_id=? AND chat_id=? AND notification_key=?",
                                (owner_id, bot_id, chat_id, notification_key)).fetchone()[0]
        # Only a fresh, explicitly enqueued intent can revive an unsent
        # recipient-change cancellation; SENT/UNKNOWN are never reset.
        db.execute("""UPDATE jobs SET state='QUEUED',error_code=NULL,not_before_ms=max(not_before_ms,?)
                WHERE id=? AND state='CANCELLED' AND error_code='RECIPIENT_CHANGED'""", (now_ms, job_id))
        db.execute("""INSERT INTO projects(job_id,project_id) VALUES(?,?) ON CONFLICT(job_id,project_id)
                DO UPDATE SET active=1""", (job_id, project_id))
        return self._job(db, job_id)

    @staticmethod
    def _waiting(db):
        return db.execute("""SELECT j.id, j.bot_id, j.chat_id, d.chat_type,
                max(j.not_before_ms,coalesce(b.next_at_ms,0),coalesce(c.next_at_ms,0)) AS due_at,
                coalesce(o.last_at_ms,-1) AS owner_turn,
                (SELECT min(coalesce(t.last_at_ms,-1)) FROM projects p LEFT JOIN project_turns t
                    ON t.bot_id=j.bot_id AND t.owner_id=j.owner_id AND t.project_id=p.project_id
                    WHERE p.job_id=j.id AND p.active=1) AS project_turn, j.created_at_ms
            FROM jobs j JOIN destinations d ON d.bot_id=j.bot_id AND d.chat_id=j.chat_id
            LEFT JOIN limits b ON b.bot_id=j.bot_id AND b.chat_id=0
            LEFT JOIN limits c ON c.bot_id=j.bot_id AND c.chat_id=j.chat_id
            LEFT JOIN owner_turns o ON o.bot_id=j.bot_id AND o.owner_id=j.owner_id
            WHERE j.state='QUEUED' AND EXISTS (SELECT 1 FROM projects p WHERE p.job_id=j.id AND p.active=1)
            ORDER BY owner_turn,project_turn,j.created_at_ms,j.id""").fetchall()

    @staticmethod
    def _limit(db, bot_id: int, chat_id: int, until: int):
        db.execute("""INSERT INTO limits VALUES(?,?,?) ON CONFLICT(bot_id,chat_id)
            DO UPDATE SET next_at_ms=max(next_at_ms,excluded.next_at_ms)""", (bot_id, chat_id, until))

    def claim_ready(self, now_ms: int) -> dict | None:
        _clock(now_ms)
        with self._db() as db:
            row = next((r for r in self._waiting(db) if r["due_at"] <= now_ms), None)
            if row is None:
                return None
            job_id, bot_id, chat_id = row["id"], row["bot_id"], row["chat_id"]
            interval = GROUP_INTERVAL_MS if row["chat_type"] in {"group", "supergroup"} else CHAT_INTERVAL_MS
            self._limit(db, bot_id, 0, now_ms + BOT_INTERVAL_MS)
            self._limit(db, bot_id, chat_id, now_ms + interval)
            db.execute("UPDATE jobs SET state='IN_FLIGHT',attempt_id=?,error_code=NULL WHERE id=?", (uuid4().hex, job_id))
            job = self._job(db, job_id)
            db.execute("""INSERT INTO owner_turns VALUES(?,?,?) ON CONFLICT(bot_id,owner_id)
                DO UPDATE SET last_at_ms=excluded.last_at_ms""", (bot_id, job["owner_id"], now_ms))
            for project_id in job['active_project_ids']:
                db.execute('''INSERT INTO project_turns VALUES(?,?,?,?) ON CONFLICT(bot_id,owner_id,project_id)
                    DO UPDATE SET last_at_ms=excluded.last_at_ms''', (bot_id, job['owner_id'], project_id, now_ms))
            return job

    def next_wake_ms(self, now_ms: int) -> int | None:
        _clock(now_ms)
        with self._db() as db:
            rows = self._waiting(db)
            return max(now_ms, min(row["due_at"] for row in rows)) if rows else None

    @staticmethod
    def _attempt(db, job_id: str, attempt_id: str):
        job = TelegramOutbox._job(db, job_id)
        if job is None or job["state"] != "IN_FLIGHT" or job["attempt_id"] != attempt_id:
            raise ValueError("telegram_attempt_not_current")
        return job

    def rate_limited(self, job_id: str, attempt_id: str, *, retry_after: int, now_ms: int, error_code='TELEGRAM_RATE_LIMIT'):
        _clock(now_ms)
        if type(retry_after) is not int or not 0 < retry_after <= 2**31:
            raise ValueError("telegram_retry_after_invalid")
        if error_code not in {'TELEGRAM_RATE_LIMIT','TELEGRAM_CONNECTION_UNAVAILABLE'}:
            raise ValueError('telegram_error_code_invalid')
        until = now_ms + retry_after * 1000 + RETRY_MARGIN_MS
        with self._db() as db:
            job = self._attempt(db, job_id, attempt_id)
            # Telegram doesn't identify the flood-control scope. Conservatively
            # pause this bot, not just this project, and never shorten a pause.
            self._limit(db, job["bot_id"], 0, until)
            db.execute("""UPDATE jobs SET state='QUEUED',not_before_ms=max(not_before_ms,?),
                attempt_id=NULL,error_code=? WHERE id=?""", (until, error_code, job_id))

    def sent(self, job_id: str, attempt_id: str, *, message_id: int):
        _positive(message_id)
        with self._db() as db:
            self._attempt(db, job_id, attempt_id)
            db.execute("UPDATE jobs SET state='SENT',message_id=?,attempt_id=NULL WHERE id=?", (message_id, job_id))

    def uncertain(self, job_id: str, attempt_id: str):
        # A lost response or interrupted send isn't permission to publish twice.
        with self._db() as db:
            self._attempt(db, job_id, attempt_id)
            db.execute("""UPDATE jobs SET state='UNKNOWN',attempt_id=NULL,
                error_code='TELEGRAM_RESULT_UNKNOWN' WHERE id=?""", (job_id,))

    def get(self, job_id: str, *, owner_id: int) -> dict | None:
        _positive(owner_id)
        with self._db() as db:
            job = self._job(db, job_id)
            return job if job and job["owner_id"] == owner_id else None
