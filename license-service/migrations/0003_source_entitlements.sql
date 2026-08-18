CREATE TABLE source_catalog (
  source_key TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

INSERT INTO source_catalog (source_key, display_name, is_active, created_at, updated_at)
VALUES ('encar', 'Encar', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

CREATE TABLE license_sources (
  license_id TEXT NOT NULL REFERENCES licenses(id) ON DELETE CASCADE,
  source_key TEXT NOT NULL REFERENCES source_catalog(source_key) ON DELETE RESTRICT,
  granted_at TEXT NOT NULL,
  granted_by TEXT,
  PRIMARY KEY (license_id, source_key)
);

CREATE INDEX license_sources_source_idx ON license_sources(source_key, license_id);

-- Existing pilot licenses keep their existing Encar functionality after the
-- entitlement model becomes enforced by newly issued leases.
INSERT OR IGNORE INTO license_sources (license_id, source_key, granted_at, granted_by)
SELECT id, 'encar', CURRENT_TIMESTAMP, 'migration-0003'
FROM licenses
WHERE status = 'ACTIVE';
