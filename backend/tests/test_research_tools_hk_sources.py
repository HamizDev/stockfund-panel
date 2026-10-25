# ruff: noqa: RUF001 -- Native Chinese research text.
from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from app.custom.research_tools import hk_sources


def eastmoney_page(rows: list[dict], *, count: int | None = None, pages: int = 1) -> dict:
    """Use the public Eastmoney datacenter envelope and pagination fields."""
    return {
        'version': '2026',
        'success': True,
        'message': 'ok',
        'code': 0,
        'result': {
            'pages': pages,
            'data': rows,
            'count': len(rows) if count is None else count,
        },
    }


def http_response(url: str, payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request('GET', url))


def install_get(monkeypatch: pytest.MonkeyPatch, handler: Callable) -> list[dict]:
    calls: list[dict] = []

    def fake_get(url: str, **kwargs):
        calls.append({'url': url, **kwargs})
        return handler(url, **kwargs)

    monkeypatch.setattr(hk_sources.httpx, 'get', fake_get)
    return calls


def test_southbound_flow_converts_million_hkd_and_percent_to_decimal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {
        'TRADE_DATE': '2026-10-23 00:00:00',
        'NET_DEAL_AMT': '1250.75',
        'BUY_AMT': '800.25',
        'SELL_AMT': '500.50',
        'DEAL_AMT': '1300.75',
        'DEAL_NUM': '58231',
        'ACCUM_DEAL_AMT': '902100.00',
        'INDEX_CLOSE_PRICE': '18300.15',
        'INDEX_CHANGE_RATE': '1.25',
    }
    calls = install_get(
        monkeypatch,
        lambda url, **kwargs: http_response(url, eastmoney_page([row])),
    )

    result = hk_sources.fetch('southbound_flow')

    assert result['status'] == 'ok'
    assert result['as_of'] == '2026-10-23'
    assert result['fetched_at'] != result['as_of']
    assert result['items'] == [{
        'date': '2026-10-23',
        'net_inflow_hkd': 1_250_750_000,
        'buy_hkd': 800_250_000,
        'sell_hkd': 500_500_000,
        'turnover_hkd': 1_300_750_000,
        'trade_count': 58231,
        'cumulative_net_inflow_hkd': 902_100_000_000,
        'hsi_close': 18300.15,
        'hsi_change_ratio': 0.0125,
    }]
    assert 'million HKD' in result['unit']['amounts']
    assert len(calls) == 1
    assert calls[0]['url'] == hk_sources.EASTMONEY_URL
    assert calls[0]['timeout'] == 8.0
    assert calls[0]['params']['pageSize'] == '100'
    assert calls[0]['params']['filter'] == '(MUTUAL_TYPE="006")'


def test_holding_requires_five_digit_hk_symbol_and_keeps_unknown_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = install_get(monkeypatch, lambda *_args, **_kwargs: pytest.fail('must not request'))

    invalid = hk_sources.fetch('southbound_holding', '700.HK')

    assert invalid['status'] == 'empty'
    assert invalid['gaps'] == ['invalid_symbol_format']
    assert calls == []

    calls = install_get(
        monkeypatch,
        lambda url, **kwargs: http_response(
            url,
            {'success': False, 'code': 9201, 'message': 'no records'},
        ),
    )
    unknown = hk_sources.fetch('southbound_holding', '99999.HK')

    assert unknown['status'] == 'empty'
    assert unknown['items'] == []
    assert calls[0]['params']['filter'] == '(SECUCODE="99999.HK")(MUTUAL_TYPE="002")'


def test_holding_maps_source_percent_to_decimal_and_keeps_nulls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {
        'TRADE_DATE': '2026-09-30 00:00:00',
        'SECUCODE': '00700.HK',
        'SECURITY_NAME': '腾讯控股',
        'HOLD_SHARES': '1079451366',
        'HOLD_MARKET_CAP': '70000000000.5',
        'HOLD_SHARES_RATIO': '11.86',
        'HOLD_SHARES_CHANGE': None,
        'CLOSE_PRICE': '600.00',
    }
    install_get(monkeypatch, lambda url, **kwargs: http_response(url, eastmoney_page([row])))

    result = hk_sources.fetch('southbound_holding', '00700.HK')

    assert result['status'] == 'partial'
    assert result['items'][0]['held_shares'] == 1_079_451_366
    assert result['items'][0]['holding_ratio'] == pytest.approx(0.1186)
    assert result['items'][0]['market_value_hkd'] == pytest.approx(70_000_000_000.5)
    assert result['items'][0]['shares_change'] is None
    assert result['items'][0]['symbol'] == '00700.HK'
    assert 'missing_hold_shares_change' in result['gaps']


