-- Default licenses include current and future active sources. Preserve every
-- explicit owner restriction, including an empty list which has no grant rows.
ALTER TABLE licenses ADD COLUMN sources_all INTEGER NOT NULL DEFAULT 1
  CHECK (sources_all IN (0, 1));

UPDATE licenses SET sources_all = 0
WHERE EXISTS (
  SELECT 1 FROM audit_events
  WHERE action = 'LICENSE_SOURCES_UPDATED' AND target_type = 'LICENSE'
    AND outcome = 'SUCCESS' AND target_id = licenses.id
);
