-- Chat IDs originate only in Telegram chat_shared, never in a desktop request.
ALTER TABLE pairing_requests ADD COLUMN target_kind TEXT NOT NULL DEFAULT 'private';
ALTER TABLE pairing_requests ADD COLUMN chat_type TEXT NOT NULL DEFAULT 'private';
ALTER TABLE pairing_requests ADD COLUMN requested_by INTEGER;
ALTER TABLE pairing_requests ADD COLUMN request_chat_id INTEGER;
ALTER TABLE pairing_requests ADD COLUMN shared_chat_id INTEGER;
CREATE UNIQUE INDEX ix_pairing_chat_request ON pairing_requests(request_chat_id);
