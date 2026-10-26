"""Native project pairing: disposable storage and mock relay, never Telegram."""
import base64
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from app.auth import require_csrf, require_user
from app.main import app
from app.models import User
from app.desktop_license import _atomic_json, canonical_json
from app.desktop_license_client import DeviceIdentity
from app import telegram_pairing
from test_auction_api import auction_api


def test_missing_relay_is_explicit_without_accessing_device_credentials(auction_api, monkeypatch):
    client, _, _ = auction_api
    monkeypatch.delenv("SEARCHCAR_TELEGRAM_RELAY_URL", raising=False)
    result = client.get('/api/projects/1/telegram?source_key=copart')
    assert result.status_code == 200, result.text
    assert result.json() == {"available": False, "issue": "telegram_relay_not_configured",
        "recipient": None, "session": None, "delivery_enabled": False}


def test_other_owner_and_wrong_source_never_reach_pairing(auction_api):
    client, _, _ = auction_api
    assert client.get('/api/projects/4/telegram?source_key=copart').status_code == 404
    assert client.get('/api/projects/1/telegram?source_key=iaa').status_code == 404


@pytest.fixture
def native_pairing(auction_api, monkeypatch, tmp_path):
    client, factory, _ = auction_api
    root = tmp_path / 'native'
    _atomic_json(root / 'license' / 'binding.json', {'protocol_version': 1,
        'license_id': '10000000-0000-4000-8000-000000000001', 'device_id': '20000000-0000-4000-8000-000000000001'})
    monkeypatch.setenv('SEARCHCAR_DESKTOP_DATA_DIR', str(root))
    monkeypatch.setenv('SEARCHCAR_TELEGRAM_RELAY_URL', 'https://relay.fixture.test')
    key = Ed25519PrivateKey.generate()
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    identity = DeviceIdentity(key, base64.urlsafe_b64encode(raw).decode().rstrip('='),
        hashlib.sha256(b'SEARCHCAR-DEVICE-FINGERPRINT-V1\n'+raw).hexdigest(), 'Fixture')
    counts = {'identity': 0, 'calls': [], 'pairs': {}, 'fault': None, 'lost_confirm': False}
    def load_identity(_):
        counts['identity'] += 1
        return identity
    monkeypatch.setattr(telegram_pairing, 'load_or_create_device_identity', load_identity)
    def relay(request):
        assert request.url == 'https://relay.fixture.test/v1/pairings'
        body = json.loads(request.content)
        proof = body.pop('proof')
        key.public_key().verify(base64.urlsafe_b64decode(proof+'=='),
            ('SEARCHCAR-DEVICE-REQUEST-V1\n'+canonical_json(body)).encode())
        assert body['audience'] == 'searchcar-telegram-pairing-v1'
        assert body['telegram']['owner_id'] == 1
        op = body['telegram'];counts['calls'].append(deepcopy(op))
        if counts['fault'] == 'redirect': return httpx.Response(302,headers={'location':'https://bad.fixture.test'})
        if counts['fault'] == 'network': raise httpx.ConnectError('private-transport-details',request=request)
        if counts['fault'] == 'limit': return httpx.Response(429,json={'ok':False,'error':{'code':'PAIRING_RATE_LIMITED','message':'private-remote-details'}})
        if op['action'] == 'start':
            pid = f'{len(counts["pairs"])+1:032x}'
            value = {'pairing_id':pid,'project_id':op['project_id'],'bot_id':123456,'status':'WAITING_START',
                'expires_at_ms':int(telegram_pairing.time.time()*1000)+600000,'recipient':None,'binding_id':None,
                'target_kind':op.get('target_kind','private')}
            counts['pairs'][pid] = value
            value = {**value,'start_url':'https://t.me/search_car_app_bot?start=p_'+'x'*32}
        elif op['action'] == 'reuse':
            source=counts['pairs'][op['source_pairing_id']]
            if source['status'] != 'CONNECTED':
                return httpx.Response(409,json={'ok':False,'error':{'code':'PAIRING_CHANGED'}})
            value=counts['pairs'].setdefault(op['pairing_id'],{**deepcopy(source),'pairing_id':op['pairing_id'],
                'project_id':op['project_id'],'binding_id':'c'*32})
            if counts.get('lost_reuse'):
                counts['lost_reuse']=False
                raise httpx.ReadTimeout('private-transport-details',request=request)
        else:
            value = counts['pairs'][op['pairing_id']]
            assert value['project_id'] == op['project_id']
            if op['action'] == 'confirm':
                assert value['recipient']['chat_id'] == op['chat_id']
                value.update(status='CONNECTED',binding_id='b'*32)
                if counts['lost_confirm']:
                    counts['lost_confirm'] = False
                    raise httpx.ReadTimeout('private-transport-details',request=request)
            if op['action'] == 'revoke': value.update(status='REVOKED',recipient=None,binding_id=None)
        value = deepcopy(value)
        if counts['fault'] == 'wrong_project': value['project_id'] = 999
        if counts['fault'] == 'wrong_url': value['start_url'] = 'https://untrusted.fixture.test/private'
        if counts['fault'] == 'group': value.update(status='AWAITING_CONFIRMATION',recipient={'chat_id':-100,'chat_type':'supergroup','title':'Group'})
        return httpx.Response(200,json={'ok':True,'data':value})
    original = httpx.Client
    monkeypatch.setattr(telegram_pairing.httpx,'Client',lambda **options: original(transport=httpx.MockTransport(relay),**options))
    with factory() as db: owner = db.get(User,1)
    app.dependency_overrides[require_csrf] = lambda: owner
    def command(action, **extra):
        return client.post('/api/projects/1/telegram?source_key=copart',json={'action':action,**extra})
    def proposed(pid):
        counts['pairs'][pid].update(status='AWAITING_CONFIRMATION',recipient={'chat_id':555,'chat_type':'private','title':'Alex <script>'})
    return SimpleNamespace(client=client,root=root,counts=counts,command=command,proposed=proposed)


