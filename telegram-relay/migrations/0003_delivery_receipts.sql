-- One-attempt receipts only: no content, queue, scheduler, waits or retries.
CREATE TABLE delivery_receipts (
  license_id TEXT NOT NULL, device_id TEXT NOT NULL, public_key TEXT NOT NULL,
  owner_id INTEGER NOT NULL, attempt_id TEXT NOT NULL, project_id INTEGER NOT NULL,
  payload_hash TEXT NOT NULL, state TEXT NOT NULL, created_at INTEGER NOT NULL,
  message_id INTEGER, retry_after INTEGER, error_code TEXT,
  PRIMARY KEY(license_id,device_id,public_key,owner_id,attempt_id)
);
