# Architecture

```text
Browser → login/session → Next.js frontend → FastAPI → PostgreSQL
                                              ↓ queue
                                         worker process → Playwright → Encar
                              ↑
                    persisted scheduler settings
```

The frontend polls `/api/scans/{id}/progress`. FastAPI performs validation and transactions but never launches a browser. The worker claims `QUEUED` rows, prevents duplicate payloads, processes projects sequentially inside one browser context, and commits project outcomes independently so CAPTCHA in one project does not block the rest.

Authentication uses an HttpOnly session cookie, rotating CSRF token, Argon2
password hashes, origin checks, login throttling and an audit log. Every project,
scan and scheduler query is owner-scoped. Blocking an account revokes its
sessions and cancels or pauses its queued work. Administrator screens expose
account controls and aggregate usage only; they do not bypass project ownership.

Cars use a hybrid storage model. `cars`, snapshots, images and price history are
canonical and global. `projects`, `project_cars`, `user_car_states`, scans and
schedulers are user-owned. Each project relation stores its last observed
snapshot, price and fingerprint, so a shared canonical update cannot consume
another user's change report.

FAST scans read the search list and open details only for new, list-changed, incomplete or manually selected cars. ACCURATE scans open every non-excluded result. Search IDs are taken only from listing links. If the Encar page displays a different registration ID, the row is rejected instead of being merged through an alias. Price changes use the primary detail price and require two identical reads. Individual refresh uses the same protected detail pipeline.

Each project independently selects `FIRST_PAGE` (default) or `ALL_PAGES`.
The full mode decodes Encar's search fragment, preserves filters and sorting,
and changes only `page`/`cursor`. All pages are collected and deduplicated
before absence is evaluated. A timeout, CAPTCHA, unexpected active page, empty
expected page or repeated page fails the enumeration and cannot mark cars
missing. A first-page scan is intentionally partial and therefore never changes
project-wide absence state.

Search absence is project-local. It can produce `NOT_FOUND_IN_SEARCH` and later `RELISTED` for that same active project relation, but never `SOLD`. `SOLD` requires the explicit Encar sold/deleted page marker from the direct listing URL.

Times are stored as UTC-aware timestamps. The browser formats them with the selected locale and local timezone. Scheduled settings persist in PostgreSQL; the worker calculates the first `next_run_at` from the chosen interval.

The optional Sites deployment is a frontend preview. The complete operational system is the Docker Compose localhost deployment because Playwright, PostgreSQL and Python worker processes intentionally remain separate.
