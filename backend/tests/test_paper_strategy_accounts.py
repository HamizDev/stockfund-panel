import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api import paper as paper_api
from app.api.paper import (
    StrategyAccountModel,
    compare_accounts,
    create_strategy_account,
    set_strategy_account_enabled,
)
from app.strategy import monitor_rules, paper, paper_auto


class _Engine:
    def has(self, strategy_id):
        return strategy_id == "strat_1"

    def get(self, strategy_id):
        return SimpleNamespace(meta={
            "id": strategy_id, "name": "示例策略",
            "timeframes": ["1d"], "asset_types": ["stock"],
        })


class _Monitor:
    def __init__(self):
        self.rules = []

    def set_rules(self, rules):
        self.rules = rules


def _request(data_dir):
    monitor = _Monitor()
    state = SimpleNamespace(
        repo=SimpleNamespace(store=SimpleNamespace(data_dir=data_dir)),
        strategy_engine=_Engine(), monitor_engine=monitor,
    )
    return SimpleNamespace(app=SimpleNamespace(state=state)), monitor


def test_strategy_account_is_isolated_and_idempotent(tmp_path):
    request, runtime_monitor = _request(tmp_path)
    body = StrategyAccountModel(strategy_id="strat_1", initial_cash=200_000, entry_pct=10)
    first = create_strategy_account(request, body)
    second = create_strategy_account(request, body)
    account_id = first["account"]["id"]
    assert second["account"]["id"] == account_id
    assert paper.list_account_ids(tmp_path) == [account_id]
    assert first["account"]["strategy_id"] == "strat_1"
    assert {r["side"] for r in paper_auto.load_auto_rules(tmp_path, account_id)} == {"buy", "sell"}
    assert len(first["rules"]) == len(second["rules"]) == 2
    assert first["monitor_enabled"] is True
    assert any(r["strategy_id"] == "strat_1" for r in runtime_monitor.rules)
    assert compare_accounts(request)["accounts"][0]["auto_enabled"] is False
    assert all(not r["enabled"] for r in first["rules"])
    set_strategy_account_enabled(request, account_id, False)
    assert compare_accounts(request)["accounts"][0]["auto_enabled"] is False
    set_strategy_account_enabled(request, account_id, True)
    assert compare_accounts(request)["accounts"][0]["auto_enabled"] is True
    # Returning an existing account must not pause it or reset funds.
    create_strategy_account(request, body)
    assert compare_accounts(request)["accounts"][0]["auto_enabled"] is True


def test_existing_disabled_strategy_monitor_is_preserved(tmp_path):
    request, _ = _request(tmp_path)
    monitor_id = monitor_rules.strategy_rule_id("strat_1")
    existing = monitor_rules.normalize({
        "id": monitor_id, "name": "自定义监控", "type": "strategy",
        "strategy_id": "strat_1", "scope": "all", "direction": "entry",
        "notify_events": ["pool_entry"], "enabled": False,
    })
    monitor_rules.save_one(tmp_path, existing)
    result = create_strategy_account(request, StrategyAccountModel(strategy_id="strat_1"))
    assert result["monitor_enabled"] is True
    after = monitor_rules.load_one(tmp_path, monitor_id)
    assert after["name"] == "自定义监控"
    assert after["notify_events"] == ["pool_entry"]
    assert after["enabled"] is False
    assert compare_accounts(request)["accounts"][0]["auto_enabled"] is False


def test_legacy_monitor_migration_does_not_disable_paper_monitor(tmp_path):
    request, _ = _request(tmp_path)
    monitor_id = monitor_rules.strategy_rule_id("strat_1")
    monitor_rules.save_one(tmp_path, monitor_rules.normalize({
        "id": monitor_id, "name": "自定义监控", "type": "strategy",
        "strategy_id": "strat_1", "scope": "all", "direction": "entry",
        "notify_events": ["pool_entry"], "enabled": True,
    }))

    result = create_strategy_account(request, StrategyAccountModel(strategy_id="strat_1"))
    account_id = result["account"]["id"]
    paper_monitor_id = f"paper_strategy_{account_id}"
    assert monitor_rules.load_one(tmp_path, paper_monitor_id)["enabled"] is True
    monitor_rules.migrate_strategy_monitors(tmp_path, ["other"], {"other": "其他策略"})
    assert monitor_rules.load_one(tmp_path, paper_monitor_id)["enabled"] is True
    assert monitor_rules.load_one(tmp_path, monitor_id)["enabled"] is False
    assert compare_accounts(request)["accounts"][0]["auto_enabled"] is False


