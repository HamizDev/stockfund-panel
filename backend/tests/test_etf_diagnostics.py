from datetime import datetime

import polars as pl
import pytest
from app.market_time import CN_TZ
from app.strategy import etf_shortterm as st


def test_diagnostic_matches_existing_fee_gate_without_changing_it():
    acc = {"commission_pct": .0005, "slippage_bps": 5}
    feature = {"mean": 4.04, "range": .01, "ts": "2026-10-15T10:00:00"}
    result = st._diagnostic(acc, 500, 4, feature, 800, 600)
    assert result["required_ratio"] == st.cost_floor(acc, 500, 4)
    assert result["roundtrip_fee"] == 10
    assert result["slippage_ratio"] == .001
    assert result["buffer_ratio"] == .002
    assert result["required_ratio"] == pytest.approx(.008)
    assert result["reversion_ratio"] == pytest.approx(.01)
    assert result["available_qty"] == 600


def test_no_affordable_quantity_is_not_serialized_as_infinite_gate():
    result = st._diagnostic({"commission_pct": .0005, "slippage_bps": 5}, 0, 4,
                            {"mean": 4, "range": .01, "ts": "t"}, 0, 0)
    assert result["required_ratio"] is None
    assert result["roundtrip_fee"] is None


def test_missing_data_reason_clears_old_metrics_but_keeps_order_projection():
    item = {"diagnostic": {"quote_price": 4}, "bar": "old", "order_id": "existing"}
    now = datetime(2026, 10, 15, 10, 3, 45, tzinfo=CN_TZ)
    st._observe_reason(item, "分钟字段缺失", now)
    assert item["checked_at"] == "2026-10-15T10:03:00+08:00"
    assert "diagnostic" not in item
    assert item["order_id"] == "existing"
    assert item["bar"] == "old"


@pytest.mark.parametrize('rejected_at', ['guard', 'order'])
@pytest.mark.parametrize('with_feature', [False, True])
def test_rejected_cycle_rebuy_refreshes_timestamp_and_diagnostic(monkeypatch, tmp_path, rejected_at, with_feature):
    symbol = '510300.SH'
    now = datetime(2026, 10, 15, 14, 46, tzinfo=CN_TZ)
    item = {'checked_at': '2026-10-15T10:00:00+08:00', 'diagnostic': {'quote_price': 4},
            'cycle': {'qty': 100, 'price': 4.3, 'sell_order': 'sold'}}
    state = {'symbols': {symbol: item}}
    acc = {'status': 'active', 'commission_pct': .0005, 'slippage_bps': 5,
           'etf_shortterm': {'mode': 'base_t', 'enabled': True}}
    feature = {'mean': 4.2, 'range': .01, 'ts': '2026-10-15T14:45:00'} if with_feature else None
    calls = []
    monkeypatch.setattr(st, 'MODES', {'base_t': {'symbols': [symbol]}})
    monkeypatch.setattr(st, 'read_state', lambda *args: state)
    monkeypatch.setattr(st.paper, 'get_account', lambda *args: acc)
    monkeypatch.setattr(st.paper, 'evaluate_etf_intraday', lambda *args: [])
    monkeypatch.setattr(st.paper, 'load_orders', lambda *args: [])
    monkeypatch.setattr(st.paper, 'load_positions', lambda *args: {})
    monkeypatch.setattr(st, '_reconcile', lambda *args: None)
    monkeypatch.setattr(st, 'features', lambda *args: (feature, '分钟缺失'))
    monkeypatch.setattr(st, 'buy_guard', lambda *args: '资金不足' if rejected_at == 'guard' else None)
    monkeypatch.setattr(st.paper, 'create_order', lambda *args, **kwargs: (calls.append(1) or None, '建单拒绝'))
    monkeypatch.setattr(st, 'save_state', lambda *args: None)
    assert st.run_account(tmp_path, 'isolated', {symbol: {'last_price': 4.2,
        'timestamp': now.timestamp() * 1000}}, pl.DataFrame(), {}, now) == []
    assert item['checked_at'] == now.isoformat()
    assert len(calls) == (rejected_at == 'order')
    if with_feature:
        assert item['diagnostic']['quote_price'] == 4.2
    else:
        assert 'diagnostic' not in item
