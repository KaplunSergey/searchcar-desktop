-- Reused grants retain the original Telegram consent ordering for /stop.
ALTER TABLE pairing_requests ADD COLUMN consent_update_id INTEGER;
ALTER TABLE pairing_requests ADD COLUMN reuse_source_id TEXT;
