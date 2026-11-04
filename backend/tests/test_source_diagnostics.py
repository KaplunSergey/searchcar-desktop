import json
import logging
import zipfile
from datetime import datetime, timezone

import pytest

from app import test_diagnostics
from app.desktop_diagnostics import build_support_report
from test_auction_list_adapters import adapters
from test_source_test_profile import isolated_environment, profile_queue


@pytest.mark.parametrize('source', ['copart', 'iaa'])
def test_source_failure_is_permanent_private_and_exported(monkeypatch, caplog, tmp_path, source):
    monkeypatch.setattr(test_diagnostics.settings, 'source_test_mode', False)
    from app.source_diagnostics import record_source_result
    caplog.set_level(logging.INFO)
    record_source_result(source, 'SOURCE_ACCESS_DENIED', {'blocked':False, 'document_loaded':True,
        'rows':[{'title':'PRIVATE TITLE'}]}, [dict(status=403, resource_type='script',
        path='/dist/runtime.secret.js?token=SECRET')], context={'attempt':'abcdef123456',
        'stage':'READING_RESULTS','elapsed_ms':1200,'pages':0,'expected_pages':1,'rows':0,
        'all_pages':False,'browser':'chrome','mode':'windowed','private':'SECRET'},
        browser_diagnostics={'script_errors':2, 'request_failures':[
            dict(resource_type='script', code='net::ERR_ABORTED'),
            dict(resource_type='PRIVATE',code='https://private/?token=SECRET')]})
    record = next(r for r in caplog.records if r.name == 'app.source_diagnostics')
    message = record.getMessage()
    assert len(message) < 2000
    assert 'SECRET' not in message and 'PRIVATE' not in message and 'secret.js' not in message
    payload = json.loads(message.split(': ',1)[1])
    assert payload['source'] == source and payload['state']['observed']
    assert payload['context']['attempt'] == 'abcdef123456'
    assert payload['resources'][0]['asset'] == 'runtime'
    assert payload['browser']['request_failures'][1] == {'type':'other','code':'OTHER'}
    logs = tmp_path/'logs'
    logs.mkdir()
    (logs/'searchcar-core.jsonl').write_text(json.dumps({'timestamp':datetime.now(timezone.utc).isoformat(),
        'level':'INFO','logger':record.name,'message':message})+'\n')
    report = build_support_report(logs, tmp_path/'reports', app_version='0.4.1',hours=1)
    with zipfile.ZipFile(tmp_path/'reports'/report.name) as archive:
        exported = json.loads(archive.read('logs.jsonl'))
    assert json.loads(exported['message'].split(': ',1)[1]) == payload


def test_diagnostics_fit_export_line_with_maximum_bounded_resources(caplog):
    from app.source_diagnostics import record_source_result
    caplog.set_level(logging.INFO)
    record_source_result('copart', 'SEARCH_VERIFICATION_RESTART_REQUIRED', {'ready':True},
        [dict(status=403, resource_type='stylesheet',path='/private/'+str(i)) for i in range(20)],
        context=dict(attempt='a'*12,stage='SEARCH_WINDOW_LOADING',elapsed_ms=86400000,
            pages=1000,expected_pages=1000,rows=100000,scan_id=123456,project_id=234567,
            browser='chromium',mode='windowed',all_pages=True),
        browser_diagnostics=dict(script_errors=1000,
            request_failures=[dict(resource_type='stylesheet',code='net::ERR_'+'A'*80)]*20,
            http_failures=[dict(resource_type='stylesheet',status=503,site_resource=True)]*20))
    assert len(caplog.records[-1].getMessage()) <= 2000


def test_successful_search_has_one_linked_start_and_result(adapters, caplog):
    from app.sources.registry import get_source
    from test_auction_list_adapters import Page, ROWS, snapshot
    caplog.set_level(logging.INFO, logger='app.source_diagnostics')
    source = get_source('copart')
    source.collect_search_pages(Page([snapshot('copart', [ROWS['copart'][0]], total=1)]),
        'https://www.copart.com/lotSearchResults?searchCriteria=%7B%7D',
        diagnostic_context={'scan_id':123,'project_id':456})
    events = [json.loads(r.getMessage().split(': ',1)[1]) for r in caplog.records if r.name=='app.source_diagnostics']
    assert [event['code'] for event in events] == ['STARTED','SUCCESS']
    assert events[0]['context']['attempt'] == events[1]['context']['attempt']
    assert events[1]['context']['scan_id'] == 123 and events[1]['context']['project_id'] == 456
    assert events[1]['context']['pages'] == 1 and events[1]['context']['rows'] == 1


def test_broken_diagnostic_writer_does_not_replace_original_failure(adapters, monkeypatch):
    from app.sources.registry import get_source
    from app.sources.auction_lists import AuctionListError
    from test_auction_list_adapters import Page, snapshot
    def broken_writer(*args, **kwargs):
        raise OSError('log unavailable')
    def denied(*args, **kwargs):
        raise AuctionListError('SOURCE_ACCESS_DENIED', 'original failure')
    monkeypatch.setattr('app.source_diagnostics.record_source_result', broken_writer)
    source = get_source('iaa')
    monkeypatch.setattr(source, 'prepare_page', denied)
    with pytest.raises(AuctionListError) as error:
        source.collect_search_pages(Page([snapshot('iaa', [], total=0)]), 'https://www.iaai.com/Search?url=x')
    assert error.value.code == 'SOURCE_ACCESS_DENIED'
    assert str(error.value) == 'original failure'