def test_native_flow_is_signed_owner_scoped_and_does_not_send(native_pairing):
    f = native_pairing
    initial = f.client.get('/api/projects/1/telegram').json()
    assert initial['available'] and not initial['delivery_enabled'] and initial['recipient'] is None
    assert f.counts['identity'] == 0 and f.counts['calls'] == []
    started = f.command('START').json();pid = started['session']['pairing_id']
    assert started['session']['start_url'].startswith('https://t.me/search_car_app_bot?start=')
    assert started['recipient'] is None
    cached = f.client.get('/api/projects/1/telegram').json()
    assert 'start_url' not in cached['session'] and len(f.counts['calls']) == 1
    f.proposed(pid)
    assert f.command('CHECK',pairing_id=pid).json()['session']['status'] == 'AWAITING_CONFIRMATION'
    confirmed = f.command('CONFIRM',pairing_id=pid,chat_id=555).json()
    assert confirmed['recipient']['chat_id'] == 555 and confirmed['recipient']['chat_type'] == 'private'
    assert not confirmed['delivery_enabled']
    assert f.client.get('/api/projects/2/telegram?source_key=iaa').json()['recipient'] is None
    db_bytes = (f.root/'notifications'/'telegram-outbox.sqlite3').read_bytes()
    assert b'p_'+b'x'*32 not in db_bytes
    assert all(op['action'] in {'start','status','confirm'} for op in f.counts['calls'])


@pytest.mark.parametrize('kind,chat_type', [('group','group'),('group','supergroup'),('channel','channel')])
def test_chat_kind_is_signed_confirmed_and_saved_only_when_explicitly_enabled(native_pairing,monkeypatch,kind,chat_type):
    f=native_pairing
    monkeypatch.setenv('SEARCHCAR_TELEGRAM_CHAT_PAIRING_ENABLED','1')
    started=f.command('START',target_kind=kind)
    assert started.status_code == 200,started.text
    session=started.json()['session'];pid=session['pairing_id']
    assert session['target_kind'] == kind
    assert f.counts['calls'][-1]['target_kind'] == kind
    f.counts['pairs'][pid].update(status='AWAITING_CONFIRMATION',recipient={'chat_id':-100555,'chat_type':chat_type,'title':'Our team'})
    checked=f.command('CHECK',pairing_id=pid)
    assert checked.status_code == 200,checked.text
    confirmed=f.command('CONFIRM',pairing_id=pid,chat_id=-100555)
    assert confirmed.status_code == 200,confirmed.text
    assert confirmed.json()['recipient']['chat_type'] == chat_type
    saved=f.client.get('/api/telegram/recipients').json()['recipients']
    assert saved[0]['chat_type'] == chat_type and saved[0]['chat_id'] == -100555
    assert not confirmed.json()['delivery_enabled']
    before=len(f.counts['calls'])
    monkeypatch.delenv('SEARCHCAR_TELEGRAM_CHAT_PAIRING_ENABLED')
    assert f.command('USE_SAVED',recipient_id=saved[0]['id']).status_code == 503
    assert f.command('CHECK',pairing_id=pid).status_code == 503
    assert len(f.counts['calls']) == before
    assert f.command('DISCONNECT',binding_id='b'*32).status_code == 200


