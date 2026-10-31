from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.services import research_context
from app.strategy import paper


def _account(
    root: Path,
    account_id: str,
    *,
    cash: float = 1000,
    positions: dict | None = None,
    commission_pct: float = 0.0003,
) -> Path:
    account_dir = root / "paper" / "accounts" / account_id
    account_dir.mkdir(parents=True)
    (account_dir / "account.json").write_text(json.dumps({
        "id": account_id,
        "cash": cash,
        "commission_pct": commission_pct,
        "stamp_tax_pct": 0.001,
        "slippage_bps": 4,
    }), encoding="utf-8")
    if positions is not None:
        (account_dir / "positions.json").write_text(
            json.dumps({"positions": positions}), encoding="utf-8",
        )
    return account_dir


def _requested_at(value: str = "2026-10-31T10:00:00+08:00") -> datetime:
    return datetime.fromisoformat(value)


def test_context_keeps_broker_unknown_and_paper_accounts_separate(tmp_path: Path) -> None:
    held = {
        "symbol": "510300.SH",
        "asset_type": "etf",
        "qty": 200,
        "avg_cost": 3.5,
        "available_qty": 999,
        "lots": [
            {"date": "2026-10-30", "qty": 100},
            {"date": "2026-10-31", "qty": 100},
        ],
    }
    _account(tmp_path, "alpha", positions={"510300.SH": held})
    _account(tmp_path, "beta", positions={})

    context = research_context.build_portfolio_context(
        tmp_path, "510300.SH", "etf", _requested_at(),
    )

    assert context["broker"] == {
        "state": "unknown", "source": "not_connected", "cash": None, "available_qty": None,
    }
    accounts = {item["id"]: item for item in context["paper_accounts"]}
    assert accounts["alpha"]["state"] == "held"
    assert accounts["alpha"]["qty"] == 200
    assert accounts["alpha"]["available_qty"] == 100
    assert accounts["alpha"]["cash"] == 1000
    assert accounts["alpha"]["fees"] == {
        "commission_pct": 0.0003,
        "stamp_tax_pct": 0.001,
        "slippage_bps": 4,
        "min_commission": paper.MIN_COMMISSION,
    }
    assert accounts["beta"]["state"] == "flat"
    assert accounts["beta"]["qty"] == 0
    assert accounts["beta"]["available_qty"] == 0
    assert accounts["beta"]["avg_cost"] is None


def test_available_qty_uses_beijing_requested_date_for_t_plus_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    positions = {"600000.SH": {
        "symbol": "600000.SH",
        "asset_type": "stock",
        "qty": 200,
        "avg_cost": 9.5,
        "available_qty": 200,
        "lots": [
            {"date": "2026-10-30", "qty": 100},
            {"date": "2026-10-31", "qty": 100},
        ],
    }}
    _account(tmp_path, "default", positions=positions)
    calls: list[str] = []
    original = paper._available_of

    def record_available(pos: dict, today: str | None = None) -> int:
        calls.append(today or "")
        return original(pos, today)

    monkeypatch.setattr(paper, "_available_of", record_available)
    same_beijing_day = datetime(2026, 10, 30, 19, tzinfo=UTC)
    next_beijing_day = datetime(2026, 10, 31, 16, tzinfo=UTC)

    same_day = research_context.build_portfolio_context(
        tmp_path, "600000.SH", "stock", same_beijing_day,
    )["paper_accounts"][0]
    next_day = research_context.build_portfolio_context(
        tmp_path, "600000.SH", "stock", next_beijing_day,
    )["paper_accounts"][0]

    assert same_day["available_qty"] == 100
    assert next_day["available_qty"] == 200
    assert calls == ["2026-10-31", "2026-11-01"]


