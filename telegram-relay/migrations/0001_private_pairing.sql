-- Recipient authorization only. No delivery jobs, timers or message payloads.
CREATE TABLE pairing_requests (
  id TEXT PRIMARY KEY,
  license_id TEXT NOT NULL,
  device_id TEXT NOT NULL,
  public_key TEXT NOT NULL,
  owner_id INTEGER NOT NULL,
  project_id INTEGER NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  chat_id INTEGER,
  title TEXT,
  claim_update_id INTEGER UNIQUE,
  claim_message_at INTEGER,
  binding_id TEXT UNIQUE,
  confirmed_at INTEGER,
  revoked_at INTEGER
);
CREATE INDEX ix_pairing_owner ON pairing_requests(license_id,device_id,public_key,owner_id,project_id);
CREATE INDEX ix_pairing_expiry ON pairing_requests(expires_at);
CREATE INDEX ix_pairing_chat ON pairing_requests(chat_id);
CREATE TABLE request_nonces (
  public_key TEXT NOT NULL,
  request_id TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  PRIMARY KEY(public_key,request_id)
);
CREATE INDEX ix_nonce_expiry ON request_nonces(expires_at);
CREATE TABLE security_windows (
  public_key TEXT NOT NULL,
  window_at INTEGER NOT NULL,
  attempts INTEGER NOT NULL,
  PRIMARY KEY(public_key,window_at)
);
