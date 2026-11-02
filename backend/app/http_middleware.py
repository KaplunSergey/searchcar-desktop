"""Direct ASGI wrappers; no generator/TaskGroup frames around each HTTP call."""
import hmac
import logging
from threading import Lock
from time import monotonic
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestLogMiddleware:
    QUIET_POLLS = frozenset({
        '/api/health', '/api/desktop/runtime', '/desktop/update-check/consume',
        '/desktop/tray-status', '/api/projects', '/api/scans/active', '/api/settings',
    })

    def __init__(self, app: ASGIApp):
        self.app = app
        self._poll_lock = Lock()
        self._last_poll: dict[str, float] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        from .desktop_runtime import desktop_correlation_id
        logger = logging.getLogger('app.main')
        token = desktop_correlation_id.set(f'request-{uuid4().hex[:12]}')
        status = 500

        async def send_response(message: Message):
            nonlocal status
            if message['type'] == 'http.response.start':
                status = message['status']
            await send(message)

        try:
            await self.app(scope, receive, send_response)
        except Exception:
            logger.exception('Local API request failed: method=%s path=%s', scope['method'], scope['path'])
            raise
        finally:
            log_request = True
            if scope['method'] == 'GET' and scope['path'] in self.QUIET_POLLS and status == 200:
                now = monotonic()
                with self._poll_lock:
                    previous = self._last_poll.get(scope['path'])
                    log_request = previous is None or now - previous >= 30
                    if log_request:
                        self._last_poll[scope['path']] = now
            if log_request:
                logger.info('Local API request completed: method=%s path=%s status=%s', scope['method'], scope['path'], status)
            desktop_correlation_id.reset(token)


class DesktopSessionMiddleware:
    PUBLIC_PATHS = frozenset({
        '/desktop/test-diagnostics',
        '/api/health', '/desktop/bootstrap', '/desktop/shutdown', '/desktop/tray-status', '/desktop/active-scans', '/desktop/locale',
        '/desktop/scheduler/toggle', '/desktop/scheduler/resumed', '/desktop/update-check/consume',
        '/desktop/update/status', '/desktop/update/prepare', '/desktop/update/abort',
    })

    def __init__(self, app: ASGIApp, cookie_name: str, expected_cookie: str):
        self.app, self.cookie_name, self.expected_cookie = app, cookie_name, expected_cookie

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope['type'] != 'http' or scope['path'] in self.PUBLIC_PATHS:
            return await self.app(scope, receive, send)
        supplied = Request(scope).cookies.get(self.cookie_name, '')
        if not hmac.compare_digest(supplied, self.expected_cookie):
            return await JSONResponse(status_code=403, content={'detail': 'desktop_session_required'})(scope, receive, send)

        async def send_response(message: Message):
            if message['type'] == 'http.response.start':
                MutableHeaders(scope=message)['Cache-Control'] = 'no-store'
            await send(message)

        await self.app(scope, receive, send_response)
