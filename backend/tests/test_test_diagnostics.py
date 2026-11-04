import json
import logging

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from app import test_diagnostics as diagnostics
from app.source_diagnostics import record_source_result


def test_app_exposes_preview_endpoint_with_csrf_and_disabled_in_release(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.auth import require_csrf
    payload = dict(event='menu-resume', focused=True, hidden=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post('/api/desktop/diagnostics/test-event', json=payload).status_code in (401, 403)
        app.dependency_overrides[require_csrf] = lambda: object()
        try:
            monkeypatch.setattr(diagnostics.settings, 'source_test_mode', True)
            assert client.post('/api/desktop/diagnostics/test-event', json=payload).status_code == 204
            monkeypatch.setattr(diagnostics.settings, 'source_test_mode', False)
            assert client.post('/api/desktop/diagnostics/test-event', json=payload).status_code == 404
        finally:
            app.dependency_overrides.pop(require_csrf, None)


def test_release_diagnostics_are_disabled(monkeypatch, caplog):
    monkeypatch.setattr(diagnostics.settings, 'source_test_mode', False)
    with pytest.raises(HTTPException) as error:
        diagnostics.record_event(diagnostics.TestEvent(event='native', focused=True, hidden=False))
    assert error.value.status_code == 404
    record_source_result('copart', 'FAIL', {}, [])
    assert 'Test diagnostic' not in caplog.text


def test_source_failure_remains_available_outside_preview(monkeypatch, caplog):
    monkeypatch.setattr(diagnostics.settings, 'source_test_mode', False)
    caplog.set_level(logging.INFO)
    record_source_result('copart', 'SOURCE_ACCESS_DENIED', {}, [])
    assert 'Source diagnostic:' in caplog.text


def test_preview_telemetry_is_bounded_and_rejects_free_text(monkeypatch, caplog):
    monkeypatch.setattr(diagnostics.settings, 'source_test_mode', True)
    monkeypatch.setattr(diagnostics, '_last', {})
    caplog.set_level(logging.INFO)
    event = diagnostics.TestEvent(event='native', focused=True, hidden=False)
    diagnostics.record_event(event)
    diagnostics.record_event(event)
    assert caplog.text.count('Test diagnostic:') == 1
    with pytest.raises(ValidationError):
        diagnostics.TestEvent(event='focus', focused=True, hidden=False, token='private')
    record_source_result('copart', 'SOURCE_ACCESS_DENIED', {'ready':False,'blocked':False}, [
        dict(status=403,resource_type='script',path='dist/runtime.abc.js?token=private')])
    assert '"asset":"runtime"' in caplog.text
    assert '"blocked":false' in caplog.text
    assert 'private' not in caplog.text and 'runtime.abc' not in caplog.text
