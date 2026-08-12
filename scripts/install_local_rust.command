#!/bin/zsh

set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"
TOOLCHAIN_ROOT="${PROJECT_ROOT:h:h}/.toolchains"
export RUSTUP_HOME="$TOOLCHAIN_ROOT/rustup"
export CARGO_HOME="$TOOLCHAIN_ROOT/cargo"
RUSTUP_INIT="$TOOLCHAIN_ROOT/downloads/rustup-init"

pause_on_exit() {
  exit_code=$?
  print ""
  if (( exit_code == 0 )); then
    print "Local Rust toolchain is ready: $CARGO_HOME/bin/cargo"
  else
    print "Rust installation failed with status $exit_code."
  fi
  if [[ "${SEARCHCAR_NO_PAUSE:-0}" == "1" ]]; then
    exit $exit_code
  fi
  print "Press any key to close this window."
  if [[ -t 0 ]]; then
    read -sk 1
  fi
  exit $exit_code
}
trap pause_on_exit EXIT

mkdir -p "$RUSTUP_HOME" "$CARGO_HOME" "${RUSTUP_INIT:h}"

if [[ ! -x "$CARGO_HOME/bin/cargo" ]]; then
  machine="$(uname -m)"
  case "$machine" in
    arm64) rustup_target="aarch64-apple-darwin" ;;
    x86_64) rustup_target="x86_64-apple-darwin" ;;
    *) print "Unsupported macOS architecture: $machine"; exit 1 ;;
  esac
  print "Downloading rustup for $rustup_target..."
  curl --fail --location --show-error \
    "https://static.rust-lang.org/rustup/dist/$rustup_target/rustup-init" \
    --output "$RUSTUP_INIT"
  chmod 755 "$RUSTUP_INIT"
  "$RUSTUP_INIT" -y --profile minimal --default-toolchain stable --no-modify-path
fi

"$CARGO_HOME/bin/rustup" toolchain install stable --profile minimal
"$CARGO_HOME/bin/rustup" default stable
"$CARGO_HOME/bin/rustc" --version
"$CARGO_HOME/bin/cargo" --version
