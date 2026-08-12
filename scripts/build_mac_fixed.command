#!/bin/zsh

set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"
TOOLCHAIN_ROOT="${PROJECT_ROOT:h:h}/.toolchains"
export RUSTUP_HOME="$TOOLCHAIN_ROOT/rustup"
export CARGO_HOME="$TOOLCHAIN_ROOT/cargo"
NODE_BIN="/Users/sergeykaplun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin"
PNPM_DIR="/Users/sergeykaplun/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback"
export PATH="$CARGO_HOME/bin:$NODE_BIN:$PNPM_DIR:$PATH"
PYTHON_BIN="/Users/sergeykaplun/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
BUILD_PYTHON="$PROJECT_ROOT/.desktop-build-venv/bin/python"
PNPM_BIN="$PNPM_DIR/pnpm"
DESTINATION="/Users/sergeykaplun/Documents/Codex/SearchCar Desktop Fixed.app"
BUILD_LOG="/Users/sergeykaplun/Documents/Codex/SearchCar Desktop Fixed build.log"

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

NEW_APP="${DESTINATION}.new"
BACKUP_APP="${DESTINATION%.app} Previous $(date -u +%Y%m%dT%H%M%SZ).app"
if [[ -e "$NEW_APP" ]]; then
  mv "$NEW_APP" "${NEW_APP}.stale.$(date -u +%s)"
fi
ditto "$BUILT_APP" "$NEW_APP"
codesign --force --deep --sign - "$NEW_APP"
codesign --verify --deep --strict "$NEW_APP"
if [[ -d "$DESTINATION" ]]; then
  mv "$DESTINATION" "$BACKUP_APP"
fi
mv "$NEW_APP" "$DESTINATION"

print "Application checksum:"
shasum -a 256 "$DESTINATION/Contents/MacOS/searchcar-desktop"
