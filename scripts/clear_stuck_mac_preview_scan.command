#!/bin/zsh

set -euo pipefail

DB="$HOME/Library/Application Support/com.searchcar.desktop/data/searchcar.sqlite3"
BACKUP_DIR="$HOME/Library/Application Support/com.searchcar.desktop/backups"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$BACKUP_DIR/searchcar-before-stuck-scan-repair-$STAMP.sqlite3"

pause_on_exit() {
  exit_code=$?
  print ""
  if (( exit_code == 0 )); then
    print "Stuck SearchCar scans cleared successfully."
  else
    print "Repair failed with status $exit_code. Keep this window open and send its output to Codex."
  fi
  print "Press any key to close this window."
  if [[ -t 0 ]]; then
    read -sk 1
  fi
  exit $exit_code
}
trap pause_on_exit EXIT

if [[ ! -f "$DB" ]]; then
  print "SearchCar database not found: $DB"
  exit 1
fi

database_pids=(${(f)"$({ lsof -t "$DB" 2>/dev/null || true; } | sort -nu)"})
if (( ${#database_pids} )); then
  print "Stopping SearchCar backend processes that still hold the database: ${database_pids[*]}"
  kill -TERM "${database_pids[@]}" 2>/dev/null || true
  for attempt in {1..10}; do
    if ! lsof "$DB" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
fi

if lsof "$DB" >/dev/null 2>&1; then
  print "Some SearchCar processes did not stop. Close SearchCar in Activity Monitor and run this repair again."
  lsof "$DB"
  exit 1
fi

mkdir -p "$BACKUP_DIR"
sqlite3 "$DB" ".backup '$BACKUP'"

active_before="$(sqlite3 "$DB" "SELECT COUNT(*) FROM scan_runs WHERE status IN ('QUEUED','RUNNING','CANCEL_REQUESTED');")"
sqlite3 "$DB" <<'SQL'
PRAGMA foreign_keys=ON;
BEGIN IMMEDIATE;
UPDATE scan_runs
SET status='CANCELLED',
    progress=0,
    payload=json_set(
      COALESCE(payload, '{}'),
      '$.cancellation_reason', 'PREVIEW_STUCK_SCAN_REPAIR',
      '$.cancelled_at', strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'),
      '$.current_project_id', NULL
    ),
    error=COALESCE(error, 'Cancelled while repairing an obsolete preview queue'),
    updated_at=CURRENT_TIMESTAMP
WHERE status IN ('QUEUED','RUNNING','CANCEL_REQUESTED');
UPDATE project_scan_runs
SET status='CANCELLED', error_code='PREVIEW_REPAIR'
WHERE status IN ('QUEUED','RUNNING');
COMMIT;
PRAGMA integrity_check;
SQL

active_after="$(sqlite3 "$DB" "SELECT COUNT(*) FROM scan_runs WHERE status IN ('QUEUED','RUNNING','CANCEL_REQUESTED');")"
print "Cleared active scans: $active_before"
print "Remaining active scans: $active_after"
print "Backup: $BACKUP"

[[ "$active_after" == "0" ]]
