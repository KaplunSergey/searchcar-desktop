DROP INDEX IF EXISTS devices_public_key_unique;

CREATE UNIQUE INDEX devices_one_active_per_public_key
  ON devices(public_key)
  WHERE is_active = 1;
