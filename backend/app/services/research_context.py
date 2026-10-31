# ruff: noqa: RUF001 -- Chinese research UI and prompt punctuation.
"""Read-only portfolio context for research prompts.

Broker holdings are not connected. Paper accounts are kept separate and are
read directly from existing files so this module cannot trigger paper's lazy
legacy migration or create account directories.
"""
from __future__ import annotations

import itertools
import json
import math
import os
import stat
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.market_time import CN_TZ, cn_now
from app.strategy import paper

_BROKER_UNKNOWN = {
    "state": "unknown",
    "source": "not_connected",
    "cash": None,
    "available_qty": None,
}
_FEE_KEYS = ("commission_pct", "stamp_tax_pct", "slippage_bps")
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_ACCOUNT_DIRS = 64


def _finite_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        return None
    return value


def _fees(account: dict[str, Any] | None) -> dict[str, int | float | None]:
    def fee(key):
        value = _finite_number(account.get(key)) if account else None
        return value if value is not None and value >= 0 else None
    return {
        **{key: fee(key) for key in _FEE_KEYS},
        "min_commission": _finite_number(paper.MIN_COMMISSION),
    }


def _unavailable_account(
    account_id: str,
    *,
    cash: int | float | None = None,
    fees: dict[str, int | float | None] | None = None,
) -> dict[str, Any]:
    return {
        "id": account_id,
        "state": "unavailable",
        "qty": None,
        "available_qty": None,
        "avg_cost": None,
        "cash": cash,
        "fees": fees or _fees(None),
    }


def _existing_account_dirs(data_dir: Path) -> tuple[list[tuple[str, Path]], bool]:
    """Return existing account directories without invoking paper._root()."""
    base = paper.accounts_base(data_dir)
    if _linked(Path(data_dir) / 'paper') or _linked(base):
        return [], False
    try:
        # One extra entry tells us whether the limit was exceeded without
        # consuming an unbounded directory iterator.
        children = list(itertools.islice(base.iterdir(), _MAX_ACCOUNT_DIRS + 1))
    except FileNotFoundError:
        # The old single-account layout can be read in place. Do not migrate it.
        legacy = Path(data_dir) / "paper"
        if _linked(legacy):
            return [], False
        try:
            (legacy / "account.json").stat()
        except FileNotFoundError:
            return [], True
        except OSError:
            return [], False
        return [(paper.DEFAULT_ACCOUNT_ID, legacy)], True
    except OSError:
        return [], False

    overflow = len(children) > _MAX_ACCOUNT_DIRS
    children = children[:_MAX_ACCOUNT_DIRS]
    directories = []
    for child in children:
        try:
            mode = child.lstat().st_mode
        except OSError:
            # Keep the visible account id so the caller can surface unavailable.
            directories.append((child.name, child))
            continue
        if stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
            directories.append((child.name, child))
    return directories, not overflow


def _regular_file_stat(path: Path, root_dir: Path) -> os.stat_result:
    """Validate a direct child regular file before reading or stat-only checks."""
    path = Path(path)
    root_dir = Path(root_dir)
    if _linked(root_dir) or _linked(path):
        raise ValueError("Symlink paper data is unavailable")
    root_stat = root_dir.lstat()
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ValueError("Paper account path is not a directory")
    file_stat = path.lstat()
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError("Paper data is not a regular file")
    try:
        resolved_root = root_dir.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
        relative = resolved_path.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("Paper data path is outside its account directory") from exc
    if len(relative.parts) != 1:
        raise ValueError("Paper data path is outside its account directory")
    return file_stat


def _linked(path: Path) -> bool:
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def _read_json(path: Path, root_dir: Path) -> dict[str, Any]:
    file_stat = _regular_file_stat(path, root_dir)
    if file_stat.st_size > _MAX_FILE_BYTES:
        raise ValueError("Paper JSON exceeds the read limit")
    with path.open("rb") as stream:
        raw = stream.read(_MAX_FILE_BYTES + 1)
    if len(raw) > _MAX_FILE_BYTES or len(raw) != file_stat.st_size:
        raise ValueError("Paper JSON changed or exceeds the read limit")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object")
    return value


def _empty_position(symbol: str, asset_type: str) -> dict[str, Any]:
    return {"symbol": symbol, "asset_type": asset_type, "lots": []}