def test_dividend_plan_text_preserves_original_currency_without_number_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_plan = '每股派港币5.30元（末期股息）'
    row = {
        'SECURITY_CODE': '00005',
        'NOTICE_DATE': '2026-03-20 00:00:00',
        'YEAR': '2025',
        'REPORT_TYPE': '年度分配',
        'PLAN_EXPLAIN': source_plan,
        'EX_DIVIDEND_DATE': '2026-06-10 00:00:00',
        'DIVIDEND_DATE': '2026-06-25 00:00:00',
        'TRANSFER_END_DATE': '2026-06-12 00:00:00',
    }
    calls = install_get(
        monkeypatch,
        lambda url, **kwargs: http_response(url, eastmoney_page([row])),
    )

    result = hk_sources.fetch('dividends', '00005.HK')

    assert result['status'] == 'ok'
    assert result['as_of'] == '2026-03-20'
    assert result['items'][0]['plan_text'] == source_plan
    assert result['items'][0]['symbol'] == '00005.HK'
    assert 'amount' not in result['items'][0]
    assert calls[0]['params']['reportName'] == 'RPT_HKF10_MAIN_DIVBASIC'
    assert calls[0]['params']['filter'] == '(SECURITY_CODE="00005")(IS_BFP="0")'


def test_dividend_missing_plan_is_partial_and_not_coerced(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {
        'SECURITY_CODE': '00005',
        'NOTICE_DATE': '2026-03-20',
        'YEAR': '2025',
        'REPORT_TYPE': '年度分配',
        'EX_DIVIDEND_DATE': None,
        'DIVIDEND_DATE': None,
        'TRANSFER_END_DATE': None,
    }
    install_get(monkeypatch, lambda url, **kwargs: http_response(url, eastmoney_page([row])))

    result = hk_sources.fetch('dividends', '00005.HK')

    assert result['status'] == 'partial'
    assert result['items'][0]['plan_text'] is None
    assert 'missing_plan_text' in result['gaps']
    assert result['items'][0]['ex_date'] is None


@pytest.mark.parametrize(
    ('dataset', 'symbol', 'row'),
    [
        pytest.param(
            'southbound_holding', '00700.HK',
            {'SECUCODE': '00005.HK', 'TRADE_DATE': '2026-09-30'},
            id='holding-wrong-code',
        ),
        pytest.param(
            'dividends', '00005.HK',
            {'SECURITY_CODE': '00001', 'NOTICE_DATE': '2026-03-20'},
            id='dividend-wrong-code',
        ),
    ],
)
def test_stock_specific_datasets_reject_mismatched_source_identity(
    monkeypatch: pytest.MonkeyPatch, dataset: str, symbol: str, row: dict,
) -> None:
    calls = install_get(
        monkeypatch,
        lambda url, **kwargs: http_response(url, eastmoney_page([row])),
    )

    result = hk_sources.fetch(dataset, symbol)

    assert result['status'] == 'partial'
    assert result['items'] == []
    assert result['gaps'] == ['source_symbol_mismatch_or_missing']
    assert len(calls) == 1


@pytest.mark.parametrize(
    ('dataset', 'symbol', 'row'),
    [
        pytest.param(
            'southbound_holding', '00700.HK',
            {'TRADE_DATE': '2026-09-30'},
            id='holding-no-identity',
        ),
        pytest.param(
            'dividends', '00005.HK',
            {'NOTICE_DATE': '2026-03-20'},
            id='dividend-no-identity',
        ),
    ],
)
def test_stock_specific_datasets_reject_missing_source_identity(
    monkeypatch: pytest.MonkeyPatch, dataset: str, symbol: str, row: dict,
) -> None:
    install_get(
        monkeypatch,
        lambda url, **kwargs: http_response(url, eastmoney_page([row])),
    )

    result = hk_sources.fetch(dataset, symbol)

    assert result['status'] == 'partial'
    assert result['items'] == []
    assert result['gaps'] == ['source_symbol_mismatch_or_missing']


def test_flow_empty_response_is_not_a_zero_valued_record(monkeypatch: pytest.MonkeyPatch) -> None:
    install_get(
        monkeypatch,
        lambda url, **kwargs: http_response(
            url,
            {'success': False, 'code': 9201, 'message': 'empty'},
        ),
    )

    result = hk_sources.fetch('southbound_flow')

    assert result['status'] == 'empty'
    assert result['items'] == []
    assert result['as_of'] is None


def test_flow_missing_field_is_partial_and_does_not_fill_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {
        'TRADE_DATE': '2026-10-23',
        'NET_DEAL_AMT': '0',
        'BUY_AMT': '0',
        'DEAL_AMT': '0',
        'DEAL_NUM': '0',
        'ACCUM_DEAL_AMT': '0',
        'INDEX_CLOSE_PRICE': '0',
        'INDEX_CHANGE_RATE': '0',
    }
    install_get(monkeypatch, lambda url, **kwargs: http_response(url, eastmoney_page([row])))

    result = hk_sources.fetch('southbound_flow')

    assert result['status'] == 'partial'
    assert result['items'][0]['sell_hkd'] is None
    assert result['items'][0]['buy_hkd'] == 0
    assert 'missing_sell_amt' in result['gaps']


def test_flow_pagination_stops_after_three_pages_and_marks_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(url: str, **kwargs) -> httpx.Response:
        page = int(kwargs['params']['pageNumber'])
        rows = [{
            'TRADE_DATE': f'2026-10-{20 + page:02d}',
            'NET_DEAL_AMT': '1',
            'BUY_AMT': '1',
            'SELL_AMT': '0',
            'DEAL_AMT': '1',
            'DEAL_NUM': '1',
            'ACCUM_DEAL_AMT': '1',
            'INDEX_CLOSE_PRICE': '100',
            'INDEX_CHANGE_RATE': '0',
        } for _ in range(100)]
        return http_response(url, eastmoney_page(rows, count=400, pages=4))

    calls = install_get(monkeypatch, handler)

    result = hk_sources.fetch('southbound_flow')

    assert result['status'] == 'partial'
    assert len(calls) == 3
    assert [call['params']['pageNumber'] for call in calls] == ['1', '2', '3']
    assert len(result['items']) == 300
    assert 'page_limit_reached' in result['gaps']


def _tencent_line(key: str, *, price: str, quote_time: str, time_index: int) -> str:
    fields = [''] * 31
    fields[3] = price
    fields[time_index] = quote_time
    return f'v_{key}="{"~".join(fields)}";'


def ah_quote_text(*, a_time: str, h_time: str, fx_time: str) -> str:
    return ''.join((
        _tencent_line('sh601398', price='10.00', quote_time=a_time, time_index=30),
        _tencent_line('r_hk01398', price='5.00', quote_time=h_time, time_index=30),
        _tencent_line('whHKDCNY', price='0.90', quote_time=fx_time, time_index=5),
    ))


def test_ah_pair_uses_exact_hk_pair_and_computes_decimal_premium(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair_row = {
        'SECUCODE': '601398.SH',
        'STR_CODEH': '01398.HK',
        'STR_NAMEA': '工商银行',
        'STR_NAMEH': '工行',
    }
    quote_text = ah_quote_text(
        a_time='2026/10/25 15:59:00',
        h_time='2026/10/25 16:00:00',
        fx_time='2026/10/25 16:00:00',
    )

    def handler(url: str, **kwargs):
        if url == hk_sources.EASTMONEY_URL:
            return http_response(url, eastmoney_page([pair_row]))
        return httpx.Response(200, content=quote_text.encode('gbk'), request=httpx.Request('GET', url))

    calls = install_get(monkeypatch, handler)

    result = hk_sources.fetch('ah_premium', '01398.HK')

    item = result['items'][0]
    assert result['status'] == 'ok'
    assert item['pair']['a_symbol'] == '601398.SH'
    assert item['pair']['h_symbol'] == '01398.HK'
    assert item['ah_ratio'] == pytest.approx(2.22222222)
    assert item['premium_ratio'] == pytest.approx(1.22222222)
    assert result['as_of'] == '2026-10-25T16:00:00+08:00'
    assert len(calls) == 2
    assert calls[1]['url'] == 'https://qt.gtimg.cn/q=sh601398,r_hk01398,whHKDCNY'
    assert all(call['timeout'] == 8.0 for call in calls)


@pytest.mark.parametrize('missing_leg', ['a', 'h', 'fx'])
def test_ah_missing_any_quote_timestamp_is_partial(
    monkeypatch: pytest.MonkeyPatch, missing_leg: str,
) -> None:
    pair_row = {
        'SECUCODE': '601398.SH',
        'STR_CODEH': '01398.HK',
        'STR_NAMEA': '工商银行',
        'STR_NAMEH': '工行',
    }
    times = {
        'a': '2026/10/25 16:00:00',
        'h': '2026/10/25 16:00:00',
        'fx': '2026/10/25 16:00:00',
    }
    times[missing_leg] = ''
    quote_text = ah_quote_text(a_time=times['a'], h_time=times['h'], fx_time=times['fx'])

    def handler(url: str, **kwargs):
        if url == hk_sources.EASTMONEY_URL:
            return http_response(url, eastmoney_page([pair_row]))
        return httpx.Response(200, content=quote_text.encode('gbk'), request=httpx.Request('GET', url))

    install_get(monkeypatch, handler)

    result = hk_sources.fetch('ah_premium', '01398.HK')

    assert result['status'] == 'partial'
    assert 'missing_quote_time' in result['gaps']
    assert result['items'][0]['premium_ratio'] == pytest.approx(1.22222222)


def test_ah_quotes_more_than_five_minutes_apart_are_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pair_row = {
        'SECUCODE': '601398.SH',
        'STR_CODEH': '01398.HK',
        'STR_NAMEA': '工商银行',
        'STR_NAMEH': '工行',
    }
    quote_text = ah_quote_text(
        a_time='2026/10/25 15:50:00',
        h_time='2026/10/25 16:00:00',
        fx_time='2026/10/25 16:00:00',
    )

    def handler(url: str, **kwargs):
        if url == hk_sources.EASTMONEY_URL:
            return http_response(url, eastmoney_page([pair_row]))
        return httpx.Response(200, content=quote_text.encode('gbk'), request=httpx.Request('GET', url))

    install_get(monkeypatch, handler)

    result = hk_sources.fetch('ah_premium', '01398.HK')

    assert result['status'] == 'partial'
    assert any('跨时点报价' in gap for gap in result['gaps'])
    assert result['items'][0]['premium_ratio'] == pytest.approx(1.22222222)


def test_ah_does_not_fall_back_to_another_hk_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = install_get(
        monkeypatch,
        lambda url, **kwargs: http_response(
            url,
            eastmoney_page([{
                'SECUCODE': '000001.SZ',
                'STR_CODEH': '00005.HK',
                'STR_NAMEA': '平安银行',
                'STR_NAMEH': '汇丰控股',
            }]),
        ),
    )

    result = hk_sources.fetch('ah_premium', '01398.HK')

    assert result['status'] == 'empty'
    assert result['items'] == []
    assert result['gaps'] == ['no_exact_ah_pair']
    assert len(calls) == 1


def test_source_failure_does_not_leak_response_text_or_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def failure(*_args, **_kwargs):
        raise RuntimeError('private response contains token=do-not-return')

    install_get(monkeypatch, failure)

    result = hk_sources.fetch('southbound_flow')

    assert result['status'] == 'partial'
    assert result['items'] == []
    assert result['gaps'] == ['source_unavailable']
    assert 'token' not in repr(result)
    assert 'private response' not in repr(result)


def test_missing_datacenter_envelope_is_not_reported_as_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_get(
        monkeypatch,
        lambda url, **kwargs: http_response(url, {'success': True, 'code': 0, 'result': {}}),
    )

    result = hk_sources.fetch('southbound_flow')

    assert result['status'] == 'partial'
    assert result['items'] == []
    assert result['gaps'] == ['source_unavailable']


def test_unsupported_dataset_returns_envelope_without_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = install_get(monkeypatch, lambda *_args, **_kwargs: pytest.fail('must not request'))

    result = hk_sources.fetch('northbound_flow')

    assert result['dataset'] == 'northbound_flow'
    assert result['status'] == 'partial'
    assert result['items'] == []
    assert result['gaps'] == ['unsupported_dataset']
    assert calls == []
