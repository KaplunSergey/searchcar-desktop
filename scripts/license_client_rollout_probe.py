"""Disposable HTTP probe for license_server_compatibility_smoke.mjs."""
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import httpx
from app.desktop_license_client import LicenseServiceClient


class FixtureStore:
    def __init__(self, path):
        self.path = path

    def load(self):
        return self.path.read_bytes() if self.path.exists() else None

    def save(self, value):
        self.path.write_bytes(value)


root, url, phase = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
assert url.startswith('http://127.0.0.1:'), 'probe must use its owned loopback server'
client = LicenseServiceClient(root, service_url=url, store=FixtureStore(root / 'fixture-identity.bin'))
previous_file = root / 'fixture-result.json'
previous = json.loads(previous_file.read_text()) if previous_file.exists() else None
result = client.activate_trial('local-rollout-fixture@example.test') if phase == 'bootstrap' else client.refresh()
assert result['can_search'], result
if previous:
    assert result['subscription_expires_at'] == previous['subscription_expires_at']


def issue(duration):
    binding = json.loads((root / 'license' / 'binding.json').read_text())
    response = httpx.post(url + '/v1/admin/activation-codes', json={
        'license_id': binding['license_id'], 'duration': duration,
    }, headers={'Authorization': 'Bearer ' + os.environ['SEARCHCAR_COMPAT_ADMIN_TOKEN'],
                'Idempotency-Key': str(uuid4()), 'X-Request-Id': str(uuid4())}, trust_env=False)
    response.raise_for_status()
    return response.json()['data']['activation_code']


if phase == 'current':
    identity_before = (root / 'fixture-identity.bin').read_bytes()
    broken = b'{invalid-trusted-time'
    repair_time = os.environ.get('SEARCHCAR_COMPAT_REPAIR_TIME', '1') == '1'
    if repair_time:
        (root / 'license' / 'trusted-time.json').write_bytes(broken)
    recovered = client.redeem(issue('CURRENT'))
    assert recovered['can_search']
    assert recovered['subscription_expires_at'] == result['subscription_expires_at']
    assert (root / 'fixture-identity.bin').read_bytes() == identity_before
    if repair_time:
        backups = list((root / 'license').glob('trusted-time.json.invalid-*'))
        assert len(backups) == 1 and backups[0].read_bytes() == broken
pending = root / 'fixture-pending-code.json'
code = json.loads(pending.read_text()) if pending.exists() else issue('P1M')
renewed = client.redeem(code)
parse = lambda value: datetime.fromisoformat(value.replace('Z', '+00:00'))
assert renewed['can_search']
assert parse(renewed['subscription_expires_at']) > parse(result['subscription_expires_at'])
previous_file.write_text(json.dumps({'subscription_expires_at': renewed['subscription_expires_at']}))
if phase != 'rollback':
    pending.write_text(json.dumps(issue('P1M')))
print(json.dumps({'phase': phase, 'status': 'ok', 'check': True, 'renewal': True,
                  'recovery': phase == 'current', 'prior_server_code': phase != 'bootstrap',
                  'corrupt_time_repaired': phase == 'current' and repair_time}))
