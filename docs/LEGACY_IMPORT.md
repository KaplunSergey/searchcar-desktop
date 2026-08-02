# Legacy import

Set the absolute source path in `.env`:

```dotenv
LEGACY_DATA_DIR=/Users/you/StudioProjects/encar-monitor
```

Restart the backend after changing this value:

```bash
docker compose up -d --force-recreate backend
```

The Settings page shows a read-only source summary before import and provides
the normal import button. The same flow is available from the command line.
Validate without writing:

```bash
docker compose exec backend python -m app.importer /legacy --dry-run
```

Commit only after reviewing the detailed totals:

```bash
docker compose exec backend python -m app.importer /legacy --commit
```

The importer reads `searches.json`, `seen.json`, every historical
`search_debug.json`, `all_found.json`, `viewed_list.json`,
`new_or_updated.json`, the current summary files and referenced screenshots. It
creates projects and project/car relations, canonicalizes and merges aliases,
keeps raw JSON as snapshots, preserves distinct historical price changes and
copies the latest screenshot for every car into the new storage layout.

It deliberately ignores `landed_cost_estimate`, exchange rates, customs, VAT, excise and any prompt text file. Unique constraints and lookups make repeated runs idempotent.
