"""Optional BaoStock provider for normalized, single-event adjustment factors.

The BaoStock SDK keeps login and socket state in module globals. SDK calls run
in a short-lived child process, serialized by a process-local lock and bounded
by a timeout. This leaves the application's global socket defaults untouched;
the child always attempts logout and its socket is reclaimed when it exits.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import logging
import math
import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

logger = logging.getLogger(__name__)

_DATASETS = ("adj_factor",)
_ADJ_SCHEMA = {
    "symbol": pl.String,
    "trade_date": pl.Date,
    "ex_factor": pl.Float64,
}
_REQUIRED_FIELDS = {"code", "dividOperateDate", "backAdjustFactor"}
_HISTORY_START = "1990-01-01"
_MAX_SYMBOLS_PER_REQUEST = 100
_SUBPROCESS_BATCH_SIZE = 40
_WORKER_TIMEOUT_MIN_S = 30
_WORKER_TIMEOUT_PER_SYMBOL_S = 10
_WORKER_TIMEOUT_MAX_S = 300
_BAOSTOCK_LOCK = threading.Lock()
_BACKEND_DIR = Path(__file__).resolve().parents[3]
_WORKER_LAUNCH = (
    "from app.plugins.baostock.provider import _worker_main; _worker_main()"
)


class BaoStockError(RuntimeError):
    """BaoStock query failed or returned an invalid response."""


@dataclass
class _BaoStockConfig:
    """Config shim used by the built-in provider loader."""

    name: str = "baostock"
    display_name: str = "BaoStock"
    datasets: dict = field(default_factory=lambda: dict.fromkeys(_DATASETS))
    path: None = None
    builtin: bool = True


def availability() -> tuple[bool, str]:
    """Optional-dependency check; safe to call during application startup."""
    try:
        installed = importlib.util.find_spec("baostock") is not None
    except (ImportError, ValueError):
        installed = False
    if not installed:
        return False, (
            "缺少可选依赖 baostock；请在数据源设置中安装插件依赖，"  # noqa: RUF001
            "或按 plugin.yaml 中的提示安装 requirements.txt。"
        )
    return True, "ok"


def _empty_adj_factors() -> pl.DataFrame:
    return pl.DataFrame(schema=_ADJ_SCHEMA)


def _baostock_code(symbol: str) -> str:
    """Convert a canonical SH/SZ stock symbol to BaoStock's code format."""
    normalized = str(symbol).strip().upper()
    match = re.fullmatch(r"(\d{6})\.(SH|SZ)", normalized)
    if match is None:
        raise ValueError(
            f"BaoStock 仅支持沪深股票代码，拒绝标的: {symbol!r}"  # noqa: RUF001
        )

    code, exchange = match.groups()
    stock_prefixes = {
        "SH": ("600", "601", "603", "605", "688"),
        "SZ": ("000", "001", "002", "003", "300", "301"),
    }
    if not code.startswith(stock_prefixes[exchange]):
        raise ValueError(f"BaoStock 插件不支持 ETF、指数或该证券代码: {normalized}")
    return f"{exchange.lower()}.{code}"


def _parse_factor(value: object, symbol: str, trade_date: date) -> float:
    try:
        factor = float(value)
    except (TypeError, ValueError) as exc:
        raise BaoStockError(
            f"BaoStock {symbol} {trade_date} 的 backAdjustFactor 不是数值"
        ) from exc
    if not math.isfinite(factor) or factor <= 0:
        raise BaoStockError(
            f"BaoStock {symbol} {trade_date} 的 backAdjustFactor 必须是有限正数"
        )
    return factor


def _parse_trade_date(value: object, symbol: str) -> date:
    try:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise BaoStockError(f"BaoStock {symbol} 返回无效除权日期: {value!r}") from exc


