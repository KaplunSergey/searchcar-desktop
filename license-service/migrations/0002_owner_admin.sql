CREATE TABLE owner_bootstrap (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  admin_user_id TEXT NOT NULL REFERENCES admin_users(id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL
);
