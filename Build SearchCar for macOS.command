#!/bin/zsh

set -euo pipefail

PROJECT_ROOT="${0:A:h}"
BUILD_SCRIPT="$PROJECT_ROOT/scripts/build_mac_fixed.command"

if [[ ! -f "$BUILD_SCRIPT" ]]; then
  print "SearchCar build script was not found:"
  print "$BUILD_SCRIPT"
  print ""
  print "Press any key to close this window."
  if [[ -t 0 ]]; then
    read -sk 1
  fi
  exit 1
fi

cd "$PROJECT_ROOT"
exec /bin/zsh "$BUILD_SCRIPT"
