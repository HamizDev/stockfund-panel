import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.data_providers.news import NewsFeed, NewsRecord
from app.services import news_service as mod

STAMP = '2026-10-23T15:00:00+08:00'


def set_news(service, text='工商银行公告回购'):
    item = NewsRecord('cls', '123', text, text, STAMP, None)
    service.feeds = {'cls': NewsFeed('cls', 'ok', [item], STAMP)}


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    calls = []
    original = mod.associate
    def counted(text, instruments):
        calls.append(text)
        return original(text, instruments)
    monkeypatch.setattr(mod, 'associate', counted)
    service = mod.NewsService(tmp_path)
    set_news(service)
    instruments = [{'symbol': '601398.SH', 'name': '工商银行', 'asset_type': 'stock'}]
    return service, instruments, calls


def test_repeat_feed_reuses_associations_but_keeps_related_scope_fresh(inputs):
    service, instruments, calls = inputs
    first = service.feed(instruments, [])
    again = service.feed([dict(row) for row in instruments], ['601398.SH'])
    assert len(calls) == 1
    assert first['items'] == again['items']
    assert again['related_symbols'] == ['601398.SH'] and first['related_symbols'] == []


def test_corrected_article_recomputes_associations(inputs):
    service, instruments, calls = inputs
    assert service.feed(instruments, [])['items'][0]['associations']
    set_news(service, '某行业常规会议')
    assert service.feed(instruments, [])['items'][0]['associations'] == []
    assert len(calls) == 2


def test_in_place_instrument_name_change_invalidates_cached_matches(inputs):
    service, instruments, calls = inputs
    assert service.feed(instruments, [])['items'][0]['associations']
    instruments[0]['name'] = '更新后的名称'
    assert service.feed(instruments, [])['items'][0]['associations'] == []
    assert len(calls) == 2


def test_mutating_response_does_not_modify_cached_association(inputs):
    service, instruments, calls = inputs
    result = service.feed(instruments, [])
    result['items'][0]['associations'][0]['name'] = 'caller changed this'
    assert service.feed(instruments, [])['items'][0]['associations'][0]['name'] == '工商银行'
    assert len(calls) == 1


def test_association_cache_remains_bounded(inputs, monkeypatch):
    service, instruments, _ = inputs
    monkeypatch.setattr(mod, 'MAX_ASSOCIATION_CACHE', 3, raising=False)
    for index in range(7):
        set_news(service, f'工商银行公告回购{index}')
        service.feed(instruments, [])
    assert len(service._association_cache) <= 3


def test_old_inflight_context_cannot_pollute_updated_instrument_cache(inputs, monkeypatch):
    service, instruments, _ = inputs
    entered, release = threading.Event(), threading.Event()
    original = mod.associate
    def blocked(text, rows):
        if rows[0]['name'] == '工商银行':
            entered.set()
            assert release.wait(3)
        return original(text, rows)
    monkeypatch.setattr(mod, 'associate', blocked)
    updated = [{'symbol': '601398.SH', 'name': '更新后的名称', 'asset_type': 'stock'}]
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(service.feed, instruments, [])
        try:
            assert entered.wait(3)
            assert service.feed(updated, [])['items'][0]['associations'] == []
        finally:
            release.set()
        assert old.result()['items'][0]['associations'][0]['name'] == '工商银行'
    assert service.feed(updated, [])['items'][0]['associations'] == []
