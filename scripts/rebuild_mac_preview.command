#!/bin/zsh

set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"
PYTHON_BIN="/Users/sergeykaplun/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
BUILD_PYTHON="$PROJECT_ROOT/.desktop-build-venv/bin/python"
BUILT_SIDECAR="$PROJECT_ROOT/desktop/src-tauri/binaries/searchcar-core-aarch64-apple-darwin"
PREVIEW_APP="/Users/sergeykaplun/Documents/Codex/SearchCar Desktop Preview 2.app"
APP_SIDECAR="$PREVIEW_APP/Contents/MacOS/searchcar-core"
APP_DRIVER_DIR="$PREVIEW_APP/Contents/Resources/playwright-driver"
DATABASE="$HOME/Library/Application Support/com.searchcar.desktop/data/searchcar.sqlite3"
BUILD_LOG="/Users/sergeykaplun/Documents/Codex/SearchCar Preview 2 build.log"

exec > >(tee "$BUILD_LOG") 2>&1

pause_on_exit() {
  exit_code=$?
  if (( exit_code == 0 )); then
    print ""
    print "SearchCar Preview 2 backend rebuilt successfully."
    print "You can close Terminal and open the application."
  else
    print ""
    print "Build failed with status $exit_code. Keep this window open and send its output to Codex."
  fi
  print "Press any key to close this window."
  if [[ -t 0 ]]; then
    read -sk 1
  fi
  exit $exit_code
}
trap pause_on_exit EXIT

if [[ ! -x "$PYTHON_BIN" ]]; then
  print "Python 3.12 runtime not found: $PYTHON_BIN"
  exit 1
fi

if [[ ! -d "$PREVIEW_APP" ]]; then
  print "Preview application not found: $PREVIEW_APP"
  exit 1
fi

if [[ -f "$DATABASE" ]] && lsof "$DATABASE" >/dev/null 2>&1; then
  print "SearchCar is still running. Quit it completely with Command-Q and run this build again."
  exit 1
fi

cd "$PROJECT_ROOT"

if [[ ! -x "$BUILD_PYTHON" ]]; then
  "$PYTHON_BIN" -m venv "$PROJECT_ROOT/.desktop-build-venv"
fi

print "Installing pinned desktop build dependencies..."
PIP_DISABLE_PIP_VERSION_CHECK=1 "$BUILD_PYTHON" -m pip install \
  -r "$PROJECT_ROOT/backend/requirements-desktop-build.txt"

print "Building the current SearchCar backend sidecar..."
"$BUILD_PYTHON" "$PROJECT_ROOT/scripts/prepare_desktop_playwright_driver.py"
"$BUILD_PYTHON" "$PROJECT_ROOT/scripts/build_desktop_sidecar.py" --mode onefile

if [[ ! -x "$BUILT_SIDECAR" ]]; then
  print "Compiled sidecar not found: $BUILT_SIDECAR"
  exit 1
fi

print "Updating SearchCar Desktop Preview 2..."
ditto "$BUILT_SIDECAR" "$APP_SIDECAR"
ditto "$PROJECT_ROOT/desktop/runtime/playwright-driver" "$APP_DRIVER_DIR"
chmod 755 "$APP_SIDECAR"
codesign --force --deep --sign - "$PREVIEW_APP"
codesign --verify --deep --strict "$PREVIEW_APP"

print "Updated sidecar:"
shasum -a 256 "$APP_SIDECAR"
