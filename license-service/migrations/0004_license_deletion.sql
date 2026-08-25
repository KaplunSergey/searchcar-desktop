ALTER TABLE licenses ADD COLUMN deleted_at TEXT;
ALTER TABLE licenses ADD COLUMN deleted_by TEXT;

CREATE INDEX licenses_deleted_idx ON licenses(deleted_at, updated_at);
