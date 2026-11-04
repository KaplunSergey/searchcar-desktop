-- Register the shipped auction preview sites. Per-license restrictions remain
-- authoritative. Do not replace grants or overwrite an explicitly inactive site.
INSERT OR IGNORE INTO source_catalog
  (source_key, display_name, is_active, created_at, updated_at)
VALUES
  ('copart', 'Copart', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
  ('iaa', 'IAA', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
