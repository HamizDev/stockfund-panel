from types import SimpleNamespace

from fastapi import FastAPI
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
    assert compare_accounts(request)["accounts"][0]["auto_enabled"] is True
    set_strategy_account_enabled(request, account_id, False)
    assert compare_accounts(request)["accounts"][0]["auto_enabled"] is False
    set_strategy_account_enabled(request, account_id, True)
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
    assert compare_accounts(request)["accounts"][0]["auto_enabled"] is True


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
    assert compare_accounts(request)["accounts"][0]["auto_enabled"] is True


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
