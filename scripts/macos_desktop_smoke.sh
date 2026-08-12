#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 6 ]]; then
  echo "usage: $0 SIDECAR BROWSER_DIR PLAYWRIGHT_DRIVER_DIR FRONTEND_DIR RUNTIME_ROOT OUTPUT_DIR" >&2
  exit 2
fi

sidecar="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
browser_dir="$(cd "$2" && pwd)"
playwright_driver_dir="$(cd "$3" && pwd)"
frontend_dir="$(cd "$4" && pwd)"
runtime_root="$5"
output_dir="$6"
mkdir -p "$runtime_root" "$output_dir"
started_at="$(date +%s)"

data_dir="$runtime_root/serve-data"
screenshot="$output_dir/compiled-browser-check.png"
stdout_log="$output_dir/compiled-sidecar.stdout.log"
stderr_log="$output_dir/compiled-sidecar.stderr.log"
cookie_jar="$output_dir/session.cookies"

"$sidecar" check --data-dir "$runtime_root/database-check" --port 18772
"$sidecar" browser-check \
  --browser-dir "$browser_dir" \
  --playwright-driver-dir "$playwright_driver_dir" \
  --output "$screenshot"
if [[ ! -s "$screenshot" ]] || [[ "$(stat -f%z "$screenshot")" -lt 1024 ]]; then
  echo "compiled browser screenshot is missing or empty" >&2
  exit 1
fi

port="$((20000 + RANDOM % 20000))"
secret="$(openssl rand -hex 32)"
SEARCHCAR_DESKTOP_SESSION_SECRET="$secret" \
  "$sidecar" serve \
  --data-dir "$data_dir" \
  --frontend-dir "$frontend_dir" \
  --browser-dir "$browser_dir" \
  --playwright-driver-dir "$playwright_driver_dir" \
  --host 127.0.0.1 \
  --port "$port" >"$stdout_log" 2>"$stderr_log" &
sidecar_pid=$!

cleanup() {
  if kill -0 "$sidecar_pid" 2>/dev/null; then
    kill "$sidecar_pid" 2>/dev/null || true
    wait "$sidecar_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT

base_url="http://127.0.0.1:$port"
healthy=0
for _ in $(seq 1 360); do
  if ! kill -0 "$sidecar_pid" 2>/dev/null; then
    echo "compiled sidecar exited before health check" >&2
    exit 1
  fi
  if [[ "$(curl -sS -o /dev/null -w '%{http_code}' "$base_url/api/health" || true)" == "200" ]]; then
    healthy=1
    break
  fi
  sleep 0.25
done
if [[ "$healthy" -ne 1 ]]; then
  echo "compiled sidecar health check timed out" >&2
  exit 1
fi

unauthorized="$(curl -sS -o /dev/null -w '%{http_code}' "$base_url/")"
if [[ "$unauthorized" != "403" ]]; then
  echo "desktop root was available without bootstrap session" >&2
  exit 1
fi

curl -sS -L -c "$cookie_jar" \
  "$base_url/desktop/bootstrap?token=$secret" >"$output_dir/bootstrap.html"
if ! grep -q "SearchCar Desktop" "$output_dir/bootstrap.html"; then
  echo "desktop bootstrap did not serve the SPA" >&2
  exit 1
fi

shutdown_status="$(curl -sS -o /dev/null -w '%{http_code}' -X POST \
  "$base_url/desktop/shutdown?token=$secret")"
if [[ "$shutdown_status" != "202" ]]; then
  echo "desktop sidecar rejected graceful shutdown" >&2
  exit 1
fi
for _ in $(seq 1 180); do
  if ! kill -0 "$sidecar_pid" 2>/dev/null; then
    break
  fi
  sleep 0.25
done
if kill -0 "$sidecar_pid" 2>/dev/null; then
  echo "desktop sidecar did not stop gracefully" >&2
  exit 1
fi
wait "$sidecar_pid"
trap - EXIT

sidecar_bytes="$(stat -f%z "$sidecar")"
screenshot_bytes="$(stat -f%z "$screenshot")"
browser_kib="$(du -sk "$browser_dir" | awk '{print $1}')"
frontend_kib="$(du -sk "$frontend_dir" | awk '{print $1}')"
sidecar_sha256="$(shasum -a 256 "$sidecar" | awk '{print $1}')"
elapsed_seconds="$(( $(date +%s) - started_at ))"
printf '{\n  "status": "ok",\n  "target": "aarch64-apple-darwin",\n  "database": "ok",\n  "browser": "ok",\n  "protected_session": "ok",\n  "graceful_shutdown": "ok",\n  "sidecar_bytes": %s,\n  "browser_bytes": %s,\n  "frontend_bytes": %s,\n  "screenshot_bytes": %s,\n  "elapsed_seconds": %s,\n  "sidecar_sha256": "%s"\n}\n' \
  "$sidecar_bytes" "$((browser_kib * 1024))" "$((frontend_kib * 1024))" \
  "$screenshot_bytes" "$elapsed_seconds" "$sidecar_sha256" \
  >"$output_dir/compiled-smoke.json"
cat "$output_dir/compiled-smoke.json"
