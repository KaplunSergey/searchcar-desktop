# Database schema

| Entity | Purpose |
|---|---|
| `users` | Login identity, role, approval/block status and configurable project limit |
| `auth_sessions` | Hashed session/CSRF tokens, expiry and revocation |
| `auth_attempts` | Privacy-preserving login throttling data |
| `audit_logs` | Administrative and authentication audit trail |
| `projects` | User-owned Encar search, detail mode, page depth and automatic participation |
| `cars` | Shared canonical global vehicle record and structured details |
| `project_cars` | Project membership, favorite/viewed state, search status, missing counter and project-specific last observation |
| `user_car_states` | Per-user comment, rating and exclusion |
| `car_aliases` | Legacy source IDs used only for local lookup; scans never merge listings through aliases |
| `car_snapshots` | Raw and normalized change/error snapshots |
| `price_history` | Distinct extracted prices |
| `car_events` | User and scraper event timeline |
| `car_images` | Current image/screenshot paths and checksums |
| `scan_runs` | User-owned durable job, claim owner, attempt count, heartbeat, timing and progress |
| `project_scan_runs` | Per-project outcome and technical error |
| `scheduler_settings` | Per-user enabled state, interval and next run |
| `scheduled_projects` | Projects included in automatic runs |

Search membership (`FOUND` / `NOT_FOUND_IN_SEARCH`) belongs to `project_cars`;
confirmed Encar availability belongs to `cars.status`. A missing search result
never proves a sale. Deletion disables only the selected project relation.
User exclusion disables that user's relations while retaining the canonical car
for shared storage and local URL lookup.

`projects.search_page_mode` is `FIRST_PAGE` by default or `ALL_PAGES`.
Only a complete `ALL_PAGES` scan can update project-wide search absence.

`cars`, `car_snapshots`, `price_history`, `car_events` and `car_images`
carry `integrity_status` and `integrity_reason`. Unreliable rows are preserved
for audit as `INVALIDATED`, but are excluded from cards, price charts and
reports. Orphan listings that were previously attached as an alias of another
canonical car are kept as `QUARANTINED` and no longer participate in projects.

The desktop database has an independent, versioned migration ledger in
`desktop_schema_migrations`. Startup applies migrations under SQLite
`BEGIN IMMEDIATE`, then reconciles abandoned `RUNNING` or `CANCEL_REQUESTED`
runs to `INTERRUPTED`. Their progress and partial report remain available in
history. A partial unique index and one atomic `UPDATE ... RETURNING` claim
permit only one active scan worker even if two worker loops race.

All mapped timestamps use `UTCDateTime`. PostgreSQL stores native aware values;
SQLite stores normalized UTC values and restores the UTC timezone on read.
