CREATE TABLE schema_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

INSERT INTO schema_meta (key, value) VALUES ('license_protocol_version', '1');

CREATE TABLE customers (
  id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  contact TEXT,
  contact_hash TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX customers_contact_hash_unique
  ON customers(contact_hash)
  WHERE contact_hash IS NOT NULL;

CREATE TABLE licenses (
  id TEXT PRIMARY KEY,
  customer_id TEXT REFERENCES customers(id) ON DELETE RESTRICT,
  kind TEXT NOT NULL CHECK (kind IN ('TRIAL', 'SUBSCRIPTION', 'PERPETUAL')),
  status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'SUSPENDED')),
  started_at TEXT NOT NULL,
  expires_at TEXT,
  perpetual INTEGER NOT NULL DEFAULT 0 CHECK (perpetual IN (0, 1)),
  activation_count INTEGER NOT NULL DEFAULT 0 CHECK (activation_count >= 0),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (
    (perpetual = 1 AND expires_at IS NULL AND kind = 'PERPETUAL') OR
    (perpetual = 0 AND expires_at IS NOT NULL AND kind != 'PERPETUAL')
  )
);

CREATE INDEX licenses_customer_idx ON licenses(customer_id);
CREATE INDEX licenses_expiry_idx ON licenses(status, expires_at);

CREATE TABLE devices (
  id TEXT PRIMARY KEY,
  license_id TEXT NOT NULL REFERENCES licenses(id) ON DELETE RESTRICT,
  public_key TEXT NOT NULL,
  fingerprint_hash TEXT NOT NULL,
  label TEXT,
  is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  deactivated_at TEXT,
  created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX devices_public_key_unique ON devices(public_key);
CREATE UNIQUE INDEX devices_one_active_per_license
  ON devices(license_id)
  WHERE is_active = 1;
CREATE INDEX devices_fingerprint_idx ON devices(fingerprint_hash);

CREATE TABLE trial_claims (
  license_id TEXT PRIMARY KEY REFERENCES licenses(id) ON DELETE RESTRICT,
  subject_hash TEXT NOT NULL UNIQUE,
  fingerprint_hash TEXT NOT NULL UNIQUE,
  claimed_at TEXT NOT NULL
);

CREATE TABLE activation_codes (
  id TEXT PRIMARY KEY,
  license_id TEXT NOT NULL REFERENCES licenses(id) ON DELETE RESTRICT,
  code_hash TEXT NOT NULL UNIQUE,
  code_hint TEXT NOT NULL,
  duration_months INTEGER CHECK (duration_months IN (1, 3, 6, 12)),
  makes_perpetual INTEGER NOT NULL DEFAULT 0 CHECK (makes_perpetual IN (0, 1)),
  expires_at TEXT,
  created_by TEXT,
  created_at TEXT NOT NULL,
  used_at TEXT,
  used_by_device_id TEXT REFERENCES devices(id) ON DELETE RESTRICT,
  CHECK (
    (makes_perpetual = 1 AND duration_months IS NULL) OR
    (makes_perpetual = 0 AND duration_months IS NOT NULL)
  )
);

CREATE INDEX activation_codes_license_idx
  ON activation_codes(license_id, used_at, created_at);

CREATE TABLE license_checks (
  device_id TEXT PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
  license_id TEXT NOT NULL REFERENCES licenses(id) ON DELETE CASCADE,
  last_checked_at TEXT NOT NULL,
  app_version TEXT NOT NULL,
  check_count INTEGER NOT NULL DEFAULT 1,
  last_ip_hash TEXT
);

CREATE TABLE renewals (
  id TEXT PRIMARY KEY,
  license_id TEXT NOT NULL REFERENCES licenses(id) ON DELETE RESTRICT,
  activation_code_id TEXT NOT NULL UNIQUE REFERENCES activation_codes(id) ON DELETE RESTRICT,
  old_expires_at TEXT,
  new_expires_at TEXT,
  old_perpetual INTEGER NOT NULL CHECK (old_perpetual IN (0, 1)),
  new_perpetual INTEGER NOT NULL CHECK (new_perpetual IN (0, 1)),
  actor TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX renewals_license_idx ON renewals(license_id, created_at);

CREATE TABLE device_transfers (
  id TEXT PRIMARY KEY,
  public_code_hash TEXT NOT NULL UNIQUE,
  public_code_hint TEXT NOT NULL,
  claim_token_hash TEXT NOT NULL,
  requested_public_key TEXT NOT NULL,
  requested_fingerprint_hash TEXT NOT NULL,
  requested_label TEXT,
  requested_app_version TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'PENDING'
    CHECK (status IN ('PENDING', 'APPROVED', 'CLAIMED', 'EXPIRED', 'REJECTED')),
  requested_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  license_id TEXT REFERENCES licenses(id) ON DELETE RESTRICT,
  old_device_id TEXT REFERENCES devices(id) ON DELETE RESTRICT,
  new_device_id TEXT REFERENCES devices(id) ON DELETE RESTRICT,
  approved_at TEXT,
  approved_by TEXT,
  claimed_at TEXT
);

CREATE INDEX device_transfers_status_idx
  ON device_transfers(status, expires_at);

CREATE TABLE idempotency_keys (
  scope TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('PROCESSING', 'COMPLETED')),
  response_status INTEGER,
  response_json TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  PRIMARY KEY (scope, idempotency_key)
);

CREATE INDEX idempotency_expiry_idx ON idempotency_keys(expires_at);

CREATE TABLE request_nonces (
  scope TEXT NOT NULL,
  request_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  PRIMARY KEY (scope, request_id)
);

CREATE INDEX request_nonces_expiry_idx ON request_nonces(expires_at);

CREATE TABLE rate_limit_windows (
  scope TEXT NOT NULL,
  subject_hash TEXT NOT NULL,
  window_start INTEGER NOT NULL,
  request_count INTEGER NOT NULL,
  expires_at TEXT NOT NULL,
  PRIMARY KEY (scope, subject_hash, window_start)
);

CREATE INDEX rate_limit_expiry_idx ON rate_limit_windows(expires_at);

CREATE TABLE audit_events (
  id TEXT PRIMARY KEY,
  dedupe_key TEXT UNIQUE,
  actor_type TEXT NOT NULL CHECK (actor_type IN ('DEVICE', 'ADMIN', 'SYSTEM')),
  actor_id TEXT,
  action TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT,
  outcome TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX audit_events_target_idx
  ON audit_events(target_type, target_id, created_at);
CREATE INDEX audit_events_created_idx ON audit_events(created_at);

CREATE TABLE admin_users (
  id TEXT PRIMARY KEY,
  login TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  recovery_codes_hash TEXT,
  is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE admin_sessions (
  id TEXT PRIMARY KEY,
  admin_user_id TEXT NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL
);

CREATE INDEX admin_sessions_expiry_idx ON admin_sessions(expires_at);

CREATE TABLE app_releases (
  version TEXT PRIMARY KEY,
  channel TEXT NOT NULL DEFAULT 'stable',
  minimum_version TEXT,
  published_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  is_current INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0, 1))
);

CREATE UNIQUE INDEX app_releases_one_current_per_channel
  ON app_releases(channel)
  WHERE is_current = 1;