def test_chat_pairing_gate_rejects_without_relay_or_mutating_existing_recipient(native_pairing):
    f=native_pairing
    assert f.command('START',target_kind='group').status_code == 503
    assert not f.counts['calls']


def test_relay_cannot_change_the_selected_kind_during_confirmation(native_pairing,monkeypatch):
    f=native_pairing;monkeypatch.setenv('SEARCHCAR_TELEGRAM_CHAT_PAIRING_ENABLED','1')
    pid=f.command('START',target_kind='group').json()['session']['pairing_id']
    f.counts['pairs'][pid].update(target_kind='channel',status='AWAITING_CONFIRMATION',
        recipient={'chat_id':-100555,'chat_type':'channel','title':'Wrong kind'})
    assert f.command('CHECK',pairing_id=pid).status_code == 502
    unchanged=f.client.get('/api/projects/1/telegram').json()
    assert unchanged['recipient'] is None and unchanged['session']['target_kind']=='group'


def test_saved_catalogue_is_persistent_deduplicated_and_private_to_owner_device(native_pairing):
    f=native_pairing
    assert f.client.get('/api/telegram/recipients').status_code == 200
    assert f.client.get('/api/telegram/recipients').json()['recipients'] == []
    pid=f.command('START').json()['session']['pairing_id'];f.proposed(pid)
    assert f.client.get('/api/telegram/recipients').json()['recipients'] == []
    f.command('CONFIRM',pairing_id=pid,chat_id=555)
    catalogue=f.client.get('/api/telegram/recipients').json()
    assert len(catalogue['recipients']) == 1
    saved=catalogue['recipients'][0]
    assert saved['chat_id'] == 555 and saved['chat_type'] == 'private'
    assert 'binding_id' not in saved and 'pairing_id' not in saved
    assert f.client.get('/api/telegram/recipients').json() == catalogue
    assert len(f.counts['calls']) == 2  # Local catalogue reads never poll the relay.
    path=f.root/'license'/'binding.json'
    binding=json.loads(path.read_text());binding['device_id']='30000000-0000-4000-8000-000000000001'
    _atomic_json(path,binding)
    assert f.client.get('/api/telegram/recipients').json()['recipients'] == []


def test_saved_recipient_can_be_used_across_sources_without_copying_the_project_grant(native_pairing):
    f=native_pairing;pid=f.command('START').json()['session']['pairing_id'];f.proposed(pid)
    f.command('CONFIRM',pairing_id=pid,chat_id=555)
    saved=f.client.get('/api/telegram/recipients').json()['recipients'][0]
    def use(): return f.client.post('/api/projects/2/telegram?source_key=iaa',json={'action':'USE_SAVED','recipient_id':saved['id']})
    f.counts['lost_reuse']=True
    assert use().status_code == 503
    assert f.client.get('/api/projects/2/telegram').json()['recipient'] is None
    result=use();assert result.status_code == 200,result.text
    assert result.json()['recipient']['project_id'] == 2 and result.json()['recipient']['binding_id'] == 'c'*32
    assert result.json()['recipient']['chat_id'] == 555 and not result.json()['delivery_enabled']
    assert f.client.get('/api/projects/1/telegram').json()['recipient']['binding_id'] == 'b'*32
    reuses=[op for op in f.counts['calls'] if op['action']=='reuse']
    assert len(reuses)==2 and reuses[0]['pairing_id']==reuses[1]['pairing_id']
    catalogue=f.client.get('/api/telegram/recipients').json()['recipients']
    assert len(catalogue)==1 and catalogue[0]['id']==saved['id']
    assert f.client.post('/api/projects/4/telegram',json={'action':'USE_SAVED','recipient_id':saved['id']}).status_code == 404
    assert f.client.post('/api/projects/2/telegram',json={'action':'USE_SAVED','recipient_id':'f'*32}).status_code == 409


