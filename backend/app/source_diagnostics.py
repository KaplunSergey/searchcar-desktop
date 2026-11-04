"""Permanent bounded source diagnostics in the existing rotating support log."""
import hashlib
import json
import logging
import re


def record_source_result(source, code, state, failures, *, context=None, browser_diagnostics=None):
    if source not in ('copart', 'iaa'):
        return
    resources = []
    resource_types = ('document', 'script', 'stylesheet', 'xhr', 'fetch')
    for failure in failures[-4:]:
        path = str(failure.get('path', '')).split('?')[0].split('#')[0]
        asset = re.search(r'(?:^|/)dist/(runtime|polyfills|main)[.-]', path)
        status = failure.get('status')
        resources.append(dict(status=status if type(status) is int and 100 <= status <= 599 else None,
            type=failure.get('resource_type') if failure.get('resource_type') in resource_types else 'other',
            asset=asset.group(1) if asset else 'other', path_hash=hashlib.sha256(path.encode()).hexdigest()[:16]))
    flags = {key: state.get(key) is True for key in ('ready', 'blocked', 'manual_required', 'consent_required', 'document_loaded')}
    flags['observed'] = bool(state)
    context = context or {}
    safe_context = {}
    for key in ('elapsed_ms', 'pages', 'expected_pages', 'rows', 'scan_id', 'project_id'):
        value = context.get(key)
        if type(value) is int:
            safe_context[key] = min(86400000, max(0, value))
    for key, allowed in dict(browser=('chrome','msedge','chromium','unknown'),
                             mode=('windowed','headless','unknown'),
                             stage=('OPENING_SEARCH','READING_RESULTS','NEXT_PAGE','SEARCH_WINDOW_LOADING')).items():
        if context.get(key) in allowed:
            safe_context[key] = context[key]
    attempt = context.get('attempt', '')
    if isinstance(attempt, str) and re.fullmatch(r'[a-f0-9]{12}', attempt):
        safe_context['attempt'] = attempt
    if type(context.get('all_pages')) is bool:
        safe_context['all_pages'] = context['all_pages']
    browser = browser_diagnostics or {}
    errors = browser.get('script_errors', 0)
    requests = []
    for failure in browser.get('request_failures', [])[-4:]:
        error = failure.get('code', '')
        requests.append(dict(type=failure.get('resource_type') if failure.get('resource_type') in resource_types else 'other',
            code=error if isinstance(error, str) and re.fullmatch(r'net::ERR_[A-Z0-9_]{1,80}', error) else 'OTHER'))
    http = []
    for failure in browser.get('http_failures', [])[-4:]:
        status = failure.get('status')
        http.append(dict(status=status if type(status) is int and 100 <= status <= 599 else None,
            type=failure.get('resource_type') if failure.get('resource_type') in resource_types else 'other',
            site=failure.get('site_resource') is True))
    payload = dict(source=source, code=code if isinstance(code, str) and re.fullmatch(r'[A-Z_]{1,64}', code) else 'READ_ERROR',
        state=flags, rows=min(len(state.get('rows') or []), 1000), resources=resources,
        context=safe_context, browser=dict(script_errors=min(1000, max(0, errors)) if type(errors) is int else 0,
                                         request_failures=requests, http_failures=http))
    logging.getLogger(__name__).info('Source diagnostic: %s', json.dumps(payload, sort_keys=True, separators=(',', ':')))