def test_strategy_account_http_contract_validates_and_creates(tmp_path):
    request, _ = _request(tmp_path)
    app = FastAPI()
    app.state.repo = request.app.state.repo
    app.state.strategy_engine = request.app.state.strategy_engine
    app.state.monitor_engine = request.app.state.monitor_engine
    app.include_router(paper_api.router)
    client = TestClient(app)

    bad = client.post("/api/paper/strategy_accounts", json={"strategy_id": "strat_1", "initial_cash": "NaN"})
    assert bad.status_code == 422
    missing = client.post("/api/paper/strategy_accounts", json={"strategy_id": "not_found"})
    assert missing.status_code == 400
    created = client.post("/api/paper/strategy_accounts", json={"strategy_id": "strat_1"})
    assert created.status_code == 200
    account_id = created.json()["account"]["id"]
    assert client.post(f"/api/paper/strategy_accounts/{account_id}/enabled?enabled=false").json() == {"enabled": False}


def test_market_accounts_are_isolated_and_orders_use_etf_rules(tmp_path):
    request, _ = _request(tmp_path)
    request.app.state.strategy_engine.get = lambda sid: SimpleNamespace(meta={
        "id": sid, "name": "双市场策略", "timeframes": ["1d"], "asset_types": ["stock", "etf"],
    })
    stock = create_strategy_account(request, StrategyAccountModel(strategy_id="strat_1", asset_type="stock"))
    etf = create_strategy_account(request, StrategyAccountModel(strategy_id="strat_1", asset_type="etf"))
    stock_id, etf_id = stock["account"]["id"], etf["account"]["id"]
    assert stock_id != etf_id
    assert etf["account"]["asset_type"] == "etf"
    assert create_strategy_account(request, StrategyAccountModel(strategy_id="strat_1", asset_type="etf"))["account"]["id"] == etf_id
    for account_id in (stock_id, etf_id):
        set_strategy_account_enabled(request, account_id, True)
    event = {"source": "strategy", "strategy_id": "strat_1", "rule_id": f"paper_strategy_{etf_id}",
             "type": "buy_signal", "symbol": "510300.SH", "price": 4.0}
    assert paper_auto.on_rule_events(tmp_path, [event], stock_id) == []
    orders = paper_auto.on_rule_events(tmp_path, [event], etf_id)
    assert len(orders) == 1
    assert orders[0]["asset_type"] == "etf"
    # Legacy strategy rules without binding fields still consume only their own monitor.
    for rule in paper_auto.load_auto_rules(tmp_path, stock_id):
        rule.pop("monitor_rule_id", None)
        rule.pop("asset_type", None)
        paper_auto.save_auto_rule(tmp_path, rule, stock_id)
    assert paper_auto.on_rule_events(tmp_path, [event], stock_id) == []


def test_unsupported_market_has_no_side_effects(tmp_path):
    request, _ = _request(tmp_path)
    with pytest.raises(HTTPException) as caught:
        create_strategy_account(request, StrategyAccountModel(strategy_id="strat_1", asset_type="etf"))
    assert caught.value.status_code == 400
    assert paper.list_account_ids(tmp_path) == []


def test_compare_uses_observed_nav_without_cost_value_fallback(tmp_path):
    request, _ = _request(tmp_path)
    account = create_strategy_account(request, StrategyAccountModel(strategy_id="strat_1"))["account"]
    row = compare_accounts(request)["accounts"][0]
    assert row["wins"] == 0 and row["holdings_count"] == 0
    assert row["nav_date"] is None
    assert row["settled_total"] is None and row["settled_pnl_pct"] is None
    paper._write_nav_line(tmp_path, "2026-09-29", {"date": "2026-09-29", "nav": 202000, "cash": 180000, "mv": 22000, "valuation_complete": True}, account["id"])
    row = compare_accounts(request)["accounts"][0]
    assert row["nav_date"] == "2026-09-29"
    assert row["settled_total"] == 202000 and row["settled_pnl_pct"] == 1.0


def test_cost_fallback_or_legacy_nav_is_not_ranked_as_observed_performance(tmp_path):
    request, _ = _request(tmp_path)
    account = create_strategy_account(request, StrategyAccountModel(strategy_id="strat_1"))["account"]
    account_id = account["id"]
    for quality in (None, False):
        record = {"date": "2026-09-30", "nav": 230000, "cash": 180000, "mv": 50000}
        if quality is not None:
            record["valuation_complete"] = quality
        paper._write_nav_line(tmp_path, "2026-09-30", record, account_id)
        row = compare_accounts(request)["accounts"][0]
        assert row["settled_total"] is None and row["settled_pnl_pct"] is None
        assert row["settled_max_drawdown"] is None
        assert row["settled_nav_status"] == ("legacy_unknown" if quality is None else "missing_prices")


def test_daily_nav_marks_missing_quotes_instead_of_calling_cost_a_market_value(tmp_path, monkeypatch):
    paper.create_account(tmp_path, 200000)
    monkeypatch.setattr(paper, "load_positions", lambda *_: {"600000.SH": {"qty": 100, "avg_cost": 10}})
    missing = paper.daily_nav(tmp_path, "2026-09-30", {})
    assert missing["valuation_complete"] is False
    assert missing["missing_symbols"] == ["600000.SH"]
    complete = paper.daily_nav(tmp_path, "2026-09-30", {"600000.SH": 11})
    assert complete["valuation_complete"] is True and complete["missing_symbols"] == []


