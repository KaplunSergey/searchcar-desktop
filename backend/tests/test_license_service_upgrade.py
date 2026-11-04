"""The production schema may already have 0008 applied before recovery 0007."""
from pathlib import Path
import sqlite3


def test_late_recovery_migration_preserves_legacy_codes_bindings_and_renewals():
    migrations = Path(__file__).resolve().parents[2] / 'license-service' / 'migrations'
    with sqlite3.connect(':memory:') as db:
        db.execute('PRAGMA foreign_keys=ON')
        for path in sorted(migrations.glob('*.sql')):
            if path.name != '0007_license_recovery_codes.sql':
                db.executescript(path.read_text())
        db.execute("""INSERT INTO licenses
          (id,kind,started_at,expires_at,created_at,updated_at,sources_all)
          VALUES ('old-license','SUBSCRIPTION','2026-01-01','2027-01-01','2026-01-01','2026-01-01',0)""")
        db.execute("INSERT INTO license_sources VALUES ('old-license','encar','2026-01-01','fixture')")
        db.execute("""INSERT INTO devices
          (id,license_id,public_key,fingerprint_hash,first_seen_at,last_seen_at,created_at)
          VALUES ('old-device','old-license','synthetic-key','synthetic-hash','2026-01-01','2026-01-01','2026-01-01')""")
        for code in ['used', 'unused']:
            db.execute("""INSERT INTO activation_codes
              (id,license_id,code_hash,code_hint,duration_months,created_at)
              VALUES (?,'old-license',?,?,1,'2026-01-01')""", (code, code, code))
        db.execute("""INSERT INTO renewals VALUES
          ('renewal','old-license','used','2026-12-01','2027-01-01',0,0,'fixture','2026-01-01')""")
        tables = ['licenses','license_sources','devices','activation_codes','renewals','source_catalog']
        before = {table: db.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall() for table in tables}
        db.executescript((migrations / '0007_license_recovery_codes.sql').read_text())
        assert {table: db.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall() for table in tables} == before
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.execute('SELECT COUNT(*) FROM all_activation_codes').fetchone()[0] == 2
        # The old Worker's table names/updates still work after a code-only rollback.
        db.execute("UPDATE activation_codes SET used_at='2026-11-04', used_by_device_id='old-device' WHERE id='unused'")
        db.execute("""INSERT INTO renewals VALUES
          ('next-renewal','old-license','unused','2027-01-01','2027-02-01',0,0,'fixture','2026-11-04')""")
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.execute('SELECT COUNT(*) FROM renewals').fetchone()[0] == 2
        db.execute("""INSERT INTO license_recovery_codes
          (id,license_id,device_id,code_hash,code_hint,expires_at,created_at)
          VALUES ('recovery','old-license','old-device','recovery-hash','ABCDE','2026-12-01','2026-11-04')""")
        # Rehearse logical export/restore locally, including the new view and FK history.
        exported = '\n'.join(db.iterdump())
        with sqlite3.connect(':memory:') as restored:
            restored.executescript(exported)
            restored.execute('PRAGMA foreign_keys=ON')
            for table in tables + ['license_recovery_codes']:
                assert restored.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall() == db.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall()
            assert restored.execute('PRAGMA foreign_key_check').fetchall() == []
            assert restored.execute('SELECT COUNT(*) FROM all_activation_codes').fetchone()[0] == 3
