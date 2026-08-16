#!/bin/zsh

set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"
TOOLCHAIN_ROOT="${PROJECT_ROOT:h:h}/.toolchains"
export RUSTUP_HOME="$TOOLCHAIN_ROOT/rustup"
export CARGO_HOME="$TOOLCHAIN_ROOT/cargo"
export SEARCHCAR_MACOS_LOCAL_KEY_FALLBACK=1
NODE_BIN="/Users/sergeykaplun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin"
PNPM_DIR="/Users/sergeykaplun/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback"
export PATH="$CARGO_HOME/bin:$NODE_BIN:$PNPM_DIR:$PATH"
PYTHON_BIN="/Users/sergeykaplun/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
BUILD_PYTHON="$PROJECT_ROOT/.desktop-build-venv/bin/python"
PNPM_BIN="$PNPM_DIR/pnpm"
DESTINATION="/Users/sergeykaplun/Documents/Codex/SearchCar Desktop Fixed.app"
BUILD_LOG="/Users/sergeykaplun/Documents/Codex/SearchCar Desktop Fixed build.log"
DESKTOP_DATA_DIR="/Users/sergeykaplun/Library/Application Support/com.searchcar.desktop"
SHELL_PATTERN="/Users/sergeykaplun/Documents/Codex/SearchCar Desktop Fixed.*\\.app/Contents/MacOS/searchcar-desktop"
SIDECAR_PATTERN="searchcar-core.*serve.*--data-dir ${DESKTOP_DATA_DIR}"

exec > >(tee "$BUILD_LOG") 2>&1

pause_on_exit() {
  exit_code=$?
  print ""
  if (( exit_code == 0 )); then
    print "SearchCar Desktop Fixed was rebuilt successfully."
    print "Application: $DESTINATION"
  else
    print "Build failed with status $exit_code."
    print "Build log: $BUILD_LOG"
  fi
  print "Press any key to close this window."
  if [[ -t 0 ]]; then
    read -sk 1
  fi
  exit $exit_code
}
trap pause_on_exit EXIT

desktop_shell_is_running() {
  pgrep -f "$SHELL_PATTERN" >/dev/null 2>&1 \
    || lsof -t /tmp/com_searchcar_desktop_si.sock >/dev/null 2>&1
}

if desktop_shell_is_running; then
  print "SearchCar Desktop is still running. Close it with the exit confirmation, then run this build again."
  exit 2
fi

if [[ ! -x "$CARGO_HOME/bin/cargo" ]]; then
  print "Local Rust is not installed; installing it now..."
  SEARCHCAR_NO_PAUSE=1 "$SCRIPT_DIR/install_local_rust.command"
fi
if [[ ! -x "$PYTHON_BIN" ]] || [[ ! -x "$PNPM_BIN" ]] || [[ ! -x "$NODE_BIN/node" ]]; then
  print "Codex Python, Node.js or pnpm runtime is unavailable."
  exit 1
fi

cd "$PROJECT_ROOT"
if [[ ! -x "$BUILD_PYTHON" ]]; then
  "$PYTHON_BIN" -m venv "$PROJECT_ROOT/.desktop-build-venv"
fi

print "Installing pinned build dependencies..."
node --version
"$BUILD_PYTHON" -m pip install -r backend/requirements-desktop-build.txt
"$PNPM_BIN" install --frozen-lockfile

print "Checking the license client and Cloudflare Worker..."
PYTHONPATH="$PROJECT_ROOT/backend" "$BUILD_PYTHON" -m pytest -q \
  backend/tests/test_desktop_license_client.py \
  backend/tests/test_desktop_runtime.py
"$PNPM_BIN" license:typecheck
"$PNPM_BIN" license:test

print "Preparing browser runtimes..."
"$BUILD_PYTHON" scripts/prepare_desktop_browser.py
"$BUILD_PYTHON" scripts/prepare_desktop_playwright_driver.py

print "Building backend sidecar..."
"$BUILD_PYTHON" scripts/build_desktop_sidecar.py --mode onefile

print "Checking and building the native shell..."
cargo check --manifest-path desktop/src-tauri/Cargo.toml
"$PNPM_BIN" desktop:tauri:build \
  --target aarch64-apple-darwin \
  --bundles app \
  --no-sign \
  --ci

BUILT_APP="$(find desktop/src-tauri/target/aarch64-apple-darwin/release/bundle/macos -maxdepth 1 -type d -name '*.app' | head -n 1)"
if [[ -z "$BUILT_APP" ]]; then
  print "Built macOS application was not found."
  exit 1
fi

if desktop_shell_is_running; then
  print "SearchCar Desktop was opened during the build. Close it and run the build again; the live bundle was not replaced."
  exit 2
fi

print "Stopping orphaned SearchCar backend processes..."
pkill -TERM -f "$SIDECAR_PATTERN" >/dev/null 2>&1 || true
for _attempt in {1..160}; do
  if ! pgrep -f "$SIDECAR_PATTERN" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done
if pgrep -f "$SIDECAR_PATTERN" >/dev/null 2>&1; then
  pkill -KILL -f "$SIDECAR_PATTERN" >/dev/null 2>&1 || true
fi
if pgrep -f "$SIDECAR_PATTERN" >/dev/null 2>&1; then
  print "Could not stop orphaned SearchCar backend processes; the application was not replaced."
  exit 3
fi

NEW_APP="${DESTINATION}.new"
BACKUP_APP="${DESTINATION%.app} Previous $(date -u +%Y%m%dT%H%M%SZ).app"
if [[ -e "$NEW_APP" ]]; then
  mv "$NEW_APP" "${NEW_APP}.stale.$(date -u +%s)"
fi
ditto "$BUILT_APP" "$NEW_APP"
codesign --force --deep --sign - "$NEW_APP"
codesign --verify --deep --strict "$NEW_APP"
if desktop_shell_is_running; then
  print "SearchCar Desktop was opened before installation. Close it and run this build again; the live bundle was not replaced."
  exit 2
fi
if [[ -d "$DESTINATION" ]]; then
  mv "$DESTINATION" "$BACKUP_APP"
fi
mv "$NEW_APP" "$DESTINATION"

print "Application checksum:"
shasum -a 256 "$DESTINATION/Contents/MacOS/searchcar-desktop"
