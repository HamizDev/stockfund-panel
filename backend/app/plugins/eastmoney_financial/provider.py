"""Eastmoney financial statements provider for the existing financial dataset contract."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import polars as pl

from app.market_time import cn_today
from app.plugins.eastmoney_financial.client import EastmoneyClient, EastmoneyError

logger = logging.getLogger(__name__)

_DATASETS = ("financial",)
_HISTORY_PERIODS = 8

_STATEMENT_FIELDS: dict[str, dict[str, str]] = {
    "income": {
        "TOTAL_OPERATE_INCOME": "revenue",
        "OPERATE_COST": "operating_cost",
        "SALE_EXPENSE": "selling_expense",
        "MANAGE_EXPENSE": "admin_expense",
        "FINANCE_EXPENSE": "financial_expense",
        "OPERATE_PROFIT": "operating_profit",
        "TOTAL_PROFIT": "total_profit",
        "INCOME_TAX": "income_tax",
        "PARENT_NETPROFIT": "net_income_attributable",
    },
    "balance_sheet": {
        "TOTAL_ASSETS": "total_assets",
        "TOTAL_LIABILITIES": "total_liabilities",
        "TOTAL_EQUITY": "total_equity",
        "MONETARYFUNDS": "cash_and_equivalents",
        "ACCOUNTS_RECE": "accounts_receivable",
        "DEBT_ASSET_RATIO": "debt_to_asset_ratio",
    },
    "cash_flow": {
        "NETCASH_OPERATE": "net_operating_cash_flow",
        "NETCASH_INVEST": "net_investing_cash_flow",
        "NETCASH_FINANCE": "net_financing_cash_flow",
        "CONSTRUCT_LONG_ASSET": "capex",
        "CCE_ADD": "net_cash_change",
    },
}

_METRIC_FIELDS = {
    "BASIC_EPS": "eps_basic",
    "BPS": "bps",
    "WEIGHTAVG_ROE": "roe",
    "XSMLL": "gross_margin",
    "YSTZ": "revenue_yoy",
    "SJLTZ": "net_income_yoy",
}
_METRIC_NULL_FIELDS = (
    "bps",
    "roe",
    "gross_margin",
    "net_margin",
    "revenue_yoy",
    "net_income_yoy",
    "debt_to_asset_ratio",
)
_A_SHARE_TYPES = {"058001001", "058001008"}

_COMMON_SCHEMA = {
    "symbol": pl.String,
    "period_end": pl.String,
    "announce_date": pl.String,
}
_VALUE_SCHEMAS: dict[str, dict[str, pl.DataType]] = {
    "income": {
        **dict.fromkeys(_STATEMENT_FIELDS["income"].values(), pl.Float64),
        "net_income": pl.Float64,
    },
    "balance_sheet": dict.fromkeys(_STATEMENT_FIELDS["balance_sheet"].values(), pl.Float64),
    "cash_flow": dict.fromkeys(_STATEMENT_FIELDS["cash_flow"].values(), pl.Float64),
    "metrics": {
        **dict.fromkeys(_METRIC_FIELDS.values(), pl.Float64),
        **dict.fromkeys(_METRIC_NULL_FIELDS, pl.Float64),
        "MGJYXJJE": pl.Float64,
    },
}
_TABLE_SCHEMAS = {
    table: {**_COMMON_SCHEMA, **value_schema}
    for table, value_schema in _VALUE_SCHEMAS.items()
}


def availability() -> tuple[bool, str]:
    """The built-in plugin needs only the backend's existing httpx dependency."""
    try:
        import httpx  # noqa: F401

        return True, "ok"
    except ImportError as exc:
        return False, f"缺少依赖 httpx: {exc}"


@dataclass
class _EastmoneyConfig:
    name: str = "eastmoney_financial"
    display_name: str = "Eastmoney Financial"
    datasets: dict = field(default_factory=lambda: {"financial": None})
    path: None = None
    builtin: bool = True


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.date()
    except ValueError:
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None


