import asyncio

import httpx
import pytest

from app.data_providers import news

STAMP = "2026-10-07T10:00:00+08:00"


def test_eastmoney_normalizes_actual_fields_without_date_invention():
    result = news.parse_feed("eastmoney", {"data": {"fastNewsList": [
        {"title": "<b>公司公告</b>", "summary": "&lt;文字&gt;", "showTime": "2026-10-07 09:45:00", "code": "202610071234"},
        {"title": "没有发布时间", "showTime": "2026-10-07"},
    ]}}, STAMP)
    assert result.status == "ok"
    assert len(result.items) == 1
    row = result.items[0]
    assert row.title == "公司公告"
    assert row.summary == "<文字>"
    assert row.published_at == "2026-10-07T09:45:00+08:00"
    assert row.url == "https://finance.eastmoney.com/a/202610071234.html"
    assert result.fetched_at != row.published_at


def test_cls_epoch_is_beijing_time_independent_of_host():
    result = news.parse_feed("cls", {"data": {"roll_data": [
        {"title": "", "content": "【实际标题】具体新闻", "ctime": 1791338400, "id": 123},
    ]}}, STAMP)
    assert result.items[0].title == "实际标题"
    assert result.items[0].published_at == "2026-10-07T10:00:00+08:00"
    assert result.items[0].url == "https://www.cls.cn/detail/123"


@pytest.mark.parametrize("value", [None, True, "", "2026-10-07", "invalid", "99:99", "1900-01-01 10:00:00"])
def test_missing_or_invalid_publication_is_not_today(value):
    assert news.publication(value) is None


@pytest.mark.parametrize("source,field", [("cls", "roll_data"), ("eastmoney", "fastNewsList")])
def test_empty_broken_and_all_invalid_have_distinct_states(source, field):
    assert news.parse_feed(source, {"data": {field: []}}, STAMP).status == "empty"
    assert news.parse_feed(source, {"data": {}}, STAMP).status == "unavailable"
    assert news.parse_feed(source, {"data": {field: [{"title": "No timestamp"}]}}, STAMP).status == "unavailable"


def test_count_and_links_are_bounded():
    row = {"title": "标题", "summary": "内容", "showTime": "2026-10-07 10:00:00", "code": "javascript:alert(1)"}
    result = news.parse_feed("eastmoney", {"data": {"fastNewsList": [row] * 300}}, STAMP)
    assert len(result.items) == 200
    assert result.items[0].url is None


def test_transport_error_does_not_leak_proxy_credentials(monkeypatch):
    original = httpx.AsyncClient
    def transport(request):
        raise httpx.ConnectError("https://user:private@proxy/secret", request=request)
    monkeypatch.setattr(news.httpx, "AsyncClient", lambda **kw: original(**kw, trust_env=False, transport=httpx.MockTransport(transport)))
    result = asyncio.run(news.fetch_news_feed("eastmoney"))
    assert result.status == "unavailable"
    assert "private" not in result.reason


def test_fetch_uses_normalizer(monkeypatch):
    original = httpx.AsyncClient
    def transport(request):
        assert request.url.params["pageSize"] == "200"
        assert request.url.params["req_trace"].isdigit()
        return httpx.Response(200, json={"data": {"fastNewsList": []}})
    monkeypatch.setattr(news.httpx, "AsyncClient", lambda **kw: original(**kw, trust_env=False, transport=httpx.MockTransport(transport)))
    assert asyncio.run(news.fetch_news_feed("eastmoney")).status == "empty"


def test_unknown_source_rejected():
    with pytest.raises(ValueError):
        news.endpoint("any-url")
