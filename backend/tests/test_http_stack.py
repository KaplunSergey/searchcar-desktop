"""HTTP guard/logging contracts, including streaming and repeated failures."""
import asyncio
import logging

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app import main
from app.desktop_runtime import DESKTOP_COOKIE_NAME, _session_cookie, create_desktop_app, desktop_correlation_id


def application():
    app = FastAPI()
    for middleware in reversed(main.app.user_middleware):
        app.add_middleware(middleware.cls, **middleware.kwargs)

    @app.get('/ok')
    async def ok():
        return {'request_id': desktop_correlation_id.get()}

    @app.get('/broken')
    async def broken():
        raise RuntimeError('fixture request failure')

    @app.get('/stream')
    async def stream():
        async def chunks():
            yield 'data: first\n\n'
            await asyncio.sleep(0)
            yield f'data: {desktop_correlation_id.get()}\n\n'
        return StreamingResponse(chunks(), media_type='text/event-stream')

    return app


def test_request_failure_does_not_break_the_next_request_or_leak_context(caplog):
    caplog.set_level(logging.INFO, logger='app.main')
    with TestClient(application(), raise_server_exceptions=False) as client:
        ids = []
        for _ in range(20):
            assert client.get('/broken?token=must-not-log').status_code == 500
            response = client.get('/ok')
            assert response.status_code == 200
            ids.append(response.json()['request_id'])
        assert len(set(ids)) == len(ids)
        assert all(value.startswith('request-') for value in ids)
        stream = client.get('/stream')
        assert stream.status_code == 200
        assert stream.text.startswith('data: first\n\ndata: request-')
    assert desktop_correlation_id.get() == 'desktop-process'
    assert 'must-not-log' not in caplog.text
    assert 'method=GET path=/broken status=500' in caplog.text
    assert 'method=GET path=/stream status=200' in caplog.text


def test_desktop_guard_still_protects_reads_and_marks_streams_no_store(tmp_path, monkeypatch):
    app = application()
    monkeypatch.setattr(main, 'app', app)
    secret = 'fixture-desktop-secret-0123456789abcdef'
    frontend = tmp_path / 'frontend'
    frontend.mkdir()
    (frontend / 'index.html').write_text('<title>fixture</title>')
    create_desktop_app(frontend, secret)
    with TestClient(app) as client:
        assert client.get('/ok').status_code == 403
        client.cookies.set(DESKTOP_COOKIE_NAME, 'wrong')
        assert client.get('/stream').status_code == 403
        client.cookies.set(DESKTOP_COOKIE_NAME, _session_cookie(secret))
        response = client.get('/ok')
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        stream = client.get('/stream')
        assert stream.status_code == 200
        assert stream.headers['cache-control'] == 'no-store'
        for enabled in ['0','1']:
            monkeypatch.setenv('SEARCHCAR_TELEGRAM_DELIVERY_ENABLED',enabled)
            runtime=client.get('/api/desktop/runtime')
            assert runtime.status_code==200
            assert runtime.json()['telegram_delivery_enabled'] is (enabled=='1')
