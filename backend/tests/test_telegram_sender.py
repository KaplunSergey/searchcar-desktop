"""The native HTTP boundary uses mock transports and fresh signed envelopes only."""
import base64
import json
import httpx
from httpx import Client as RealClient
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from app.desktop_license import canonical_json
from app import telegram_sender, telegram_pairing
from test_telegram_pairing import native_pairing
from test_auction_api import auction_api

@pytest.mark.parametrize('reply,expected',[
    ('sent','SENT'),('limit','RATE_LIMITED'),('lost','UNKNOWN'),('redirect','UNKNOWN'),('huge','UNKNOWN'),
    ('wrong_attempt','UNKNOWN'),('bad_message_id','UNKNOWN'),('unknown_error','UNKNOWN'),('revoked','FAILED'),('offline','RETRY_LATER'),
    ('chat_revoked','FAILED'),('chat_offline','RETRY_LATER'),('chat_limit','RATE_LIMITED'),
])
def test_signed_one_attempt_never_retries_or_exposes_remote_secrets(native_pairing,monkeypatch,reply,expected):
    f=native_pairing;config,_=telegram_pairing.configuration();calls=[]
    job=dict(owner_id=1,bot_id=123456,attempt_id='a'*32)
    def relay(request):
        calls.append(request);assert str(request.url)=='https://relay.fixture.test/v1/send'
        value=json.loads(request.content);proof=value.pop('proof')
        key=Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(value['device']['public_key']+'=='))
        key.verify(base64.urlsafe_b64decode(proof+'=='),('SEARCHCAR-DEVICE-REQUEST-V1\n'+canonical_json(value)).encode())
        assert value['audience']=='searchcar-telegram-delivery-v1'
        assert value['telegram']['owner_id']==1 and value['telegram']['project_id']==1
        if reply=='lost':raise httpx.ReadTimeout('PRIVATE TRANSPORT DETAILS',request=request)
        if reply=='offline':raise httpx.ConnectError('PRIVATE NETWORK DETAILS',request=request)
        if reply=='redirect':return httpx.Response(302,headers={'location':'https://forbidden.test'})
        if reply=='huge':return httpx.Response(200,content=b'x'*20000)
        if reply=='revoked':return httpx.Response(403,json=dict(ok=False,error=dict(code='RECIPIENT_NOT_AUTHORIZED',description='PRIVATE REMOTE DETAILS')))
        if reply=='limit':return httpx.Response(429,json=dict(ok=False,error=dict(code='PAIRING_RATE_LIMITED')))
        if reply=='chat_limit':return httpx.Response(429,json=dict(ok=False,error=dict(code='CHAT_VERIFICATION_RATE_LIMITED'),retry_after=37))
        if reply=='chat_revoked':return httpx.Response(403,json=dict(ok=False,error=dict(code='CHAT_PERMISSION_REQUIRED')))
        if reply=='chat_offline':return httpx.Response(503,json=dict(ok=False,error=dict(code='CHAT_VERIFICATION_UNAVAILABLE')))
        state=dict(attempt_id=job['attempt_id'],state='SENT',message_id=123,retry_after=None,error_code=None)
        if reply=='wrong_attempt':state['attempt_id']='b'*32
        if reply=='bad_message_id':state['message_id']=True
        if reply=='unknown_error':state.update(state='FAILED',message_id=None,error_code='PRIVATE REMOTE DETAILS')
        return httpx.Response(200,json=dict(ok=True,data=state))
    monkeypatch.setattr(telegram_sender.httpx,'Client',lambda **options:RealClient(transport=httpx.MockTransport(relay),**options))
    result=telegram_sender.send_once(config,job,1,'b'*32,dict(text='Vehicle',photo=None,entities=[]))
    assert result['state']==expected and len(calls)==1
    if reply=='chat_limit':assert result['retry_after']==37
    assert 'PRIVATE' not in json.dumps(result) and 'bot_token' not in calls[0].content.decode()