@pytest.mark.parametrize("change", [{"asset_type": "etf"}, {"strategy_id": "other"}])
def test_changed_monitor_context_cannot_trade_or_enable(tmp_path, change):
    request, _ = _request(tmp_path)
    account = create_strategy_account(request, StrategyAccountModel(strategy_id="strat_1"))["account"]
    account_id = account["id"]
    set_strategy_account_enabled(request, account_id, True)
    monitor_id = f"paper_strategy_{account_id}"
    monitor = monitor_rules.load_one(tmp_path, monitor_id)
    monitor.update(change)
    monitor_rules.save_one(tmp_path, monitor)
    event = {"source": "strategy", "strategy_id": "strat_1", "rule_id": monitor_id,
             "type": "buy_signal", "symbol": "600000.SH", "price": 10}
    assert paper_auto.on_rule_events(tmp_path, [event], account_id) == []
    assert compare_accounts(request)["accounts"][0]["auto_enabled"] is False
    with pytest.raises(HTTPException) as caught:
        set_strategy_account_enabled(request, account_id, True)
    assert caught.value.status_code == 409
    with pytest.raises(HTTPException) as caught:
        create_strategy_account(request, StrategyAccountModel(strategy_id="strat_1"))
    assert caught.value.status_code == 409
    assert paper.load_orders(tmp_path, account_id) == []


def test_frozen_account_cannot_enable_but_can_pause(tmp_path):
    request, _ = _request(tmp_path)
    account = create_strategy_account(request, StrategyAccountModel(strategy_id="strat_1"))["account"]
    account["status"] = "frozen"
    paper.save_account(tmp_path, account, account["id"])
    with pytest.raises(HTTPException) as caught:
        set_strategy_account_enabled(request, account["id"], True)
    assert caught.value.status_code == 409
    assert set_strategy_account_enabled(request, account["id"], False) == {"enabled": False}
    assert all(not r["enabled"] for r in paper_auto.load_auto_rules(tmp_path, account["id"]))


def test_changed_order_rule_market_is_rejected(tmp_path):
    request, _ = _request(tmp_path)
    account = create_strategy_account(request, StrategyAccountModel(strategy_id="strat_1"))["account"]
    account_id = account["id"]
    set_strategy_account_enabled(request, account_id, True)
    for rule in paper_auto.load_auto_rules(tmp_path, account_id):
        rule["asset_type"] = "etf"
        paper_auto.save_auto_rule(tmp_path, rule, account_id)
    with pytest.raises(HTTPException) as caught:
        set_strategy_account_enabled(request, account_id, True)
    assert caught.value.status_code == 409
    event = {"source": "strategy", "strategy_id": "strat_1", "rule_id": f"paper_strategy_{account_id}",
             "type": "buy_signal", "symbol": "600000.SH", "price": 10}
    assert paper_auto.on_rule_events(tmp_path, [event], account_id) == []


def test_manual_order_also_respects_strategy_account_market(tmp_path):
    request, _ = _request(tmp_path)
    account = create_strategy_account(request, StrategyAccountModel(strategy_id="strat_1"))["account"]
    order, error = paper.create_order(tmp_path, "510300.SH", "buy", account_id=account["id"],
                                      asset_type="etf", qty=100, ref_price=4)
    assert order is None and error == "订单市场与策略账户不一致"
    assert paper.load_orders(tmp_path, account["id"]) == []


def test_pause_prevents_queued_event_using_old_enabled_snapshot(tmp_path, monkeypatch):
    request, _ = _request(tmp_path)
    account_id = create_strategy_account(request, StrategyAccountModel(strategy_id="strat_1"))["account"]["id"]
    set_strategy_account_enabled(request, account_id, True)
    base_lock = threading.RLock()
    event_waiting = threading.Event()

    class GateLock:
        def __enter__(self):
            if threading.current_thread().name == "paper-event":
                event_waiting.set()
            base_lock.acquire()

        def __exit__(self, *_):
            base_lock.release()

    monkeypatch.setattr(paper, "PAPER_LOCK", GateLock())
    event = {"source": "strategy", "strategy_id": "strat_1", "rule_id": f"paper_strategy_{account_id}",
             "type": "buy_signal", "symbol": "600000.SH", "price": 10}
    created = []
    with base_lock:
        worker = threading.Thread(name="paper-event", target=lambda: created.extend(paper_auto.on_rule_events(tmp_path, [event], account_id)))
        worker.start()
        assert event_waiting.wait(5)
        set_strategy_account_enabled(request, account_id, False)
    worker.join(5)
    assert not worker.is_alive()
    assert created == [] and paper.load_orders(tmp_path, account_id) == []