def _normalize_factor_rows(
    symbol: str,
    requested_code: str,
    fields: list[str],
    rows: list[dict],
) -> list[dict]:
    """Validate cumulative source rows and derive canonical event ratios."""
    missing = _REQUIRED_FIELDS.difference(fields)
    if missing:
        raise BaoStockError(
            f"BaoStock {symbol} 响应缺少字段: {', '.join(sorted(missing))}"
        )

    by_date: dict[date, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise BaoStockError(f"BaoStock {symbol} 返回了格式错误的复权记录")
        row_code = str(row.get("code", "")).strip().lower()
        if row_code != requested_code.lower():
            raise BaoStockError(
                f"BaoStock 标的代码不匹配: 请求 {requested_code}, 返回 {row_code or '空值'}"
            )

        trade_date = _parse_trade_date(row.get("dividOperateDate"), symbol)
        factor = _parse_factor(row.get("backAdjustFactor"), symbol, trade_date)
        previous = by_date.get(trade_date)
        if previous is not None and previous != factor:
            raise BaoStockError(
                f"BaoStock {symbol} {trade_date} 返回冲突的重复复权因子"
            )
        by_date[trade_date] = factor

    result: list[dict] = []
    previous_cumulative = 1.0
    for index, (trade_date, cumulative) in enumerate(sorted(by_date.items())):
        # BaoStock includes the listing baseline (backAdjustFactor = 1) in the
        # event query. It is a level, not a corporate-action event.
        if index == 0 and cumulative == 1.0:
            continue
        ex_factor = cumulative / previous_cumulative
        if not math.isfinite(ex_factor) or ex_factor <= 0:
            raise BaoStockError(
                f"BaoStock {symbol} {trade_date} 推导出的单事件因子无效"
            )
        result.append(
            {"symbol": symbol, "trade_date": trade_date, "ex_factor": ex_factor}
        )
        previous_cumulative = cumulative
    return result


def _query_adjust_factor_rows(
    bs,
    code: str,
    start_date: str,
    end_date: str,
) -> tuple[list[str], list[dict]]:
    """Isolated SDK API call, kept small so field/error behavior is mockable."""
    try:
        response = bs.query_adjust_factor(
            code=code,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as exc:  # SDK/network exceptions must not mean "no events".
        raise BaoStockError(f"BaoStock {code} 复权因子请求失败: {exc}") from exc
    if response is None:
        raise BaoStockError(f"BaoStock {code} 未返回复权因子响应")

    error_code = str(getattr(response, "error_code", ""))
    if error_code != "0":
        error_msg = str(getattr(response, "error_msg", ""))
        raise BaoStockError(
            f"BaoStock {code} 复权因子查询失败 ({error_code}): {error_msg}"
        )

    fields = list(getattr(response, "fields", []) or [])
    missing = _REQUIRED_FIELDS.difference(fields)
    if missing:
        raise BaoStockError(
            f"BaoStock {code} 响应缺少字段: {', '.join(sorted(missing))}"
        )

    rows: list[dict] = []
    while True:
        current_error = str(getattr(response, "error_code", ""))
        if current_error != "0":
            error_msg = str(getattr(response, "error_msg", ""))
            raise BaoStockError(
                f"BaoStock {code} 复权因子迭代失败 ({current_error}): {error_msg}"
            )
        try:
            has_row = response.next()
        except Exception as exc:
            raise BaoStockError(f"BaoStock {code} 读取复权因子失败: {exc}") from exc
        if not has_row:
            break
        try:
            values = response.get_row_data()
        except Exception as exc:
            raise BaoStockError(f"BaoStock {code} 读取复权记录失败: {exc}") from exc
        if not isinstance(values, (list, tuple)) or len(values) != len(fields):
            raise BaoStockError(f"BaoStock {code} 返回了字段数不匹配的复权记录")
        rows.append(dict(zip(fields, values, strict=True)))
    return fields, rows


def _fetch_with_sdk_session(
    bs,
    symbols: list[str],
    end_date: str,
) -> list[dict]:
    """Login once, query symbols serially, and always attempt to logout."""
    results: list[dict] = []
    try:
        login = bs.login()
        login_code = str(getattr(login, "error_code", ""))
        if login_code != "0":
            login_msg = str(getattr(login, "error_msg", ""))
            raise BaoStockError(f"BaoStock 登录失败 ({login_code}): {login_msg}")

        for symbol in symbols:
            code = _baostock_code(symbol)
            fields, rows = _query_adjust_factor_rows(
                bs,
                code=code,
                start_date=_HISTORY_START,
                end_date=end_date,
            )
            results.append(
                {"symbol": symbol, "code": code, "fields": fields, "rows": rows}
            )
        return results
    finally:
        # BaoStock owns module-global login/socket state. The child process is
        # disposable, but explicit logout also covers normal and error returns.
        with contextlib.suppress(Exception):
            bs.logout()


def _worker_main() -> None:
    """Child-process entry point; emits one JSON response on stdout."""
    try:
        request = json.load(sys.stdin)
        symbols = request.get("symbols")
        end_date = request.get("end_date")
        if not isinstance(symbols, list) or not isinstance(end_date, str):
            raise BaoStockError("BaoStock 子进程收到无效请求")

        # The SDK prints login status to stdout on some releases. Keep protocol
        # output machine-readable and suppress SDK diagnostics from both pipes.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            io.StringIO()
        ):
            import baostock as bs

            results = _fetch_with_sdk_session(bs, symbols, end_date)
        payload = {"results": results}
    except Exception as exc:  # serialized so the parent raises instead of emptying.
        payload = {"error": f"{type(exc).__name__}: {exc}"}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _run_worker_subprocess(
    request: str,
    timeout_s: float,
    *,
    launch_code: str = _WORKER_LAUNCH,
) -> subprocess.CompletedProcess:
    """Run one isolated SDK process under the global session lock and timeout."""
    try:
        with _BAOSTOCK_LOCK:
            completed = subprocess.run(
                [sys.executable, "-c", launch_code],
                input=request,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=str(_BACKEND_DIR),
                check=False,
            )
    except subprocess.TimeoutExpired as exc:
        raise BaoStockError(f"BaoStock 查询子进程超时 ({timeout_s:g} 秒)") from exc
    except OSError as exc:
        raise BaoStockError(f"无法启动 BaoStock 隔离查询进程: {exc}") from exc
    return completed


def _worker_timeout_s(symbol_count: int) -> int:
    return min(
        _WORKER_TIMEOUT_MAX_S,
        max(_WORKER_TIMEOUT_MIN_S, _WORKER_TIMEOUT_PER_SYMBOL_S * symbol_count),
    )


def _query_adjust_factor_batch(symbols: list[str], end_date: date) -> list[dict]:
    """Fetch up to 100 symbols in serialized, independently timed batches."""
    if len(symbols) > _MAX_SYMBOLS_PER_REQUEST:
        raise ValueError(
            f"BaoStock 单次最多查询 {_MAX_SYMBOLS_PER_REQUEST} 只股票"
        )

    all_results: list[dict] = []
    for offset in range(0, len(symbols), _SUBPROCESS_BATCH_SIZE):
        batch = symbols[offset : offset + _SUBPROCESS_BATCH_SIZE]
        request = json.dumps(
            {"symbols": batch, "end_date": end_date.isoformat()},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        timeout_s = _worker_timeout_s(len(batch))
        completed = _run_worker_subprocess(request, timeout_s)

        if completed.returncode != 0:
            raise BaoStockError(
                f"BaoStock 隔离查询进程失败 (exit {completed.returncode})"
            )
        try:
            payload = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise BaoStockError("BaoStock 隔离查询返回了无效响应") from exc
        if not isinstance(payload, dict):
            raise BaoStockError("BaoStock 隔离查询返回了无效响应")
        if payload.get("error"):
            raise BaoStockError(str(payload["error"]))
        batch_results = payload.get("results")
        if not isinstance(batch_results, list) or len(batch_results) != len(batch):
            raise BaoStockError("BaoStock 隔离查询缺少复权结果")
        for expected_symbol, result in zip(batch, batch_results, strict=True):
            if not isinstance(result, dict) or result.get("symbol") != expected_symbol:
                raise BaoStockError(
                    f"BaoStock 隔离查询标的数量或顺序不匹配: 预期 {expected_symbol}"
                )
        all_results.extend(batch_results)
    return all_results


class BaoStockProvider:
    """Free, optional provider for SH/SZ stock adjustment factors only."""

    name = "baostock"
    builtin = True

    def __init__(self) -> None:
        self.config = _BaoStockConfig()

    def close(self) -> None:
        """No persistent client is kept; every SDK session is isolated."""

    def get_adj_factors(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        on_chunk_done=None,
    ) -> pl.DataFrame:
        if asset_type != "stock":
            raise ValueError("BaoStock 复权因子仅支持 SH/SZ 股票")
        if not symbols:
            return _empty_adj_factors()

        canonical_symbols: list[str] = []
        seen: set[str] = set()
        for symbol in symbols:
            code = _baostock_code(symbol)
            canonical = code[3:].upper() + "." + code[:2].upper()
            if canonical not in seen:
                canonical_symbols.append(canonical)
                seen.add(canonical)

        start_date = start_time.date() if start_time is not None else None
        if end_time is not None:
            end_date = end_time.date()
        else:
            end_date = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        if start_date is not None and start_date > end_date:
            raise ValueError("复权因子开始日期晚于结束日期")

        raw_results = _query_adjust_factor_batch(canonical_symbols, end_date)
        if len(raw_results) != len(canonical_symbols):
            raise BaoStockError("BaoStock 查询标的数量与响应数量不一致")

        output: list[dict] = []
        for index, expected_symbol in enumerate(canonical_symbols):
            raw = raw_results[index]
            if not isinstance(raw, dict) or raw.get("symbol") != expected_symbol:
                raise BaoStockError(
                    f"BaoStock 响应标的顺序或代码不匹配: 预期 {expected_symbol}"
                )
            requested_code = _baostock_code(expected_symbol)
            events = _normalize_factor_rows(
                symbol=expected_symbol,
                requested_code=requested_code,
                fields=list(raw.get("fields") or []),
                rows=list(raw.get("rows") or []),
            )
            # Ratios are derived using the full history (including the event
            # immediately before start_time); only canonical events are sliced.
            output.extend(
                row
                for row in events
                if (start_date is None or row["trade_date"] >= start_date)
                and row["trade_date"] <= end_date
            )
            if on_chunk_done is not None:
                on_chunk_done(index + 1, len(canonical_symbols))

        if not output:
            return _empty_adj_factors()
        frame = pl.DataFrame(output, schema=_ADJ_SCHEMA).sort(["symbol", "trade_date"])
        if frame.select(pl.struct("symbol", "trade_date").n_unique()).item() != frame.height:
            raise BaoStockError("BaoStock 规范化结果包含重复的标的日期")
        return frame

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        if dataset != "adj_factor":
            return {
                "provider": self.name,
                "dataset": dataset,
                "rows": 0,
                "error": f"BaoStock 插件未接入 {dataset} 数据集",
            }
        requested = (symbols or ["600519.SH"])[:1]
        try:
            frame = self.get_adj_factors(requested, None, None)
        except (BaoStockError, ValueError) as exc:
            return {
                "provider": self.name,
                "dataset": dataset,
                "rows": 0,
                "error": str(exc),
            }
        preview = frame.head(5).to_dicts()
        for row in preview:
            row["trade_date"] = row["trade_date"].isoformat()
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": frame.height,
            "columns": frame.columns,
            "preview": preview,
        }


if __name__ == "__main__":
    _worker_main()
