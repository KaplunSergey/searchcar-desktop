#!/bin/zsh

set -euo pipefail
umask 077

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"
DEFAULT_KEY_DIR="${PROJECT_ROOT:h}/.searchcar-release-secrets"
KEY_DIR="${SEARCHCAR_UPDATER_KEY_DIR:-$DEFAULT_KEY_DIR}"
PRIVATE_KEY="$KEY_DIR/searchcar-updater.key"
PUBLIC_KEY="$PRIVATE_KEY.pub"
TEMP_PRIVATE="$KEY_DIR/.searchcar-updater.key.$$.tmp"
TEMP_PUBLIC="$TEMP_PRIVATE.pub"

NODE_BIN="/Users/sergeykaplun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin"
PNPM_DIR="/Users/sergeykaplun/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback"
if ! command -v node >/dev/null 2>&1 && [[ -x "$NODE_BIN/node" ]]; then
  export PATH="$NODE_BIN:$PATH"
fi
if ! command -v pnpm >/dev/null 2>&1 && [[ -x "$PNPM_DIR/pnpm" ]]; then
  export PATH="$PNPM_DIR:$PATH"
fi

if ! command -v node >/dev/null 2>&1 || ! command -v pnpm >/dev/null 2>&1; then
  print "Node.js 22 and pnpm are required. They were not found."
  exit 1
fi

mkdir -p "$KEY_DIR"
chmod 700 "$KEY_DIR"
KEY_DIR_REAL="$(cd "$KEY_DIR" && pwd -P)"
PROJECT_ROOT_REAL="$(cd "$PROJECT_ROOT" && pwd -P)"
if [[ "$KEY_DIR_REAL" == "$PROJECT_ROOT_REAL" || "$KEY_DIR_REAL" == "$PROJECT_ROOT_REAL"/* ]]; then
  print "Refusing to store the updater private key inside the Git repository."
  print "Choose an external directory through SEARCHCAR_UPDATER_KEY_DIR."
  exit 2
fi
if [[ -e "$PRIVATE_KEY" || -e "$PUBLIC_KEY" ]]; then
  print "Updater key files already exist; they were not overwritten:"
  print "  $PRIVATE_KEY"
  print "  $PUBLIC_KEY"
  exit 3
fi

cleanup() {
  rm -f "$TEMP_PRIVATE" "$TEMP_PUBLIC"
}
trap cleanup EXIT

cd "$PROJECT_ROOT"
print "Create and confirm a strong password when Tauri asks for it."
print "Save that password in your password manager; it cannot be recovered."
print ""
pnpm exec tauri signer generate --write-keys "$TEMP_PRIVATE"

if [[ ! -s "$TEMP_PRIVATE" || ! -s "$TEMP_PUBLIC" ]]; then
  print "Tauri did not create both key files."
  exit 4
fi
chmod 600 "$TEMP_PRIVATE"
chmod 644 "$TEMP_PUBLIC"
mv "$TEMP_PRIVATE" "$PRIVATE_KEY"
mv "$TEMP_PUBLIC" "$PUBLIC_KEY"
trap - EXIT

print ""
print "Updater keypair created successfully."
print "Private key (never commit or send it): $PRIVATE_KEY"
print "Public key (safe to embed):          $PUBLIC_KEY"
print "Public-key SHA-256:"
shasum -a 256 "$PUBLIC_KEY"
print ""
print "Next GitHub Secrets:"
print "  TAURI_SIGNING_PRIVATE_KEY          = contents of the private key file"
print "  TAURI_SIGNING_PRIVATE_KEY_PASSWORD = the password you just chose"
print ""
print "To copy the private key without printing it:"
print "  pbcopy < \"$PRIVATE_KEY\""
print "After saving both GitHub Secrets, return here and say: ready."
