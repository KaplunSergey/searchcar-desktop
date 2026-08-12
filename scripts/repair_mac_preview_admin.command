#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
repo_dir="$(cd "${script_dir}/.." && pwd)"
database_path="${SEARCHCAR_REPAIR_DB:-${HOME}/Library/Application Support/com.searchcar.desktop/data/searchcar.sqlite3}"
argon_library="${repo_dir}/work/nuitka/aarch64-apple-darwin/standalone/searchcar_core.dist/_argon2_cffi_bindings/_ffi.so"

finish() {
  local status="$?"
  if [[ -t 0 ]]; then
    echo
    read -r -p "Press Enter to close..." _
  fi
  exit "${status}"
}
trap finish EXIT

if [[ ! -f "${database_path}" ]]; then
  echo "SearchCar database was not found: ${database_path}" >&2
  exit 1
fi

if [[ ! -f "${argon_library}" ]]; then
  echo "The bundled Argon2 library was not found: ${argon_library}" >&2
  exit 1
fi

if lsof "${database_path}" >/dev/null 2>&1; then
  echo "SearchCar is still running. Quit every SearchCar process and run this file again." >&2
  lsof "${database_path}" >&2 || true
  exit 1
fi

SEARCHCAR_REPAIR_DB="${database_path}" \
SEARCHCAR_ARGON_LIBRARY="${argon_library}" \
/usr/bin/python3 <<'PY'
import ctypes
import datetime as dt
import os
import secrets
import shutil
import sqlite3
from pathlib import Path

database_path = Path(os.environ["SEARCHCAR_REPAIR_DB"]).expanduser().resolve()
library_path = Path(os.environ["SEARCHCAR_ARGON_LIBRARY"]).resolve()
timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup_path = database_path.with_name(f"{database_path.name}.before-admin-repair-{timestamp}")

source = sqlite3.connect(database_path, timeout=10)
backup = sqlite3.connect(backup_path)
try:
    source.backup(backup)
finally:
    backup.close()
    source.close()

argon = ctypes.CDLL(str(library_path))
argon.argon2_encodedlen.argtypes = [
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_int,
]
argon.argon2_encodedlen.restype = ctypes.c_size_t
argon.argon2_hash.argtypes = [
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_char_p,
    ctypes.c_size_t,
    ctypes.c_int,
    ctypes.c_uint32,
]
argon.argon2_hash.restype = ctypes.c_int
argon.argon2_verify.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
argon.argon2_verify.restype = ctypes.c_int

password = b"sergiokap09"
salt = secrets.token_bytes(16)
hash_output = ctypes.create_string_buffer(32)
encoded_length = argon.argon2_encodedlen(3, 65536, 4, len(salt), 32, 2)
encoded = ctypes.create_string_buffer(encoded_length)
result = argon.argon2_hash(
    3,
    65536,
    4,
    password,
    len(password),
    salt,
    len(salt),
    hash_output,
    32,
    encoded,
    encoded_length,
    2,
    19,
)
if result != 0 or argon.argon2_verify(encoded.value, password, len(password), 2) != 0:
    raise SystemExit("Could not create and verify the administrator password hash.")