def _finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _symbol(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().upper()
    return normalized or None


def _period_text(period: str | date) -> str:
    period_date = _as_date(period)
    if period_date is None:
        raise ValueError(f"invalid report period: {period!r}")
    return period_date.isoformat()


def _recent_completed_quarters(as_of: date, count: int = _HISTORY_PERIODS) -> list[str]:
    candidates = [
        date(year, month, day)
        for year in range(as_of.year - 3, as_of.year + 1)
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31))
        if date(year, month, day) <= as_of
    ]
    return [period.isoformat() for period in sorted(candidates, reverse=True)[:count]]


def _empty_frame(table: str) -> pl.DataFrame:
    return pl.DataFrame(schema=_TABLE_SCHEMAS[table])


def _frame(table: str, rows: list[dict[str, Any]]) -> pl.DataFrame:
    schema = _TABLE_SCHEMAS[table]
    if not rows:
        return _empty_frame(table)
    columns = {name: [row.get(name) for row in rows] for name in schema}
    return pl.DataFrame(columns, schema=schema).sort(["symbol", "period_end"])


def merge_metrics_with_balance(
    metrics: pl.DataFrame,
    balance_sheet: pl.DataFrame,
) -> pl.DataFrame:
    """Fill null metric debt ratios from the matching balance report period.

    This is a left-side, symbol-and-period join. Existing metric values win; when
    the balance report supplies a missing value, announce_date becomes the later
    of the two source dates to avoid making the value appear available too early.
    """
    if metrics.is_empty() or balance_sheet.is_empty():
        return metrics.clone()

    metric_required = {"symbol", "period_end", "announce_date", "debt_to_asset_ratio"}
    balance_required = {"symbol", "period_end", "announce_date", "debt_to_asset_ratio"}
    missing_metrics = metric_required.difference(metrics.columns)
    missing_balance = balance_required.difference(balance_sheet.columns)
    if missing_metrics or missing_balance:
        raise ValueError(
            "cannot merge debt ratio; missing metric columns "
            f"{sorted(missing_metrics)} or balance columns {sorted(missing_balance)}"
        )

    balance_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in balance_sheet.to_dicts():
        key = (str(row["symbol"]), str(row["period_end"]))
        if key in balance_by_key:
            raise EastmoneyError(
                f"duplicate balance rows while merging metrics for {key[0]} {key[1]}"
            )
        balance_by_key[key] = row

    merged_values: list[float | None] = []
    merged_announce_dates: list[str | None] = []
    for metric_row in metrics.to_dicts():
        key = (str(metric_row["symbol"]), str(metric_row["period_end"]))
        metric_value = _finite_float(metric_row["debt_to_asset_ratio"])
        balance_row = balance_by_key.get(key)
        balance_value = (
            _finite_float(balance_row.get("debt_to_asset_ratio"))
            if balance_row is not None
            else None
        )
        if metric_value is not None or balance_value is None:
            merged_values.append(metric_value)
            merged_announce_dates.append(metric_row["announce_date"])
            continue

        merged_values.append(balance_value)
        available_dates = [
            value
            for value in (
                _as_date(metric_row.get("announce_date")),
                _as_date(balance_row.get("announce_date")),
            )
            if value is not None
        ]
        merged_announce_dates.append(
            max(available_dates).isoformat()
            if available_dates
            else metric_row.get("announce_date")
        )

    return metrics.with_columns(
        pl.Series("debt_to_asset_ratio", merged_values, dtype=pl.Float64),
        pl.Series("announce_date", merged_announce_dates, dtype=pl.String),
    )