def _read_position(
    account_dir: Path,
    symbol: str,
    asset_type: str,
) -> tuple[dict[str, Any] | None, bool]:
    path = account_dir / "positions.json"
    try:
        payload = _read_json(path, account_dir)
    except FileNotFoundError:
        # A fresh account without a materialized position snapshot is flat only
        # when there is also no ledger to replay. A non-empty ledger is unknown.
        fills_path = account_dir / "fills.jsonl"
        try:
            fills_stat = _regular_file_stat(fills_path, account_dir)
            has_fills = fills_stat.st_size > 0
        except FileNotFoundError:
            has_fills = False
        except (OSError, ValueError):
            return None, False
        return (None, not has_fills)
    except (OSError, ValueError, TypeError):
        return None, False

    positions = payload.get("positions")
    if not isinstance(positions, dict):
        return None, False
    if symbol not in positions:
        return None, True
    position = positions[symbol]
    if not isinstance(position, dict):
        return None, False
    result = dict(position)
    result.setdefault("symbol", symbol)
    result.setdefault("asset_type", asset_type)
    if not isinstance(result.get("lots"), list):
        return None, False
    if result.get("asset_type") != asset_type or result.get('symbol') != symbol:
        return None, False
    return result, True


def _read_account_context(
    account_id: str,
    account_dir: Path,
    symbol: str,
    asset_type: str,
    requested_date: str,
) -> dict[str, Any]:
    try:
        # paper.get_account() calls _root(), which can create directories and
        # move legacy files. Read its existing persisted fields without that path.
        account = _read_json(account_dir / "account.json", account_dir)
    except (OSError, ValueError, TypeError):
        return _unavailable_account(account_id)
    if account.get("id") not in (None, account_id):
        return _unavailable_account(account_id)

    cash = _finite_number(account.get("cash"))
    fees = _fees(account)
    position, readable = _read_position(account_dir, symbol, asset_type)
    if not readable:
        return _unavailable_account(account_id, cash=cash, fees=fees)

    has_position = position is not None
    position = position or _empty_position(symbol, asset_type)
    if has_position and "qty" not in position:
        return _unavailable_account(account_id, cash=cash, fees=fees)
    qty = _finite_number(position.get("qty", 0) if has_position else 0)
    if qty is None or qty < 0:
        return _unavailable_account(account_id, cash=cash, fees=fees)
    lot_qtys = [_finite_number(lot.get("qty")) for lot in position["lots"] if isinstance(lot, dict)]
    if len(lot_qtys) != len(position["lots"]) or any(value is None or value < 0 for value in lot_qtys):
        return _unavailable_account(account_id, cash=cash, fees=fees)
    if not math.isclose(sum(lot_qtys), qty, rel_tol=1e-9, abs_tol=1e-6):
        return _unavailable_account(account_id, cash=cash, fees=fees)

    try:
        # Never trust the materialized available_qty: it can be stale across days.
        available_qty = paper._available_of(position, requested_date)
    except Exception:  # A broken position must not look flat.
        return _unavailable_account(account_id, cash=cash, fees=fees)
    available_qty = _finite_number(available_qty)
    if available_qty is None or available_qty < 0 or available_qty > qty:
        return _unavailable_account(account_id, cash=cash, fees=fees)

    held = qty > 0
    avg_cost = _finite_number(position.get("avg_cost")) if held else None
    if avg_cost is not None and avg_cost <= 0:
        avg_cost = None
    return {
        "id": account_id,
        "state": "held" if held else "flat",
        "qty": qty,
        "available_qty": available_qty,
        "avg_cost": avg_cost,
        "cash": cash,
        "fees": fees,
    }


def _account_context(account_id, account_dir, symbol, asset_type, requested_date):
    # A fill changes account cash then materializes positions under this same
    # lock. Reading both within it prevents publishing a mixed account snapshot.
    with paper.PAPER_LOCK:
        return _read_account_context(account_id, account_dir, symbol, asset_type, requested_date)