def test_revoked_saved_recipient_is_not_assigned_and_catalogue_survives_disconnect(native_pairing):
    f=native_pairing;pid=f.command('START').json()['session']['pairing_id'];f.proposed(pid)
    f.command('CONFIRM',pairing_id=pid,chat_id=555)
    saved=f.client.get('/api/telegram/recipients').json()['recipients'][0]
    assert f.command('DISCONNECT',binding_id='b'*32).status_code == 200
    assert len(f.client.get('/api/telegram/recipients').json()['recipients']) == 1
    result=f.client.post('/api/projects/2/telegram',json={'action':'USE_SAVED','recipient_id':saved['id']})
    assert result.status_code == 409
    assert f.client.get('/api/projects/2/telegram').json()['recipient'] is None
    # A fresh explicit Start repairs the same saved contact; an old failed operation must not pin revoked consent.
    fresh=f.command('START').json()['session']['pairing_id'];f.proposed(fresh)
    f.command('CONFIRM',pairing_id=fresh,chat_id=555)
    assert f.client.get('/api/telegram/recipients').json()['recipients'][0]['id'] == saved['id']
    retry=f.client.post('/api/projects/2/telegram',json={'action':'USE_SAVED','recipient_id':saved['id']})
    assert retry.status_code == 200,retry.text


def test_catalogue_never_shares_another_local_owners_contacts(native_pairing):
    f=native_pairing;pid=f.command('START').json()['session']['pairing_id'];f.proposed(pid)
    f.command('CONFIRM',pairing_id=pid,chat_id=555)
    previous=app.dependency_overrides[require_user]
    app.dependency_overrides[require_user]=lambda: SimpleNamespace(id=2)
    try:
        assert f.client.get('/api/telegram/recipients').json()['recipients'] == []
    finally: app.dependency_overrides[require_user]=previous


def test_saved_catalogue_survives_project_deletion_and_upgrades_existing_binding(native_pairing):
    f=native_pairing;pid=f.command('START').json()['session']['pairing_id'];f.proposed(pid)
    f.command('CONFIRM',pairing_id=pid,chat_id=555)
    box=telegram_pairing.TelegramOutbox(f.root/'notifications'/'telegram-outbox.sqlite3')
    with box._db() as db: db.execute('DELETE FROM saved_recipients')
    # Upgrade from a build without a separate catalogue imports only the owned confirmed context.
    saved=f.client.get('/api/telegram/recipients').json()['recipients']
    assert len(saved)==1
    assert f.client.delete('/api/projects/1').status_code == 204
    assert f.client.get('/api/telegram/recipients').json()['recipients'] == saved


def test_confirmation_timeout_is_recoverable_after_reopening_without_republication(native_pairing):
    f=native_pairing;pid=f.command('START').json()['session']['pairing_id'];f.proposed(pid)
    f.counts['lost_confirm']=True
    result=f.command('CONFIRM',pairing_id=pid,chat_id=555)
    assert result.status_code == 503 and 'private-' not in result.text
    assert f.client.get('/api/projects/1/telegram').json()['recipient'] is None
    recovered=f.command('CHECK',pairing_id=pid).json()
    assert recovered['recipient']['binding_id'] == 'b'*32
    assert [op['action'] for op in f.counts['calls']] == ['start','confirm','status']


@pytest.mark.parametrize('fault',['wrong_project','wrong_url','group','redirect','network','limit'])
def test_bad_or_failed_relay_does_not_persist_a_fake_recipient(native_pairing,fault):
    f=native_pairing;f.counts['fault']=fault
    result=f.command('START')
    assert result.status_code in {429,502,503}
    assert 'private-' not in result.text and 'untrusted.fixture' not in result.text
    assert f.client.get('/api/projects/1/telegram').json()['recipient'] is None


def test_stale_window_cannot_confirm_a_different_pending_selection(native_pairing):
    f=native_pairing;old=f.command('START').json()['session']['pairing_id'];f.proposed(old)
    current=f.command('START').json()['session']['pairing_id']
    assert f.command('CONFIRM',pairing_id=old,chat_id=555).status_code == 409
    assert f.client.get('/api/projects/1/telegram').json()['session']['pairing_id'] == current


