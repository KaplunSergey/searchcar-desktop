"""Small, permanent lifecycle evidence; only enums, booleans and bounded counters."""
import json
import logging
from threading import Lock
from time import monotonic
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from .auth import require_csrf


class LifecycleEvent(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    event: Literal['ui-mounted', 'heartbeat', 'menu-resume', 'recheck-start', 'recheck-finish', 'recheck-error']
    focused: bool
    hidden: bool
    elapsed_ms: int = Field(default=0, ge=0, le=3600000)
    pending_queries: int = Field(default=0, ge=0, le=1000)
    error_queries: int = Field(default=0, ge=0, le=1000)


router = APIRouter()
_lock = Lock()
_last: dict[str, float] = {}


@router.post('/api/desktop/diagnostics/lifecycle', status_code=204)
def record_lifecycle(event: LifecycleEvent, _user=Depends(require_csrf)):
    now = monotonic()
    with _lock:
        if now - _last.get(event.event, -100) < 1:
            return
        _last[event.event] = now
    logging.getLogger(__name__).info('Desktop lifecycle: %s', json.dumps(event.model_dump(), sort_keys=True))