class EastmoneyFinancialProvider:
    """Map Eastmoney's public financial reports to the stockfund-panel schema."""

    name = "eastmoney_financial"
    builtin = True

    def __init__(self, client: EastmoneyClient | None = None) -> None:
        self.config = _EastmoneyConfig()
        self._client = client
        self._owns_client = client is None

    def _get_client(self) -> EastmoneyClient:
        if self._client is None:
            self._client = EastmoneyClient()
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def fetch_period(
        self,
        table: str,
        period: str | date,
        symbols: list[str] | None = None,
    ) -> pl.DataFrame:
        """Fetch and normalize one report period without writing data.

        This method is exposed for staging callers. It returns a complete period or
        propagates an error; page-level partial results are never returned.
        """
        if table not in _TABLE_SCHEMAS:
            raise ValueError(f"unsupported Eastmoney financial table: {table}")
        period_iso = _period_text(period)
        period_date = date.fromisoformat(period_iso)
        if period_date > cn_today():
            return _empty_frame(table)

        requested = None if symbols is None else {
            value for value in (_symbol(item) for item in symbols) if value
        }
        source_rows = self._get_client().fetch_period(table, period_iso)
        if source_rows and not any(
            isinstance(source, dict) and _symbol(source.get("SECUCODE"))
            for source in source_rows
        ):
            raise EastmoneyError(
                f"Eastmoney {table} {period_iso} rows contain no recognized SECUCODE field"
            )

        latest_by_key: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
        report_field = "REPORTDATE" if table == "metrics" else "REPORT_DATE"
        for source in source_rows:
            if not isinstance(source, dict):
                raise EastmoneyError(f"Eastmoney {table} {period_iso} contained a non-object row")

            if table == "metrics" and str(source.get("SECURITY_TYPE_CODE", "")).strip() not in _A_SHARE_TYPES:
                continue

            symbol = _symbol(source.get("SECUCODE"))
            if symbol is None or (requested is not None and symbol not in requested):
                continue
            report_date = _as_date(source.get(report_field))
            if report_date is None or report_date.isoformat() != period_iso:
                continue
            if report_date > cn_today():
                continue

            availability_dates = [
                _as_date(source.get(field)) for field in ("NOTICE_DATE", "UPDATE_DATE")
            ]
            valid_availability_dates = [value for value in availability_dates if value]
            if not valid_availability_dates:
                continue
            announce_date = max(valid_availability_dates)
            if announce_date > cn_today():
                continue

            row: dict[str, Any] = {
                "symbol": symbol,
                "period_end": period_iso,
                "announce_date": announce_date.isoformat(),
            }
            if table == "metrics":
                for source_field, target_field in _METRIC_FIELDS.items():
                    row[target_field] = _finite_float(source.get(source_field))
                for field_name in _METRIC_NULL_FIELDS:
                    row.setdefault(field_name, None)
                row["MGJYXJJE"] = _finite_float(source.get("MGJYXJJE"))
            else:
                for source_field, target_field in _STATEMENT_FIELDS[table].items():
                    row[target_field] = _finite_float(source.get(source_field))
                if table == "income":
                    # No independently verified consolidated net-income source was provided.
                    row["net_income"] = None

            key = (symbol, period_iso)
            previous = latest_by_key.get(key)
            if previous is None or announce_date.isoformat() > previous[0]:
                latest_by_key[key] = (announce_date.isoformat(), row)
            elif announce_date.isoformat() == previous[0] and row != previous[1]:
                raise EastmoneyError(
                    f"Eastmoney returned conflicting {table} rows for {symbol} "
                    f"{period_iso} with the same availability date"
                )

        return _frame(table, [value[1] for value in latest_by_key.values()])

    def get_financials(
        self,
        table: str,
        symbols: list[str],
        latest_only: bool = False,
    ) -> pl.DataFrame:
        """Fetch the latest available 8-quarter history for a financial table."""
        if table == "shares":
            return pl.DataFrame()
        if table not in _TABLE_SCHEMAS:
            return pl.DataFrame()
        requested = {value for value in (_symbol(item) for item in symbols) if value}
        if not requested:
            return _empty_frame(table)

        collected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for period in _recent_completed_quarters(cn_today()):
            period_rows = self.fetch_period(table, period, sorted(requested))
            if table == "metrics" and not period_rows.is_empty():
                balance_rows = self.fetch_period(
                    "balance_sheet", period, sorted(requested)
                )
                period_rows = merge_metrics_with_balance(period_rows, balance_rows)
            records = period_rows.to_dicts()
            collected.extend(records)
            seen.update(record["symbol"] for record in records)
            if latest_only and requested.issubset(seen):
                break

        if latest_only:
            newest_by_symbol: dict[str, dict[str, Any]] = {}
            for record in collected:
                previous = newest_by_symbol.get(record["symbol"])
                if previous is None or record["period_end"] > previous["period_end"]:
                    newest_by_symbol[record["symbol"]] = record
            collected = list(newest_by_symbol.values())
        return _frame(table, collected)
