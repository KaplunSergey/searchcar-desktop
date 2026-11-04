CREATE TABLE license_recovery_codes (
  id TEXT PRIMARY KEY,
  license_id TEXT NOT NULL REFERENCES licenses(id) ON DELETE RESTRICT,
  device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
  code_hash TEXT NOT NULL UNIQUE,
  code_hint TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  created_by TEXT,
  created_at TEXT NOT NULL,
  used_at TEXT,
  used_by_device_id TEXT REFERENCES devices(id) ON DELETE RESTRICT
);

CREATE INDEX license_recovery_codes_license_idx
  ON license_recovery_codes(license_id, used_at, created_at);

-- A shared read model keeps legacy activation-code lookups and owner UI intact
-- without rebuilding a table referenced by renewal history.
CREATE VIEW all_activation_codes AS
  SELECT id, license_id, code_hash, code_hint, duration_months, makes_perpetual,
         expires_at, created_by, created_at, used_at, used_by_device_id,
         0 AS is_recovery, NULL AS recovery_device_id
    FROM activation_codes
  UNION ALL
  SELECT id, license_id, code_hash, code_hint, NULL AS duration_months, 0 AS makes_perpetual,
         expires_at, created_by, created_at, used_at, used_by_device_id,
         1 AS is_recovery, device_id AS recovery_device_id
    FROM license_recovery_codes;
