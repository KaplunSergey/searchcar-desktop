"""Temporary preview-only telemetry. Removal points: docs/TEST_DIAGNOSTICS.md."""
import json
import logging
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
