#!/usr/bin/env bash
set -euo pipefail

database_name="${1:-searchcar-license-production}"
output_dir="${2:-artifacts/license-service-backup}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
sql_path="${output_dir}/${database_name}-${timestamp}.sql"
checksum_path="${sql_path}.sha256"

mkdir -p "${output_dir}"
pnpm exec wrangler d1 export "${database_name}" \
  --remote \
  --config license-service/wrangler.jsonc \
  --output="${sql_path}"

if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "${sql_path}" > "${checksum_path}"
else
  shasum -a 256 "${sql_path}" > "${checksum_path}"
fi

echo "D1 export created: ${sql_path}"
echo "Checksum created: ${checksum_path}"
echo "Copy both files to encrypted offline storage; signing keys are backed up separately."