def test_read_failure_is_unavailable_not_flat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _account(tmp_path, "broken", positions={})
    original = Path.open

    def fail_positions(path: Path, *args, **kwargs):
        if path.name == "positions.json":
            raise OSError("simulated read failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_positions)
    account = research_context.build_portfolio_context(
        tmp_path, "600000.SH", "stock", _requested_at(),
    )["paper_accounts"][0]

    assert account["state"] == "unavailable"
    assert account["qty"] is None
    assert account["available_qty"] is None


def test_legacy_layout_is_read_without_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = tmp_path / "paper"
    legacy.mkdir()
    (legacy / "account.json").write_text(json.dumps({"id": "default", "cash": 500}), encoding="utf-8")
    (legacy / "positions.json").write_text(json.dumps({"positions": {}}), encoding="utf-8")

    def unexpected(*args, **kwargs):
        pytest.fail("paper API helper can create directories or migrate legacy data")

    monkeypatch.setattr(paper, "get_account", unexpected)
    account = research_context.build_portfolio_context(
        tmp_path, "600000.SH", "stock", _requested_at(),
    )["paper_accounts"][0]

    assert account["id"] == "default"
    assert account["state"] == "flat"
    assert not (legacy / "accounts").exists()


def test_include_accounts_false_does_not_scan_or_read_paper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(*args, **kwargs):
        pytest.fail("paper accounts must not be scanned")

    monkeypatch.setattr(paper, "accounts_base", unexpected)
    monkeypatch.setattr(paper, "list_account_ids", unexpected)
    monkeypatch.setattr(paper, "get_account", unexpected)
    monkeypatch.setattr(paper, "_available_of", unexpected)

    context = research_context.build_portfolio_context(
        tmp_path, "600000.SH", "stock", _requested_at(), include_accounts=False,
    )

    assert context["broker"]["state"] == "unknown"
    assert context["paper_accounts"] == []
    assert context["paper_accounts_state"] == "not_requested"
    assert list(tmp_path.iterdir()) == []


def test_nonfinite_values_remain_unknown_and_do_not_become_zero(tmp_path: Path) -> None:
    _account(
        tmp_path,
        "nonfinite",
        cash=float("inf"),
        commission_pct=float("nan"),
        positions={"600000.SH": {
            "symbol": "600000.SH",
            "asset_type": "stock",
            "qty": float("nan"),
            "avg_cost": float("inf"),
            "available_qty": 100,
            "lots": [{"date": "2026-10-30", "qty": 100}],
        }},
    )

    context = research_context.build_portfolio_context(
        tmp_path, "600000.SH", "stock", _requested_at(),
    )
    account = context["paper_accounts"][0]

    assert account["state"] == "unavailable"
    assert account["qty"] is None
    assert account["available_qty"] is None
    assert account["avg_cost"] is None
    assert account["cash"] is None
    assert account["fees"]["commission_pct"] is None
    assert account["fees"]["stamp_tax_pct"] == 0.001
    assert account["fees"]["min_commission"] == paper.MIN_COMMISSION
    # The returned context is safe to serialize into the existing prompt stream.
    json.dumps(context, ensure_ascii=False, allow_nan=False)


def test_instruction_keeps_simulation_and_execution_limits_explicit() -> None:
    text = research_context.instruction({
        "broker": {"state": "unknown", "source": "not_connected"},
        "paper_accounts": [],
    })

    assert "未连接实盘账户" in text
    assert "逐账户隔离" in text
    assert "T+1" in text
    assert "报告保持中性" in text


def test_naive_requested_at_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        research_context.build_portfolio_context(
            tmp_path, "600000.SH", "stock", datetime(2026, 10, 31, 10),
        )


@pytest.mark.parametrize("filename", ["account.json", "positions.json"])
def test_oversized_paper_json_is_unavailable_before_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str,
) -> None:
    account_dir = _account(tmp_path, "large", positions={})
    target = account_dir / filename
    target.write_bytes(b" " * (research_context._MAX_FILE_BYTES + 1))
    original = Path.open

    def forbid_oversized_read(path: Path, *args, **kwargs):
        if path == target:
            pytest.fail("oversized paper JSON must be rejected from stat before reading")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", forbid_oversized_read)
    account = research_context.build_portfolio_context(
        tmp_path, "600000.SH", "stock", _requested_at(),
    )["paper_accounts"][0]

    assert account["state"] == "unavailable"
    assert account["qty"] is None


def test_symlinked_position_file_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _account(tmp_path, "linked", positions={})
    original = Path.is_symlink

    def mock_position_symlink(path: Path) -> bool:
        return path.name == "positions.json" or original(path)

    monkeypatch.setattr(Path, "is_symlink", mock_position_symlink)
    account = research_context.build_portfolio_context(
        tmp_path, "600000.SH", "stock", _requested_at(),
    )["paper_accounts"][0]

    assert account["state"] == "unavailable"
    assert account["qty"] is None
    assert account["available_qty"] is None


