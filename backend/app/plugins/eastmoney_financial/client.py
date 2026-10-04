"""Small HTTP client for Eastmoney's public financial-report endpoint."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date, datetime
from threading import Lock
from typing import Any

ENDPOINT = "https://datacenter-web.eastmoney.com/api/data/v1/get"
USER_AGENT = "Mozilla/5.0"
PAGE_SIZE = 500
MAX_PAGES_PER_PERIOD = 100
REQUEST_INTERVAL_S = 0.3

REPORT_NAMES = {
    "income": "RPT_DMSK_FN_INCOME",
    "balance_sheet": "RPT_DMSK_FN_BALANCE",
    "cash_flow": "RPT_DMSK_FN_CASHFLOW",
    "metrics": "RPT_LICO_FN_CPD",
}


class EastmoneyError(RuntimeError):
    """An Eastmoney response was invalid or a request failed."""


def _period_text(period: str | date) -> str:
    if isinstance(period, datetime):
        period = period.date()
    if isinstance(period, date):
        return period.isoformat()
    try:
        return date.fromisoformat(str(period)).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid report period: {period!r}") from exc


def _filter_for(table: str, period: str) -> str:
    if table == "metrics":
        return (
            f'(REPORTDATE=\'{period}\')'
            '(SECURITY_TYPE_CODE in ("058001001","058001008"))'
        )
    return f"(REPORT_DATE='{period}')"


class EastmoneyClient:
    """Fetch one complete report-period result, paging serially and fail-closed."""

    def __init__(
        self,
        *,
        http_client: Any | None = None,
        endpoint: str = ENDPOINT,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        if http_client is None:
            import httpx

            self._http = httpx.Client(
                headers={"User-Agent": USER_AGENT},
                timeout=30.0,
            )
            self._owns_http = True
        else:
            self._http = http_client
            self._owns_http = False
        self._endpoint = endpoint
        self._sleep = sleep or time.sleep
        self._has_requested = False
        self._request_lock = Lock()

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def fetch_period(self, table: str, period: str | date) -> list[dict[str, Any]]:
        """Return all rows for one report and period, or raise without partial rows."""
        if table not in REPORT_NAMES:
            raise ValueError(f"unsupported Eastmoney financial table: {table}")
        period_iso = _period_text(period)
        common = {
            "reportName": REPORT_NAMES[table],
            "columns": "ALL",
            "pageSize": PAGE_SIZE,
            "filter": _filter_for(table, period_iso),
            "sortColumns": "SECURITY_CODE",
            "sortTypes": "1",
        }

        rows: list[dict[str, Any]] = []
        expected_pages: int | None = None
        expected_count: int | None = None
        page_number = 1
        while expected_pages is None or page_number <= expected_pages:
            payload = self._get_page({**common, "pageNumber": page_number})
            if self._is_empty_period(payload):
                if page_number == 1:
                    return []
                raise EastmoneyError(
                    f"Eastmoney returned an empty period midway through {table} {period_iso}"
                )
            result = self._validated_result(payload, table, period_iso, page_number)
            page_count = self._page_count(result, table, period_iso)
            row_count = self._row_count(result, table, period_iso)

            if expected_pages is None:
                if page_count > MAX_PAGES_PER_PERIOD:
                    raise EastmoneyError(
                        f"Eastmoney {table} {period_iso} needs {page_count} pages; "
                        f"limit is {MAX_PAGES_PER_PERIOD}"
                    )
                if page_count == 0 or row_count == 0:
                    if page_count == 0 and row_count == 0 and result.get("data") == []:
                        return []
                    raise EastmoneyError(
                        f"Eastmoney returned inconsistent empty pagination for {table} {period_iso}"
                    )
                expected_page_count = (row_count + PAGE_SIZE - 1) // PAGE_SIZE
                if page_count != expected_page_count:
                    raise EastmoneyError(
                        f"Eastmoney count/pages mismatch for {table} {period_iso}: "
                        f"count={row_count}, pages={page_count}, expected_pages={expected_page_count}"
                    )
                expected_pages = page_count
                expected_count = row_count
            elif page_count != expected_pages:
                raise EastmoneyError(
                    f"Eastmoney pagination changed during {table} {period_iso}: "
                    f"{expected_pages} to {page_count} pages"
                )
            elif row_count != expected_count:
                raise EastmoneyError(
                    f"Eastmoney count changed during {table} {period_iso}: "
                    f"{expected_count} to {row_count} rows"
                )

            page_rows = result.get("data")
            if not isinstance(page_rows, list) or any(not isinstance(row, dict) for row in page_rows):
                raise EastmoneyError(
                    f"Eastmoney returned invalid data rows for {table} {period_iso} page {page_number}"
                )
            expected_page_rows = min(PAGE_SIZE, expected_count - (page_number - 1) * PAGE_SIZE)
            if len(page_rows) != expected_page_rows:
                raise EastmoneyError(
                    f"Eastmoney returned an incomplete page for {table} {period_iso} page "
                    f"{page_number}: got {len(page_rows)}, expected {expected_page_rows}"
                )
            rows.extend(page_rows)
            page_number += 1

        if len(rows) != expected_count:
            raise EastmoneyError(
                f"Eastmoney returned an incomplete result for {table} {period_iso}: "
                f"got {len(rows)} rows, expected {expected_count}"
            )
        return rows

    def _get_page(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._request_lock:
            if self._has_requested:
                self._sleep(REQUEST_INTERVAL_S)
            self._has_requested = True
            try:
                response = self._http.get(
                    self._endpoint,
                    params=params,
                    headers={"User-Agent": USER_AGENT},
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                raise EastmoneyError(f"Eastmoney request failed: {type(exc).__name__}") from exc
        if not isinstance(payload, dict):
            raise EastmoneyError("Eastmoney returned a non-object response")
        return payload

    @staticmethod
    def _is_empty_period(payload: dict[str, Any]) -> bool:
        return payload.get("success") is False and str(payload.get("code")) == "9201"

    @staticmethod
    def _validated_result(
        payload: dict[str, Any], table: str, period: str, page_number: int
    ) -> dict[str, Any]:
        if payload.get("success") is not True or payload.get("code") not in (0, "0"):
            raise EastmoneyError(
                f"Eastmoney rejected {table} {period} page {page_number}: "
                f"code={payload.get('code')}"
            )
        result = payload.get("result")
        if not isinstance(result, dict):
            raise EastmoneyError(
                f"Eastmoney returned no result for {table} {period} page {page_number}"
            )
        return result

    @staticmethod
    def _page_count(result: dict[str, Any], table: str, period: str) -> int:
        try:
            raw_pages = result.get("pages")
            if isinstance(raw_pages, bool):
                raise ValueError("boolean page count")
            pages = int(raw_pages)
            if isinstance(raw_pages, float) and not raw_pages.is_integer():
                raise ValueError("fractional page count")
        except (TypeError, ValueError) as exc:
            raise EastmoneyError(
                f"Eastmoney returned invalid page count for {table} {period}"
            ) from exc
        if pages < 0:
            raise EastmoneyError(f"Eastmoney returned negative page count for {table} {period}")
        return pages

    @staticmethod
    def _row_count(result: dict[str, Any], table: str, period: str) -> int:
        try:
            raw_count = result.get("count")
            if isinstance(raw_count, bool):
                raise ValueError("boolean count")
            count = int(raw_count)
            if isinstance(raw_count, float) and not raw_count.is_integer():
                raise ValueError("fractional count")
        except (TypeError, ValueError, OverflowError) as exc:
            raise EastmoneyError(
                f"Eastmoney returned invalid row count for {table} {period}"
            ) from exc
        if count < 0:
            raise EastmoneyError(f"Eastmoney returned negative row count for {table} {period}")
        return count
