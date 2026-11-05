import logging
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.auth import require_csrf
from app import desktop_lifecycle as lifecycle


def test_ordinary_lifecycle_is_authenticated_rate_limited_and_has_no_free_text(monkeypatch, caplog):
    app = FastAPI()
    app.include_router(lifecycle.router)
    monkeypatch.setattr(lifecycle, '_last', {})
    caplog.set_level(logging.INFO)
    data = {'event':'menu-resume', 'focused':True, 'hidden':False}
    with TestClient(app) as client:
        assert client.post('/api/desktop/diagnostics/lifecycle',json=data).status_code in (401,403)
        app.dependency_overrides[require_csrf] = lambda: object()
        for _ in range(3):
            assert client.post('/api/desktop/diagnostics/lifecycle',json=data).status_code == 204
        assert caplog.text.count('Desktop lifecycle:') == 1
        for extra in [{'token':'secret'}, {'elapsed_ms':3600001}, {'pending_queries':1001}, {'event':'private text'}]:
            assert client.post('/api/desktop/diagnostics/lifecycle',json={**data,**extra}).status_code == 422
    with pytest.raises(ValidationError):
        lifecycle.LifecycleEvent(**data, elapsed_ms=-1)
