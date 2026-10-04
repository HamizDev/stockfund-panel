"""AI draft data integrity, bounded rule execution and no-account side effects."""
from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.custom.crypto_paper import auto, rules
from app.custom.crypto_paper import strategy_draft as draft
from app.custom.crypto_paper.routes import build_router

_NOW = 1_800_000_120_000
_HOUR = 3_600_000


def _bars():
    last = _NOW // _HOUR * _HOUR - _HOUR
    return [{"open_time_ms": last - (199 - i) * _HOUR,
             "close_time_ms": last - (199 - i) * _HOUR + _HOUR - 1,
             "open": str(100 + i), "high": str(101 + i), "low": str(99 + i),
             "close": str(100 + i), "volume": "10"} for i in range(200)]


def _quote():
    return {"exchange": "bitget", "market": "usdm", "symbol": "ETHUSDT", "asof_ms": _NOW,
            "bid": "299", "ask": "300", "max_leverage": 150,
            "taker_fee_rate": "0.0006", "last_funding_rate": "0.0001"}


def _model(**changes):
    return {"name": "AI trend research", "strategy_id": "ema_trend",
            "strategy_params": {"fast_period": 10, "slow_period": 40},
            "allocation_pct": 5, "stop_loss_pct": 1.5, "take_profit_pct": 3,
            "rationale": "Use the observed trend and volatility for a paper rule draft.",
            "risk_notes": ["Short sample; no independent backtest."], **changes}


@pytest.fixture
def source(monkeypatch):
    state = {"bars": _bars(), "quote": _quote(), "messages": None, "source_calls": []}
    module = SimpleNamespace(klines=lambda *a, **k: state["bars"], quote=lambda *a: state["quote"])

    def source_for(exchange):
        state["source_calls"].append(exchange)
        return module

    monkeypatch.setattr(auto, "_source", source_for)
    monkeypatch.setattr(draft.time, "time", lambda: _NOW / 1000)
    monkeypatch.setattr(draft.ai_provider, "ai_configured", lambda: True)
    monkeypatch.setattr(draft.ai_provider, "current_ai_model", lambda: "configured-model")
    monkeypatch.setattr(draft.ai_provider, "current_ai_provider", lambda: "codex_cli")

    async def generate(messages, **kwargs):
        state["messages"] = messages
        return json.dumps(_model())

    monkeypatch.setattr(draft.ai_provider, "generate_ai_text", generate)
    return state


def test_generation_uses_public_observations_without_creating_accounts(source, tmp_path):
    response = asyncio.run(draft.generate(draft.DraftRequest()))
    assert response["draft"]["exchange"] == "bitget"
    assert response["draft"]["strategy_params"] == {"fast_period": 10, "slow_period": 40}
    assert response["draft"]["leverage"] == 10
    assert response["evidence"]["bars_count"] == 200
    assert response["evidence"]["return_pct"] == 199
    assert response["evidence"]["data_end_ms"] < _NOW
    assert response["evidence"]["max_close_drawdown_pct"] == 0
    assert response["diagnostics"]["latest_signal"] == "long"
    assert response["diagnostics"]["evaluated_bars"] == 140
    assert "return_pct" not in response["diagnostics"]
    sent = json.loads(source["messages"][1]["content"])
    assert sent["closed_candles"] == source["bars"]
    assert set(sent) == {"request", "evidence", "closed_candles", "paper_model", "execution_rules"}
    assert sent["execution_rules"] == draft.EXECUTION_RULES
    assert "不使用下一根开盘价" in sent["execution_rules"]
    assert auto.accounts(tmp_path) == []
    assert not list(tmp_path.iterdir())


def test_binance_draft_is_rejected_before_any_market_request(source):
    with pytest.raises(ValueError, match="仅支持 Bitget"):
        asyncio.run(draft.generate(draft.DraftRequest(exchange="binance")))
    assert source["source_calls"] == []
    assert source["messages"] is None


@pytest.mark.parametrize("change", [
    {"asof_ms": _NOW - 60_001}, {"asof_ms": _NOW + 5001}, {"exchange": "binance"}, {"symbol": "BTCUSDT"},
    {"market": "spot"}, {"bid": "301"}, {"max_leverage": 5},
    {"last_funding_rate": "NaN"}, {"last_funding_rate": "not-a-rate"},
])
def test_wrong_or_stale_quote_fails_before_model(source, change):
    source["quote"].update(change)
    with pytest.raises(ValueError):
        asyncio.run(draft.generate(draft.DraftRequest()))
    assert source["messages"] is None


@pytest.mark.parametrize("claim", [
    "模拟成交使用下一根开盘价并计入滑点,没有下一根数据时不虚构成交。",
    "用后续已收盘K线高低价核对止盈止损,同根触及两者时按止损先发生。",
    "同一根同时触及两者时按止损先发生,跳空则调整成交价格。",
    "Use the next candle open as the fill price for every order.",
    "按历史K线收盘价回填成交,计算仓位和收益情况。",
])
def test_incompatible_execution_prose_is_rejected(claim):
    with pytest.raises(ValueError, match="固定执行口径"):
        draft.parse_draft(json.dumps(_model(rationale=claim)), draft.DraftRequest())


def test_execution_guard_applies_to_risk_notes_too():
    value = _model(risk_notes=["模拟使用下一根开盘价成交,可能出现跳空风险。"])
    with pytest.raises(ValueError, match="固定执行口径"):
        draft.parse_draft(json.dumps(value), draft.DraftRequest())