def _registered_lots(data_dir, symbol):
    """User-entered reference batches, never a reconciled brokerage book."""
    folder = Path(data_dir) / 'user_data' / 'lots'
    result = {'source': 'manual_registration', 'state': 'not_registered', 'items': [],
              'cash': None, 'available_qty': None, 'fees': None}
    if _linked(folder.parent) or _linked(folder):
        return result | {'state': 'unavailable'}
    try:
        paths = list(itertools.islice(folder.iterdir(), 101))
    except FileNotFoundError:
        return result
    except OSError:
        return result | {'state': 'unavailable'}
    failed = len(paths) > 100
    for path in paths[:100]:
        if not path.name.startswith('lot_') or path.suffix != '.json':
            continue
        try:
            row = _read_json(path, folder)
            if row.get('symbol') != symbol:
                continue
            qty, cost = _finite_number(row.get('qty')), _finite_number(row.get('cost_price'))
            if qty is None or qty < 0 or cost is None or cost <= 0:
                raise ValueError('Invalid registered batch')
            buy_date = row.get('buy_date')
            if buy_date is None or (isinstance(buy_date, str) and buy_date == ''):
                buy_date = None
            else:
                if not isinstance(buy_date, str) or len(buy_date) != 10:
                    raise ValueError('Invalid registered batch date')
                if date.fromisoformat(buy_date).isoformat() != buy_date:
                    raise ValueError('Invalid registered batch date')
            result['items'].append({'id': str(row.get('id') or path.stem)[:100],
                'qty': qty, 'cost_price': cost, 'buy_date': buy_date})
        except (OSError, ValueError, TypeError):
            failed = True
    result['state'] = 'unavailable' if failed else ('registered' if result['items'] else 'not_registered')
    return result


def build_portfolio_context(
    data_dir: Path,
    symbol: str,
    asset_type: str,
    requested_at: datetime,
    *,
    include_accounts: bool = True,
) -> dict[str, Any]:
    """Build broker-unknown and per-account paper context without writes."""
    if requested_at.tzinfo is None or requested_at.utcoffset() is None:
        raise ValueError("requested_at must be timezone-aware")
    if not symbol or not asset_type:
        raise ValueError("symbol and asset_type are required")

    context: dict[str, Any] = {
        'captured_at': cn_now().isoformat(),
        "broker": dict(_BROKER_UNKNOWN),
        "paper_accounts": [],
        "paper_accounts_state": "not_requested" if not include_accounts else "available",
    }
    if not include_accounts:
        return context
    context['registered_lots'] = _registered_lots(data_dir, symbol)

    requested_date = requested_at.astimezone(CN_TZ).date().isoformat()
    account_dirs, scan_ok = _existing_account_dirs(Path(data_dir))
    if not scan_ok:
        context["paper_accounts_state"] = "unavailable"

    accounts = []
    for account_id, account_dir in account_dirs:
        try:
            paper.validate_account_id(account_id)
        except ValueError:
            accounts.append(_unavailable_account(account_id))
            continue
        accounts.append(_account_context(account_id, account_dir, symbol, asset_type, requested_date))
    context["paper_accounts"] = accounts
    if any(account["state"] == "unavailable" for account in accounts):
        context["paper_accounts_state"] = "unavailable"
    return context


def instruction(context: dict[str, Any]) -> str:
    """Return prompt guidance that preserves the distinction from live holdings."""
    encoded = json.dumps(context, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return (
        "\n## 持仓与账户数据边界\n"
        "broker.state=unknown 表示未连接实盘账户；不得推断真实持仓、现金、可卖数量或券商费率。"
        "registered_lots仅为用户登记的参考批次，可结合其数量和成本作条件分析；没有当前券商核对，不能据此认定可卖份额、现金或实际总仓位。"
        "paper_accounts 是逐账户隔离的本地模拟盘，不得合并成一个实盘仓位。\n"
        "available_qty 是按 requested_at 的北京时间日期，依据对应模拟账户持仓批次和本地交易规则重算；"
        "股票及未明确支持当日卖出的 ETF 遵守 T+1，当日新买通常不可卖，明确支持当日卖出的品种按其规则。\n"
        "每条 paper account 的费用只适用于该模拟账户；费率是账户设置和模拟规则，不是实盘报价。"
        "cash 或 available_qty 为 null、账户状态为 unavailable 或真实账户状态未知时，不得写出可执行的实际数量/金额。\n"
        "以上信息仅作事实和约束说明；报告保持中性，不给出买卖、加减仓、持有或仓位操作建议，不改变策略参数或声称已下单。\n"
        "本次只读账户上下文(JSON)：\n" + encoded
    )
