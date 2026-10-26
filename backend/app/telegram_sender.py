"""One authenticated relay attempt. No bot secret, scheduler or HTTP retry here."""
import json
import httpx
from . import telegram_pairing
from .desktop_license_client import LicenseClientError

ERRORS = {'TELEGRAM_RESULT_UNKNOWN', 'TELEGRAM_RATE_LIMIT', 'TELEGRAM_PHOTO_REJECTED',
          'TELEGRAM_FORBIDDEN', 'TELEGRAM_MESSAGE_REJECTED'}

def send_once(config, job, project_id, binding_id, message):
    root, url, binding = config
    unknown = dict(state='UNKNOWN', message_id=None, retry_after=None, error_code='TELEGRAM_RESULT_UNKNOWN')
    try:
        identity = telegram_pairing.load_or_create_device_identity(root)
        signed = identity.signed_request({**binding, 'audience': 'searchcar-telegram-delivery-v1', 'telegram': {
            'action': 'send', 'owner_id': job['owner_id'], 'project_id': project_id,
            'binding_id': binding_id, 'bot_id': job['bot_id'], 'attempt_id': job['attempt_id'], 'message': message}})
    except LicenseClientError:
        return dict(state='FAILED', message_id=None, retry_after=None, error_code='TELEGRAM_DEVICE_UNAVAILABLE')
    try:
        with httpx.Client(timeout=httpx.Timeout(25, connect=5), follow_redirects=False, trust_env=False) as client:
            with client.stream('POST', url+'/v1/send', json=signed) as response:
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 16384:
                        return unknown
                status = response.status_code
        envelope = json.loads(raw)
        if not isinstance(envelope, dict):
            return unknown
        if status != 200 or envelope.get('ok') is not True:
            error = (envelope.get('error') or {}).get('code') if isinstance(envelope.get('error'), dict) else None
            if status == 429 and error == 'PAIRING_RATE_LIMITED':
                return dict(state='RATE_LIMITED', message_id=None, retry_after=60, error_code='TELEGRAM_RATE_LIMIT')
            if status == 429 and error == 'CHAT_VERIFICATION_RATE_LIMITED':
                retry = envelope.get('retry_after')
                return dict(state='RATE_LIMITED', message_id=None, retry_after=retry if type(retry) is int and 0 < retry <= 2**31 else None, error_code='TELEGRAM_RATE_LIMIT')
            if status == 503 and error == 'CHAT_VERIFICATION_UNAVAILABLE':
                return dict(state='RETRY_LATER', message_id=None, retry_after=None, error_code='TELEGRAM_CONNECTION_UNAVAILABLE')
            if (status, error) in {(403, 'DEVICE_NOT_AUTHORIZED'), (403, 'RECIPIENT_NOT_AUTHORIZED'),
                    (403, 'CHAT_PERMISSION_REQUIRED'), (503, 'CHAT_PAIRING_DISABLED'),
                    (400, 'INVALID_MESSAGE'), (503, 'DELIVERY_NOT_CONFIGURED'), (503, 'SERVICE_NOT_CONFIGURED')}:
                return dict(state='FAILED', message_id=None, retry_after=None, error_code='TELEGRAM_RELAY_REJECTED')
            return unknown
        value = envelope.get('data')
        if not isinstance(value, dict) or value.get('attempt_id') != job['attempt_id']:
            return unknown
        state, mid, retry, error = (value.get(key) for key in ('state', 'message_id', 'retry_after', 'error_code'))
        if (state not in {'SENT', 'UNKNOWN', 'RATE_LIMITED', 'FAILED', 'PHOTO_REJECTED'}
                or (state == 'SENT' and not (type(mid) is int and 0 < mid < 2**52))
                or (state != 'SENT' and mid is not None) or (state == 'SENT' and error is not None)
                or (state != 'SENT' and error not in ERRORS)
                or (retry is not None and (state != 'RATE_LIMITED' or type(retry) is not int or not 0 < retry <= 2**31))
                or (state == 'PHOTO_REJECTED' and not message['photo'])):
            return unknown
        return dict(state=state, message_id=mid, retry_after=retry, error_code=error)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        # No connection was established, so no POST could have reached the relay.
        return dict(state='RETRY_LATER', message_id=None, retry_after=None, error_code='TELEGRAM_CONNECTION_UNAVAILABLE')
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        return unknown
