# SearchCar Desktop

This repository is an isolated copy of the working SearchCar web
application and is reserved for its desktop productization. The original
`SearchCar` repository is not modified by work performed here.

The complete implementation plan is available in
[`docs/DESKTOP_PRODUCTIZATION_PLAN.md`](docs/DESKTOP_PRODUCTIZATION_PLAN.md).
The decisions already agreed with the product owner are recorded in
[`docs/DESKTOP_DECISIONS.md`](docs/DESKTOP_DECISIONS.md), and the planned
nontechnical operating procedures are listed in
[`docs/NONTECHNICAL_OPERATIONS_PLAN.md`](docs/NONTECHNICAL_OPERATIONS_PLAN.md).
The exact upstream commit and copy exclusions are documented in
[`docs/UPSTREAM_BASELINE.md`](docs/UPSTREAM_BASELINE.md).
The Phase 6 Worker/D1 licensing protocol, deployment and recovery procedure is
documented in [`docs/LICENSE_SERVICE.md`](docs/LICENSE_SERVICE.md).
The Phase 7 desktop entitlement, secure device identity and activation flow are
documented in
[`docs/DESKTOP_LICENSE_ENFORCEMENT.md`](docs/DESKTOP_LICENSE_ENFORCEMENT.md).
The maintainer checklist for preparing, building, validating and rolling back
a desktop pilot release is in [`docs/RELEASE_RUNBOOK.md`](docs/RELEASE_RUNBOOK.md).
The Russian-language guide for a pilot user is in
[`docs/DESKTOP_PILOT_GUIDE.md`](docs/DESKTOP_PILOT_GUIDE.md).
The pilot's security boundaries, accepted risks and release checklist are in
[`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md).
The local, cache-free dependency inventory and review procedure are in
[`docs/DEPENDENCY_REVIEW.md`](docs/DEPENDENCY_REVIEW.md).

Until the desktop milestones are implemented, the baseline application below
continues to run through Docker exactly as it did in the source repository.

## Web baseline

A localhost-first, multi-user application for monitoring vehicle listings on
Encar Korea. Projects, favorites, comments, ratings, scan runs, and scheduler
settings are isolated by user. When the same listing appears in projects owned
by different users, its core vehicle data, images, screenshots, and price
history are stored in one shared canonical record.

The application consists of four Docker services:

- `frontend` — web interface at `http://localhost:3000`;
- `backend` — FastAPI server and API documentation at
  `http://localhost:8000/docs`;
- `worker` — scheduler and Playwright-based Encar scanning;
- `db` — PostgreSQL 16.

## Requirements

You do not need to install Python, Node.js, or pnpm separately for the standard
local setup. All required runtimes are installed inside the Docker images.

You need:

- Git;
- Docker Desktop for macOS or Windows, or Docker Engine with Compose v2 for
  Linux;
- approximately 5 GB of free space for Docker images, the database, and
  screenshots;
- internet access during the initial build and when scanning Encar.

Verify the required tools:

```bash
git --version
docker --version
docker compose version
docker info
```

If `docker info` reports that it cannot connect to the Docker daemon, start
Docker Desktop and wait until it displays **Docker is running**.

## 1. Clone the private repository

```bash
git clone https://github.com/KaplunSergey/SearchCar.git
cd SearchCar
```

GitHub may ask you to authenticate. For a private repository, use a Personal
Access Token in the password field instead of your GitHub account password.

If the project is already available locally, open its directory:

```bash
cd /path/to/SearchCar
```

## 2. Configure the environment

Create a local `.env` file and the required storage directories:

```bash
cp .env.example .env
mkdir -p storage legacy-data
```

The `.env` file is excluded from Git and must never be committed.

For a localhost installation, update these values in `.env`:

```dotenv
POSTGRES_DB=encar
POSTGRES_USER=encar
POSTGRES_PASSWORD=replace-with-a-local-password
DATABASE_URL=postgresql+psycopg://encar:replace-with-a-local-password@db:5432/encar

AUTH_HASH_SECRET=replace-with-a-long-random-secret
AUTH_COOKIE_SECURE=false
ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000

LEGACY_DATA_DIR=./legacy-data
NEXT_PUBLIC_API_URL=http://localhost:8000/api
```

The password in `POSTGRES_PASSWORD` must match the password inside
`DATABASE_URL`. For local development, an alphanumeric database password is
easier to use because special characters must otherwise be URL-encoded.

Generate a secure authentication secret with:

```bash
openssl rand -hex 32
```

Copy the generated value into `AUTH_HASH_SECRET`. Never publish the contents of
your `.env` file.

If you do not need to import an older Encar Monitor installation, keep:

```dotenv
LEGACY_DATA_DIR=./legacy-data
```

To import existing data, use its absolute directory path instead:

```dotenv
LEGACY_DATA_DIR=/Users/you/StudioProjects/encar-monitor
```

## 3. Build and start the application

Start all services in the background:

```bash
docker compose up -d --build
```

The first build can take several minutes while Docker downloads PostgreSQL,
Node.js, Python, and the Playwright browser.

Check the service state:

```bash
docker compose ps
```

Expected result:

- `db` — `healthy`;
- `backend` — `healthy`;
- `frontend` — `running`;
- `worker` — `running`.

Alembic migrations run automatically when the backend starts. If you need to
apply them manually, run:

```bash
docker compose exec backend alembic upgrade head
```

## 4. Create the administrator account

After the backend becomes `healthy`, create the first administrator:

```bash
docker compose exec backend python -m app.auth bootstrap-admin --username Serhii
```

The command asks for the password twice. Password characters are not displayed
in the terminal; this is expected. The password must contain between 8 and 128
characters. PostgreSQL stores only its Argon2 hash.

By default, the application asks the administrator to change the password after
the first sign-in. To disable that requirement:

```bash
docker compose exec backend python -m app.auth bootstrap-admin \
  --username Serhii \
  --no-require-password-change
```

Running `bootstrap-admin` again is safe: it updates the selected
administrator's password and revokes their previous sessions.

## 5. Open the application

Open:

- application: [http://localhost:3000](http://localhost:3000);
- Swagger API documentation:
  [http://localhost:8000/docs](http://localhost:8000/docs);
- backend health check:
  [http://localhost:8000/api/health](http://localhost:8000/api/health).

Sign in with the username and password configured in the previous step.

## 6. Add demo data (optional)

Skip this section for a clean production-like installation.

To add several demo projects, one vehicle, sample price history, and scheduler
settings, run:

```bash
docker compose exec backend python -m app.seed
```

The seed command does not add anything if the `Serhii` user already owns at
least one project.

## 7. Import data from the old Encar Monitor (optional)

After changing `LEGACY_DATA_DIR`, recreate the backend so Docker applies the new
read-only mount:

```bash
docker compose up -d --force-recreate backend
```

Run a dry run first:

```bash
docker compose exec backend python -m app.importer /legacy --dry-run
```

Review the result, then perform the import:

```bash
docker compose exec backend python -m app.importer /legacy --commit
```

The same importer is available in the interface under
**Settings → Import from old Encar Monitor**. Repeated imports are idempotent
and must not create duplicates.

See [docs/LEGACY_IMPORT.md](docs/LEGACY_IMPORT.md) for additional details.

## Application management

### View logs

Follow logs from all services:

```bash
docker compose logs -f
```

Follow scanner logs only:

```bash
docker compose logs -f worker
```

Follow backend or frontend logs:

```bash
docker compose logs -f backend
docker compose logs -f frontend
```

Press `Ctrl+C` to stop following logs. The containers will continue running.

### Restart

Restart all services:

```bash
docker compose restart
```

After changing `.env`, recreate the services:

```bash
docker compose up -d --force-recreate
```

After changing dependencies or a Dockerfile, rebuild the images:

```bash
docker compose up -d --build
```

### Stop

Stop the containers while keeping them:

```bash
docker compose stop
```

Start them again:

```bash
docker compose start
```

Remove containers and the Compose network while preserving the PostgreSQL
volume and the `storage/` directory:

```bash
docker compose down
```

Reset the database completely:

```bash
docker compose down -v
```

> **Warning:** `docker compose down -v` permanently deletes the PostgreSQL
> volume, including all users, projects, relationships, and history. It does
> not delete the `storage/` directory.

## Update to a newer version

Before updating, make sure any local changes are committed:

```bash
git status
```

Pull the latest version and rebuild the containers:

```bash
git pull --ff-only origin main
docker compose up -d --build
docker compose ps
```

The backend applies new Alembic migrations automatically. The PostgreSQL volume
and locally stored images in `storage/` are preserved.

## Run checks

Backend tests:

```bash
docker compose exec -T backend pytest -q
```

Frontend tests:

```bash
docker compose exec -T frontend pnpm test
```

Frontend production build:

```bash
docker compose exec -T frontend pnpm build
```

## Troubleshooting

### `Cannot connect to the Docker daemon`

Start Docker Desktop, wait until it has fully initialized, and run:

```bash
docker info
```

### `service "backend" is not running`

Inspect the containers and logs:

```bash
docker compose ps
docker compose logs --tail=200 db backend
```

Common causes include different passwords in `POSTGRES_PASSWORD` and
`DATABASE_URL`, an occupied port `5432`, or a stopped Docker daemon.

### Frontend fails during `pnpm install`

Rebuild the frontend without the Docker build cache:

```bash
docker compose build --no-cache frontend
docker compose up -d frontend
```

### Port `3000`, `8000`, or `5432` is already in use

On macOS or Linux, identify the process with:

```bash
lsof -nP -iTCP:3000 -sTCP:LISTEN
lsof -nP -iTCP:8000 -sTCP:LISTEN
lsof -nP -iTCP:5432 -sTCP:LISTEN
```

Stop the conflicting service or change the left-hand port in the corresponding
`ports` entry in `docker-compose.yml`.

### Sign-in does not work

Reset the administrator password by running:

```bash
docker compose exec backend python -m app.auth bootstrap-admin --username Serhii
```

### Scans do not start

Make sure the worker is running:

```bash
docker compose ps worker
docker compose logs --tail=200 worker
```

The scheduler must be enabled and at least one project must be selected.

### Encar displays a CAPTCHA or limits access

The application records a CAPTCHA as a project error and does not attempt to
bypass it. Disable frequent updates, increase the scan interval, and wait for
access to recover.

## Data storage

- PostgreSQL data is stored in the Docker volume `postgres_data`.
- Main images and latest screenshots are stored under
  `storage/cars/<canonical-id>/`.
- PostgreSQL stores file paths and checksums, not binary image data.
- A successful refresh atomically replaces the current main image and
  screenshot.
- The first scheduled run is set to `current time + interval`; it does not start
  immediately after Docker starts.
- CAPTCHA errors are stored separately from listing statuses.
- A failed search does not increment a vehicle's absence counters.

## Architecture rules

- Project names and search URLs are unique inside a user's workspace.
- A canonical Encar ID is global, while URL and source IDs are stored as
  aliases.
- Favorite and viewed states belong to the vehicle-project relationship.
- Comments, ratings, and exclusions belong to the current user's vehicle state
  and do not affect other users.
- A standard account is limited to one project by default. An administrator can
  change or remove the limit.
- Registration is implemented but disabled by default. When enabled, new
  accounts require administrator approval.
- Disappearance from search results does not mean that a vehicle was sold.
  `SOLD` is assigned only after Encar returns a confirmed sold or deleted
  listing page.
- Excluding a vehicle disables tracking only for that user and preserves the
  shared canonical record.
- A new price-history point is created only after a confirmed price change.

Additional documentation:

- [Architecture](docs/ARCHITECTURE.md)
- [Database](docs/DATABASE.md)
- [Legacy data import](docs/LEGACY_IMPORT.md)
- [Acceptance checklist](docs/ACCEPTANCE.md)
