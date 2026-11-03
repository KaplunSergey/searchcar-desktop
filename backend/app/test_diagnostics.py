"""Temporary preview-only telemetry. Removal points: docs/TEST_DIAGNOSTICS.md."""
import hashlib
import json
import logging
import re
from threading import Lock
from time import monotonic
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .auth import require_csrf
from .database import settings


class TestEvent(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    event: Literal['heartbeat', 'focus', 'blur', 'visibility', 'menu-key', 'menu-resume', 'timer-gap', 'native']
    focused: bool
    hidden: bool
    lag_ms: int = Field(default=0, ge=0, le=3600000)
    native_age_ms: int = Field(default=0, ge=0, le=3600000)
    pending_queries: int = Field(default=0, ge=0, le=1000)
    error_queries: int = Field(default=0, ge=0, le=1000)


_lock = Lock()
_last: dict[str, float] = {}


def record_event(event: TestEvent):
    if not settings.source_test_mode:
        raise HTTPException(404, 'test_diagnostics_disabled')
    now = monotonic()
    with _lock:
        if now - _last.get(event.event, -100) < 1:
            return
        _last[event.event] = now
    logging.getLogger(__name__).info('Test diagnostic: %s', json.dumps(event.model_dump(), sort_keys=True))


router = APIRouter()


@router.post('/api/desktop/diagnostics/test-event', status_code=204)
def ui_event(event: TestEvent, _user=Depends(require_csrf)):
    record_event(event)


def record_source_failure(source, code, state, failures):
    if not settings.source_test_mode:
        return
    resources = []
    for failure in failures[-6:]:
        path = str(failure.get('path', '')).split('?')[0].split('#')[0]
        asset = re.search(r'(?:^|/)dist/(runtime|polyfills|main)[.-]', path)
        resources.append(dict(status=failure.get('status'), type=failure.get('resource_type'),
            asset=asset.group(1) if asset else 'other', path_hash=hashlib.sha256(path.encode()).hexdigest()[:16]))
    flags = {key: state.get(key) is True for key in ('ready', 'blocked', 'manual_required', 'consent_required', 'document_loaded')}
    flags['observed'] = bool(state)
    logging.getLogger(__name__).info('Test source diagnostic: %s', json.dumps(dict(
        source=source, code=code, state=flags, rows=min(len(state.get('rows') or []), 1000), resources=resources), sort_keys=True))
