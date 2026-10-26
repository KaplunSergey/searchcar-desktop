"""Characterization: real queue/signing/sender, only the network reply is fake."""
import base64
from copy import deepcopy
from datetime import timedelta
import json
from threading import Event

import httpx
from httpx import Client as RealClient
import pytest

from app import telegram_dispatch as delivery, telegram_pairing, telegram_sender
from app.desktop_license import canonical_json
from test_telegram_dispatch import ready_delivery, native_pairing, auction_api
from test_telegram_reschedules import change, notices, reconcile, scan, setting


@pytest.fixture
def signed_transport(monkeypatch):
    def connect(f):
        requests, replies = [], []
        sent = Event()
        identity = telegram_pairing.load_or_create_device_identity(f.root)

        def relay(request):
            assert str(request.url) == 'https://relay.fixture.test/v1/send'
            assert request.method == 'POST'
            body = json.loads(request.content)
            proof = body.pop('proof')
            identity.private_key.public_key().verify(base64.urlsafe_b64decode(proof+'=='),
                ('SEARCHCAR-DEVICE-REQUEST-V1\n'+canonical_json(body)).encode())
            assert body['device']['public_key'] == identity.public_key
            assert body['audience'] == 'searchcar-telegram-delivery-v1'
            assert all(body[key] == value for key, value in f.config[2].items())
            op = body['telegram']
            assert (op['action'], op['owner_id'], op['project_id'], op['bot_id'], op['binding_id']) == (
                'send', 1, 1, 123456, 'b'*32)
            assert 'bot_token' not in request.content.decode()
            requests.append(deepcopy(body))
            sent.set()
            reply = replies.pop(0) if replies else 'sent'
            if reply == 'lost':
                raise httpx.ReadTimeout('fixture: response lost after admission', request=request)
            if reply == 'limit':
                return httpx.Response(429, json={'ok': False,
                    'error': {'code': 'CHAT_VERIFICATION_RATE_LIMITED'}, 'retry_after': 17})
            if reply == 'forbidden':
                return httpx.Response(403, json={'ok': False, 'error': {'code': 'CHAT_PERMISSION_REQUIRED'}})
            return httpx.Response(200, json={'ok': True, 'data': {
                'attempt_id': op['attempt_id'], 'state': 'SENT', 'message_id': 700+len(requests),
                'retry_after': None, 'error_code': None}})

        monkeypatch.setattr(telegram_sender.httpx, 'Client',
            lambda **options: RealClient(transport=httpx.MockTransport(relay), **options))
        monkeypatch.setattr(delivery, 'send_once', telegram_sender.send_once)
        return requests, replies, sent
    return connect


def initial_publication(f, requests):
    assert setting(f)['enabled'] is True
    assert f.client.get('/api/projects/1/telegram-automatic').json()['enabled'] is False
    reconcile(f)
    assert f.post().status_code == 200 and not requests
    assert f.step() and len(requests) == 1
    assert f.get().json()['jobs'][0]['state'] == 'SENT'
    assert not f.calls  # The ready_delivery fixture's high-level fake sender was not used.


@pytest.mark.parametrize('target', ['private', 'group', 'supergroup', 'channel'])
def test_publication_then_reschedule_reaches_the_signed_http_boundary_once(ready_delivery, signed_transport, monkeypatch, target):
    f = ready_delivery
    if target != 'private':
        monkeypatch.setenv('SEARCHCAR_TELEGRAM_CHAT_PAIRING_ENABLED', '1')
        started = f.command('START', target_kind='channel' if target == 'channel' else 'group')
        assert started.status_code == 200, started.text
        pid = started.json()['session']['pairing_id']
        f.counts['pairs'][pid].update(status='AWAITING_CONFIRMATION',
            recipient={'chat_id': -100555, 'chat_type': target, 'title': 'Fixture destination'})
        assert f.command('CONFIRM', pairing_id=pid, chat_id=-100555).status_code == 200
    requests, _, _ = signed_transport(f)
    initial_publication(f, requests)
    original_message = deepcopy(requests[0]['telegram']['message'])
    change(f); scan(f); reconcile(f)
    queued = notices(f)
    assert len(queued) == 1 and queued[0]['state'] == 'QUEUED'
    box = delivery.DeliveryBox(f.root/'notifications'/'telegram-outbox.sqlite3')
    assert box.get(queued[0]['id'], owner_id=1)['chat_type'] == target
    assert f.step() and len(requests) == 2
    assert setting(f)['counts'] == {'SENT': 1}
    message = requests[1]['telegram']['message']
    assert message['photo'] is None and 'Было:' in message['text'] and 'Стало:' in message['text']
    assert '11 октября' in message['text'] and '12 октября' in message['text']
    assert any(entity.get('url') == 'https://www.copart.com/lot/65507986' for entity in message['entities'])
    assert requests[0]['telegram']['message'] == original_message
    assert len({body['request_id'] for body in requests}) == 2
    scan(f, key=11); reconcile(f)
    assert not f.step() and len(requests) == 2 and len(notices(f)) == 1


@pytest.mark.parametrize('reply, expected', [('limit', 'QUEUED'), ('lost', 'UNKNOWN'), ('forbidden', 'FAILED')])
def test_real_sender_results_control_persistent_queue_without_blind_resends(ready_delivery, signed_transport, reply, expected):
    f = ready_delivery
    requests, replies, _ = signed_transport(f)
    initial_publication(f, requests)
    change(f); scan(f); reconcile(f); replies.append(reply)
    assert f.step() and notices(f)[0]['state'] == expected
    assert not f.step() and len(requests) == 2
    f.clock[0] += timedelta(seconds=19)
    # Reopen persisted storage as a restarted dispatcher would, not an in-memory job.
    box = delivery.DeliveryBox(f.root/'notifications'/'telegram-outbox.sqlite3')
    box.recover()
    reconcile(f)
    if reply == 'limit':
        assert f.step() and len(requests) == 3 and notices(f)[0]['state'] == 'SENT'
        assert requests[1]['telegram']['message'] == requests[2]['telegram']['message']
        assert requests[1]['telegram']['attempt_id'] != requests[2]['telegram']['attempt_id']
        assert requests[1]['request_id'] != requests[2]['request_id']
    else:
        scan(f, key=11); reconcile(f)
        assert not f.step() and len(requests) == 2 and notices(f)[0]['state'] == expected


def test_new_dispatcher_sends_persisted_notice_through_real_sender_without_open_settings(ready_delivery, signed_transport, monkeypatch):
    from app import database
    f = ready_delivery
    requests, _, sent = signed_transport(f)
    initial_publication(f, requests)
    change(f); scan(f); reconcile(f)
    job_id = notices(f)[0]['id']
    monkeypatch.setattr(database, 'SessionLocal', f.factory)
    monkeypatch.setattr(delivery.time, 'time', lambda: f.now()/1000)
    sent.clear()
    service = delivery.DeliveryService()
    try:
        service.start()
        assert sent.wait(3), 'persisted notice never reached the signed sender'
    finally:
        service.stop()
    assert not service.thread.is_alive()
    assert len(requests) == 2 and notices(f)[0]['id'] == job_id and notices(f)[0]['state'] == 'SENT'
    assert setting(f)['counts'] == {'SENT': 1}