@pytest.mark.parametrize("denial", [
    "信号后按当前公开报价成交。不使用下一根开盘价成交。",
    "不使用历史收盘价成交,也不按K线高低价核对止盈止损。",
    "We do not use the next candle open for simulated fills.",
])
def test_correct_denials_of_incompatible_execution_are_allowed(denial):
    parsed = draft.parse_draft(json.dumps(_model(rationale=denial)), draft.DraftRequest())
    assert parsed.rationale == denial


def test_denial_does_not_hide_a_later_positive_claim():
    value = _model(rationale="不使用下一根开盘价,实际模拟使用下一根开盘价成交。")
    with pytest.raises(ValueError, match="固定执行口径"):
        draft.parse_draft(json.dumps(value), draft.DraftRequest())


@pytest.mark.parametrize("problem", ["short", "gap", "duplicate", "open", "ohlc", "nonfinite"])
def test_incomplete_or_invalid_candles_fail_before_model(source, problem):
    if problem == "short":
        source["bars"] = source["bars"][-120:]
    elif problem == "gap":
        source["bars"].pop(20)
    elif problem == "duplicate":
        source["bars"][20] = source["bars"][19]
    elif problem == "open":
        source["bars"][-1]["close_time_ms"] = _NOW
    elif problem == "ohlc":
        source["bars"][-1]["high"] = "1"
    else:
        source["bars"][-1]["close"] = "NaN"
    with pytest.raises(ValueError):
        asyncio.run(draft.generate(draft.DraftRequest()))
    assert source["messages"] is None


@pytest.mark.parametrize("value", [
    _model(strategy_id="arbitrary_code"), _model(code="print('not allowed')"),
    _model(strategy_params={"fast_period": True, "slow_period": 40}),
    _model(strategy_params={"fast_period": 30, "slow_period": 20}),
    _model(allocation_pct=11), _model(stop_loss_pct=8),
    _model(risk_notes=[""]), _model(leverage=20),
])
def test_unbounded_or_unknown_model_rules_rejected(value):
    with pytest.raises(ValueError):
        draft.parse_draft(json.dumps(value), draft.DraftRequest())


def test_json_trailing_text_and_duplicate_keys_rejected():
    text = json.dumps(_model())
    for raw in (text + " extra", text[:-1] + ', "name":"other"}'):
        with pytest.raises(ValueError):
            draft.parse_draft(raw, draft.DraftRequest())
    assert draft.parse_draft("```json\n" + text + "\n```", draft.DraftRequest()).name


def test_model_failure_releases_generation_lock(source, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(draft.ai_provider, "generate_ai_text", broken)
    with pytest.raises(RuntimeError, match="unavailable"):
        asyncio.run(draft.generate(draft.DraftRequest()))
    assert draft._GENERATION_LOCK.acquire(blocking=False)
    draft._GENERATION_LOCK.release()


def test_parameters_change_executed_signal_and_need_sufficient_history():
    bars = _bars()
    for i, bar in enumerate(bars[-8:]):
        value = str(270 - i * 4)
        bar.update(open=value, close=value, high=str(int(value) + 1), low=str(int(value) - 1))
    assert auto.signal("ema_trend", bars, "usdm", {"fast_period": 5, "slow_period": 20}) == "short"
    assert auto.signal("ema_trend", bars, "usdm") == "flat"
    with pytest.raises(ValueError, match="121"):
        auto.signal("channel_breakout", bars[-61:], "usdm", {"lookback": 120})


def test_old_idempotent_account_request_keeps_default_rules(tmp_path):
    body = {"name": "legacy", "market": "usdm", "symbol": "BTCUSDT", "strategy_id": "ema_trend",
            "interval": "1h", "leverage": 10, "initial_cash": 10000, "allocation_pct": 10,
            "stop_loss_pct": 2, "take_profit_pct": 4, "request_id": "legacy-draft"}
    original = auto.create(tmp_path, body)[0]
    state = auto._load(tmp_path)
    del state["requests"]["legacy-draft"]["signature"]["config"]["strategy_params"]
    del state["accounts"][original["id"]]["strategy_params"]
    auto._save(tmp_path, state)
    assert auto.create(tmp_path, body)[0]["id"] == original["id"]
    assert auto.accounts(tmp_path)[0]["strategy_params"] == {"fast_period": 20, "slow_period": 60}
    with pytest.raises(ValueError, match="其他账户"):
        auto.create(tmp_path, {**body, "strategy_params": {"fast_period": 10, "slow_period": 30}})


def test_draft_api_does_not_write_ledger_and_account_persists_reviewed_params(source, tmp_path):
    app = FastAPI()
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    app.include_router(build_router())
    with TestClient(app) as api:
        response = api.post("/api/custom/crypto-paper/strategy-draft", json={})
        assert response.status_code == 200
        assert not list(tmp_path.iterdir())
        body = {**response.json()["draft"], "request_id": "reviewed-draft"}
        created = api.post("/api/custom/crypto-paper/strategy-accounts", json=body)
        assert created.status_code == 200
        assert not created.json()["account"]["enabled"]
        assert created.json()["account"]["strategy_params"] == body["strategy_params"]
        changed = copy.deepcopy(body)
        changed["request_id"] = "bad-periods"
        changed["strategy_params"] = {"fast_period": 40, "slow_period": 20}
        assert api.post("/api/custom/crypto-paper/strategy-accounts", json=changed).status_code == 400
        assert len(auto.accounts(tmp_path)) == 1
    assert rules.parameters("channel_breakout") == {"lookback": 20}