def test_cancel_replacement_preserves_prior_recipient_and_disconnect_is_explicit(native_pairing):
    f=native_pairing;pid=f.command('START').json()['session']['pairing_id'];f.proposed(pid)
    f.command('CONFIRM',pairing_id=pid,chat_id=555)
    pending=f.command('START').json()['session']['pairing_id']
    cancelled=f.command('CANCEL',pairing_id=pending).json()
    assert cancelled['recipient']['chat_id'] == 555
    assert f.command('DISCONNECT',binding_id='c'*32).status_code == 409
    disconnected=f.command('DISCONNECT',binding_id='b'*32).json()
    assert disconnected['recipient'] is None and disconnected['session'] is None


def test_mutation_cannot_supply_owner_or_secrets_or_another_projects_state(native_pairing):
    f=native_pairing
    for body in [{'action':'START','owner_id':2},{'action':'START','token':'private-secret'},
        {'action':'START','target_kind':[]},{'action':'START','target_kind':'supergroup'},
        {'action':'START','target_kind':'group','chat_id':-100555},
        {'action':'CONFIRM','pairing_id':'a'*32,'chat_id':True},{'action':'CONFIRM','pairing_id':[]}]:
        result=f.client.post('/api/projects/1/telegram',json=body)
        assert result.status_code == 422 and 'private-secret' not in result.text
    assert f.client.post('/api/projects/4/telegram',json={'action':'START'}).status_code == 404
    assert f.client.post('/api/projects/1/telegram?source_key=iaa',json={'action':'START'}).status_code == 404
    assert not f.counts['calls']


def test_real_csrf_guard_is_required_for_commands(auction_api,monkeypatch):
    client,_,_=auction_api
    monkeypatch.delenv('SEARCHCAR_TELEGRAM_RELAY_URL',raising=False)
    result=client.post('/api/projects/1/telegram',json={'action':'START'})
    assert result.status_code == 401


def test_disconnect_only_retires_this_projects_pending_queue_context(native_pairing):
    f=native_pairing;pid=f.command('START').json()['session']['pairing_id'];f.proposed(pid)
    f.command('CONFIRM',pairing_id=pid,chat_id=555)
    box=telegram_pairing.TelegramOutbox(f.root/'notifications'/'telegram-outbox.sqlite3')
    def enqueue(project):
        return box.enqueue(owner_id=1,project_id=project,bot_id=123456,
            destination={'chat_id':555,'chat_type':'private','title':'Alex'},notification_key='copart/lot/round/initial',now_ms=0)
    job=enqueue(1);enqueue(2)
    assert f.command('DISCONNECT',binding_id='b'*32).status_code == 200
    assert box.get(job['id'],owner_id=1)['active_project_ids'] == [2]
    assert box.claim_ready(0)['id'] == job['id']


def test_in_flight_publication_blocks_recipient_confirmation_before_relay_change(native_pairing):
    f=native_pairing;pid=f.command('START').json()['session']['pairing_id'];f.proposed(pid)
    f.command('CONFIRM',pairing_id=pid,chat_id=555)
    box=telegram_pairing.TelegramOutbox(f.root/'notifications'/'telegram-outbox.sqlite3')
    box.enqueue(owner_id=1,project_id=1,bot_id=123456,destination={'chat_id':555,'chat_type':'private'},
        notification_key='copart/lot/round/initial',now_ms=0)
    box.claim_ready(0)
    pending=f.command('START').json()['session']['pairing_id'];f.proposed(pending)
    count=len(f.counts['calls'])
    result=f.command('CONFIRM',pairing_id=pending,chat_id=555)
    assert result.status_code == 409 and result.json()['detail'] == 'telegram_recipient_busy'
    assert len(f.counts['calls']) == count
    saved=f.client.get('/api/telegram/recipients').json()['recipients'][0]
    assert f.command('USE_SAVED',recipient_id=saved['id']).status_code == 409
    assert len(f.counts['calls']) == count


def test_changed_device_binding_does_not_inherit_an_old_saved_recipient(native_pairing):
    f=native_pairing;pid=f.command('START').json()['session']['pairing_id'];f.proposed(pid)
    f.command('CONFIRM',pairing_id=pid,chat_id=555)
    path=f.root/'license'/'binding.json'
    binding=json.loads(path.read_text());binding['device_id']='30000000-0000-4000-8000-000000000001'
    _atomic_json(path,binding)
    count=len(f.counts['calls'])
    status=f.client.get('/api/projects/1/telegram').json()
    assert status['available'] and status['recipient'] is None and status['session'] is None
    assert len(f.counts['calls']) == count