def test_json_reader_rejects_path_outside_account_dir(tmp_path: Path) -> None:
    account_dir = tmp_path / "paper" / "accounts" / "safe"
    account_dir.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="outside"):
        research_context._read_json(outside, account_dir)


def test_more_than_64_account_entries_is_bounded_and_globally_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for index in range(70):
        _account(tmp_path, f"acct{index:02d}", positions={})
    base = paper.accounts_base(tmp_path)
    original = Path.iterdir
    yielded = 0

    def count_account_entries(path: Path):
        nonlocal yielded
        iterator = original(path)
        if path != base:
            yield from iterator
            return
        for child in iterator:
            yielded += 1
            yield child

    monkeypatch.setattr(Path, "iterdir", count_account_entries)
    context = research_context.build_portfolio_context(
        tmp_path, "600000.SH", "stock", _requested_at(),
    )

    assert yielded == 65
    assert len(context["paper_accounts"]) == 64
    assert context["paper_accounts_state"] == "unavailable"


def test_account_reads_share_the_matchers_lock(tmp_path, monkeypatch):
    _account(tmp_path, 'locked', positions={})
    entered = []
    class Lock:
        active = False
        def __enter__(self):
            self.active = True
        def __exit__(self, *a):
            self.active = False
    lock = Lock()
    original = research_context._read_json
    def checked(*args):
        entered.append(lock.active)
        return original(*args)
    monkeypatch.setattr(paper, 'PAPER_LOCK', lock)
    monkeypatch.setattr(research_context, '_read_json', checked)
    research_context.build_portfolio_context(tmp_path, '600000.SH', 'stock', _requested_at())
    assert entered and all(entered)


def test_position_cannot_use_another_symbols_trading_rule(tmp_path):
    pos = dict(symbol='511010.SH', asset_type='etf', qty=100, avg_cost=1,
               lots=[{'date': '2026-10-31', 'qty': 100}])
    _account(tmp_path, 'mismatch', positions={'515880.SH': pos})
    result = research_context.build_portfolio_context(tmp_path, '515880.SH', 'etf', _requested_at())
    assert result['paper_accounts'][0]['state'] == 'unavailable'


def test_manual_registered_lots_are_separate_from_verified_broker_or_paper(tmp_path):
    folder = tmp_path / 'user_data' / 'lots'
    folder.mkdir(parents=True)
    (folder / 'lot_manual.json').write_text(json.dumps(dict(id='lot_manual', symbol='515880.SH',
        qty=3500, cost_price=.531, buy_date='2026-10-16')), encoding='utf-8')
    result = research_context.build_portfolio_context(tmp_path, '515880.SH', 'etf', _requested_at())
    assert result['registered_lots']['state'] == 'registered'
    assert result['registered_lots']['items'][0]['qty'] == 3500
    assert result['registered_lots']['available_qty'] is None
    assert result['broker']['state'] == 'unknown' and result['paper_accounts'] == []
    assert not (tmp_path / 'paper').exists()


@pytest.mark.parametrize('buy_date', [float('nan'), {'nested': 'value'}, 'x' * 10000, '2026-02-30', '20261016'])
def test_malformed_registered_date_does_not_enter_prompt_or_abort_analysis(tmp_path, buy_date):
    folder = tmp_path / 'user_data' / 'lots'
    folder.mkdir(parents=True)
    (folder / 'lot_bad.json').write_text(json.dumps(dict(symbol='515880.SH', qty=100,
        cost_price=.531, buy_date=buy_date)), encoding='utf-8')
    context = research_context.build_portfolio_context(tmp_path, '515880.SH', 'etf', _requested_at())
    assert context['registered_lots']['state'] == 'unavailable'
    assert context['registered_lots']['items'] == []
    assert 'broker.state=unknown' in research_context.instruction(context)


@pytest.mark.parametrize('date_fields', [{}, {'buy_date': None}, {'buy_date': ''}])
def test_registered_lots_preserve_optional_date_contract(tmp_path, date_fields):
    folder = tmp_path / 'user_data' / 'lots'
    folder.mkdir(parents=True)
    (folder / 'lot_optional.json').write_text(json.dumps(dict(symbol='515880.SH', qty=100,
        cost_price=.531, **date_fields)), encoding='utf-8')
    result = research_context.build_portfolio_context(tmp_path, '515880.SH', 'etf', _requested_at())
    assert result['registered_lots']['state'] == 'registered'
    assert result['registered_lots']['items'][0]['buy_date'] is None
