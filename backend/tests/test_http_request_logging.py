import asyncio
import logging

import pytest
from app.http_middleware import RequestLogMiddleware


@pytest.mark.parametrize('started', [False, True])
def test_cancelled_request_is_not_reported_as_http_500(caplog, started):
    async def app(scope, receive, send):
        if started:
            await send({'type':'http.response.start', 'status':200, 'headers':[]})
        raise asyncio.CancelledError('shutdown')

    async def receive(): return {'type':'http.disconnect'}
    async def send(message): pass
    caplog.set_level(logging.INFO)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(RequestLogMiddleware(app)({'type':'http','method':'GET','path':'/api/scans'}, receive, send))
    assert 'cancelled' in caplog.text
    assert 'status=500' not in caplog.text
    assert 'elapsed_ms=' in caplog.text


def test_real_server_error_is_still_logged_and_raised(caplog):
    async def app(scope, receive, send): raise ValueError('fixture failure')
    async def receive(): return {'type':'http.disconnect'}
    async def send(message): pass
    caplog.set_level(logging.INFO)
    with pytest.raises(ValueError):
        asyncio.run(RequestLogMiddleware(app)({'type':'http','method':'GET','path':'/api/scans'}, receive, send))
    assert 'request failed' in caplog.text
    assert 'status=500' in caplog.text