database = sqlite3.connect(database_path, timeout=10)
try:
    database.execute("PRAGMA foreign_keys = ON")
    database.execute("BEGIN IMMEDIATE")
    users = database.execute(
        "SELECT id, username, role, status FROM users ORDER BY id"
    ).fetchall()
    if not users:
        cursor = database.execute(
            """
            INSERT INTO users
              (username, username_key, password_hash, role, status, project_limit,
               must_change_password, preferred_locale)
            VALUES (?, ?, ?, 'ADMIN', 'ACTIVE', NULL, 1, 'ru')
            """,
            ("Serhii", "serhii", encoded.value.decode("ascii")),
        )
        user_id = cursor.lastrowid
        database.execute(
            """
            INSERT INTO audit_logs
              (actor_user_id, target_user_id, action, entity_type, entity_id,
               outcome, payload)
            VALUES (?, ?, 'DESKTOP_ADMIN_REPAIRED', 'USER', ?, 'SUCCESS', ?)
            """,
            (user_id, user_id, str(user_id), '{"source":"PREVIEW_REPAIR"}'),
        )
    elif len(users) == 1 and users[0][1:] == ("Serhii", "ADMIN", "ACTIVE"):
        user_id = users[0][0]
        database.execute(
            """
            UPDATE users
               SET password_hash = ?,
                   must_change_password = 1,
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = ?
            """,
            (encoded.value.decode("ascii"), user_id),
        )
    else:
        raise RuntimeError(
            "The database contains an unexpected user set. No account was changed."
        )

    database.execute(
        """
        CREATE TRIGGER IF NOT EXISTS preview_auth_sessions_insert_utc
        AFTER INSERT ON auth_sessions
        WHEN substr(NEW.expires_at, -6, 1) NOT IN ('+', '-')
        BEGIN
          UPDATE auth_sessions
             SET expires_at = expires_at || '+00:00',
                 last_seen_at = CASE
                   WHEN last_seen_at IS NOT NULL
                    AND substr(last_seen_at, -6, 1) NOT IN ('+', '-')
                   THEN last_seen_at || '+00:00'
                   ELSE last_seen_at
                 END
           WHERE id = NEW.id;
        END
        """
    )
    database.execute(
        """
        CREATE TRIGGER IF NOT EXISTS preview_auth_sessions_update_utc
        AFTER UPDATE OF expires_at, last_seen_at ON auth_sessions
        WHEN substr(NEW.expires_at, -6, 1) NOT IN ('+', '-')
          OR (
            NEW.last_seen_at IS NOT NULL
            AND substr(NEW.last_seen_at, -6, 1) NOT IN ('+', '-')
          )
        BEGIN
          UPDATE auth_sessions
             SET expires_at = CASE
                   WHEN substr(expires_at, -6, 1) NOT IN ('+', '-')
                   THEN expires_at || '+00:00'
                   ELSE expires_at
                 END,
                 last_seen_at = CASE
                   WHEN last_seen_at IS NOT NULL
                    AND substr(last_seen_at, -6, 1) NOT IN ('+', '-')
                   THEN last_seen_at || '+00:00'
                   ELSE last_seen_at
                 END
           WHERE id = NEW.id;
        END
        """
    )
    database.execute(
        """
        UPDATE auth_sessions
           SET expires_at = CASE
                 WHEN substr(expires_at, -6, 1) NOT IN ('+', '-')
                 THEN expires_at || '+00:00'
                 ELSE expires_at
               END,
               last_seen_at = CASE
                 WHEN last_seen_at IS NOT NULL
                  AND substr(last_seen_at, -6, 1) NOT IN ('+', '-')
                 THEN last_seen_at || '+00:00'
                 ELSE last_seen_at
               END
        """
    )
    database.execute("DELETE FROM auth_attempts WHERE username_key = ?", ("serhii",))
    database.execute(
        """
        INSERT INTO audit_logs
          (actor_user_id, target_user_id, action, entity_type, entity_id,
           outcome, payload)
        VALUES (?, ?, 'DESKTOP_SESSION_COMPATIBILITY_REPAIRED', 'USER', ?,
                'SUCCESS', ?)
        """,
        (user_id, user_id, str(user_id), '{"source":"PREVIEW_REPAIR"}'),
    )
    database.commit()
except Exception:
    database.rollback()
    raise
finally:
    database.close()

verify = sqlite3.connect(database_path, timeout=10)
try:
    row = verify.execute(
        "SELECT username, role, status, must_change_password FROM users WHERE username_key = ?",
        ("serhii",),
    ).fetchone()
finally:
    verify.close()
if row != ("Serhii", "ADMIN", "ACTIVE", 1):
    shutil.copy2(backup_path, database_path)
    raise SystemExit("Verification failed. The original database was restored.")

print("SearchCar preview login repaired successfully.")
print("Username: Serhii")
print("Temporary password: sergiokap09")
print(f"Backup: {backup_path}")
PY
