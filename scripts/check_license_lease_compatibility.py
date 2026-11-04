"""Validate a synthetic Worker lease with the backend selected by PYTHONPATH.

Used by the local Worker compatibility test; never reads installed app data.
"""
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from app.desktop_license import evaluate_search_entitlement


def main():
    sample = json.load(sys.stdin)
    lease = sample['lease']
    payload = lease['payload']
    with tempfile.TemporaryDirectory(prefix='searchcar-lease-compat-') as directory:
        root = Path(directory)
        target = root / 'license'
        target.mkdir()
        (target / 'binding.json').write_text(json.dumps({
            'protocol_version': 1, 'license_id': payload['license_id'],
            'device_id': payload['device_id'],
        }))
        (target / 'lease.json').write_text(json.dumps(lease))
        result = evaluate_search_entitlement(data_dir=root, mode='required',
            public_keys={payload['key_id']: sample['public_key']}).as_dict()
        assert result['can_search'] is sample['can_search'], result.get('reason')
        parse = lambda value: datetime.fromisoformat(value.replace('Z', '+00:00'))
        assert parse(result['subscription_expires_at']) == parse(payload['subscription_expires_at'])
        print(json.dumps({'status': 'ok', 'can_search': result['can_search']}))


if __name__ == '__main__':
    main()
