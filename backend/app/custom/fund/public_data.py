"""Free Eastmoney fund disclosures. No credentials, disk writes or JS execution.

Public website responses are not a guaranteed API. Each section fails independently;
missing fields stay null. All percentages are percentage points, not fractions.
"""

from __future__ import annotations

import calendar
import json
import math
import re
import threading
import time
from datetime import UTC, date, datetime, timedelta, timezone
from html.parser import HTMLParser

import httpx

_REQUEST_SLOTS = threading.BoundedSemaphore(4)
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://fundf10.eastmoney.com/"}
HORIZONS = {"1m": 31, "3m": 93, "6m": 186, "1y": 366, "2y": 732, "3y": 1098}


def fund_code(thscode: str) -> str:
    code = thscode.strip().upper().removesuffix(".OF")
    if not re.fullmatch(r"[0-9]{6}", code):
        raise ValueError("仅支持六位场外基金代码")
    return code


def iso_date(value) -> str | None:
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def number(value) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _get(url: str, params: dict | None = None) -> httpx.Response:
    # Bounded across users as well as the 16-fund screen. No automatic retries.
    if not _REQUEST_SLOTS.acquire(timeout=2):
        raise TimeoutError("基金公开数据源忙")
    try:
        response = httpx.get(url, params=params, headers=_HEADERS, timeout=8, follow_redirects=True)
        response.raise_for_status()
        return response
    finally:
        _REQUEST_SLOTS.release()


class _Tables(HTMLParser):
    """Extract h4 + table rows without optional HTML/parser dependencies."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[tuple[str, list[list[str]]]] = []
        self.title = ""
        self._title: list[str] | None = None
        self._rows: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "h4":
            self._title = []
        elif tag == "table":
            self._rows = []
        elif tag == "tr" and self._rows is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data):
        if self._title is not None:
            self._title.append(data)
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag == "h4" and self._title is not None:
            self.title = " ".join("".join(self._title).split())
            self._title = None
        elif tag in {"td", "th"} and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self._rows.append(self._row)
            self._row = None
        elif tag == "table" and self._rows is not None:
            self.tables.append((self.title, self._rows))
            self._rows = None


def parse_fees(html: str, code: str) -> dict:
    tables = _Tables()
    tables.feed(html)
    out = {
        "status": "unavailable", "management_pct": None, "custody_pct": None,
        "sales_service_pct": None, "subscription_rules": [], "redemption_rules": [],
        "source_url": f"https://fundf10.eastmoney.com/jjfl_{code}.html", "as_of": None,
        "note": "运作费率为年费率; 交易费按金额/持有期限变化。平台优惠不代表所有渠道, 生效日期未提供。",
    }
    labels = {"管理费率": "management_pct", "托管费率": "custody_pct", "销售服务费率": "sales_service_pct"}
    for title, rows in tables.tables:
        if title == "运作费用":
            cells = [cell for row in rows for cell in row]
            for index, cell in enumerate(cells[:-1]):
                if cell in labels:
                    match = re.search(r"([0-9.]+)\s*%", cells[index + 1])
                    out[labels[cell]] = number(match[1]) if match else None
        elif title.startswith("申购费率") or title.startswith("赎回费率"):
            target = "subscription_rules" if title.startswith("申购") else "redemption_rules"
            title = re.match(r"(?:申购|赎回)费率(?:\uFF08[^\uFF09]+\uFF09)?", title)[0]
            for row in rows:
                if len(row) < 2 or not any("%" in cell or "每笔" in cell for cell in row[1:]):
                    continue
                # Keep fixed amount fees and all purchase channels verbatim, never cast to %.
                rate = row[1].split("|")
                out[target].append({
                    "condition": f"{title} · {row[0]}", "rate_text": rate[0].strip(),
                    "discount_text": " | ".join(rate[1:] + row[2:]).strip() or None,
                })
    fields = [out[key] for key in labels.values()]
    available = any(value is not None for value in fields) or bool(out["subscription_rules"] or out["redemption_rules"])
    out["status"] = "ok" if all(value is not None for value in fields) else "partial" if available else "unavailable"
    return out


def parse_holdings(html: str, code: str) -> dict:
    parser = _Tables()
    parser.feed(html)
    periods = []
    for title, rows in parser.tables:
        match = re.search(r"截止至?\s*(\d{4}-\d{2}-\d{2})", title)
        report_date = iso_date(match[1]) if match else None
        if report_date is None:
            quarter = re.search(r"(\d{4})年([1-4])季度", title)
            if quarter:
                year, month = int(quarter[1]), int(quarter[2]) * 3
                report_date = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
        if not report_date or not rows:
            continue
        headers = [re.sub(r"\s+", "", cell) for cell in rows[0]]
        if "股票代码" not in headers or "占净值比例" not in headers:
            continue
        items = []
        for row in rows[1:]:
            values = dict(zip(headers, row, strict=False))
            symbol = values.get("股票代码", "")
            weight = number(values.get("占净值比例", "").removesuffix("%"))
            if weight is None or weight < 0 or weight > 100:
                continue
            if re.fullmatch(r"\d{6}", symbol):
                suffix = "SH" if symbol.startswith(("6", "9")) else "BJ" if symbol.startswith(("4", "8", "92")) else "SZ"
            elif re.fullmatch(r"\d{5}", symbol):
                suffix = "HK"
            else:
                continue
            items.append({"thscode": f"{symbol}.{suffix}", "name": values.get("股票名称", ""),
                          "hold_ratio": weight, "asset_type": "stock"})
        periods.append((report_date, items))
    report_date, items = max(periods, key=lambda value: value[0]) if periods else (None, [])
    # Duplicate rows must not double count exposure.
    items = list({row["thscode"]: row for row in items}.values())
    items.sort(key=lambda row: row["hold_ratio"], reverse=True)
    weight = sum(row["hold_ratio"] for row in items)
    if weight > 100.1:
        items = []
    return {
        "status": "ok" if items else "unavailable", "report_date": report_date,
        "publication_date": None, "related_reports": [], "items": items,
        "coverage_weight_pct": round(weight, 4) if items else None,
        "source_url": f"https://fundf10.eastmoney.com/ccmx_{code}.html",
        "report_note": "公开披露股票持仓, 非实时; 不含未披露持仓/债券/现金。公告日期与持仓表的对应关系未核实。",
    }


def _archive_content(text: str) -> str:
    match = re.search(r"\bcontent\s*:\s*", text)
    if not match:
        raise ValueError("持仓响应缺少 content")
    # Decode the quoted JSON string only. Never evaluate the remote JavaScript.
    content, _ = json.JSONDecoder().raw_decode(text[match.end():])
    if not isinstance(content, str):
        raise ValueError("持仓 content 不是文本")
    return content


def parse_risk(payload: dict, code: str, horizon: str) -> dict:
    out = {"status": "unavailable", "basis": None, "max_drawdown_pct": None,
           "start_date": None, "end_date": None, "observations": 0,
           "source_url": f"https://fund.eastmoney.com/{code}.html",
           "note": "按来源累计收益曲线计算区间观测最大回撤; 采样可能稀疏, 可能低估每日最大回撤; 不是基金终身回撤。"}
    data = (payload.get("Data") or []) if isinstance(payload, dict) else []
    if not isinstance(data, list):
        data = []
    # Series 0 is the fund; never substitute the benchmark series.
    rows = data[0].get("data", []) if data and isinstance(data[0], dict) else []
    if not isinstance(rows, list):
        rows = []
    values = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        ts, growth = number(row[0]), number(row[1])
        if ts is None or growth is None or growth <= -100:
            continue
        try:
            day = datetime.fromtimestamp(ts / 1000, tz=UTC).astimezone(timezone(timedelta(hours=8))).date()
        except (ValueError, OverflowError, OSError):
            continue
        values[day] = 1 + growth / 100
    if not values:
        return out
    end = max(values)
    cutoff = end - timedelta(days=HORIZONS[horizon])
    series = sorted((day, value) for day, value in values.items() if day >= cutoff)
    if len(series) < 2:
        return out
    peak = series[0][1]
    drawdown = 0.0
    for _, value in series:
        peak = max(peak, value)
        drawdown = max(drawdown, (peak - value) / peak)
    out.update(status="ok", basis="source_return_series", max_drawdown_pct=round(drawdown * 100, 4),
               start_date=series[0][0].isoformat(), end_date=end.isoformat(), observations=len(series))
    return out


def _related_reports(rows: list[dict], report_date: str | None) -> list[dict]:
    out = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("TITLE") or "")
        year = re.search(r"(\d{4})年", title)
        if not year:
            continue
        quarter = re.search(r"第?([1-4])季度", title)
        month = int(quarter[1]) * 3 if quarter else 6 if "中期" in title or "半年度" in title else 12 if "年度报告" in title else None
        if month is None:
            continue
        period = date(int(year[1]), month, calendar.monthrange(int(year[1]), month)[1]).isoformat()
        publication = iso_date(row.get("PUBLISHDATE"))
        report_id = str(row.get("ID") or "")
        if period != report_date or not publication or not re.fullmatch(r"AN\d+", report_id):
            continue
        out.append({"title": title, "report_date": period, "publication_date": publication,
                    "source_url": f"https://fund.eastmoney.com/gonggao/{row.get('FUNDCODE', '')},{report_id}.html"})
    return out[:3]


def fetch_research(thscode: str, horizon: str = "1y") -> dict:
    code = fund_code(thscode)
    if horizon not in HORIZONS:
        raise ValueError("不支持的研究区间")
    warnings = []
    fees = parse_fees("", code)
    holdings = parse_holdings("", code)
    risk = parse_risk({}, code, horizon)
    try:
        fees = parse_fees(_get(fees["source_url"]).text, code)
    except (httpx.HTTPError, ValueError, TimeoutError):
        warnings.append("公告费率暂不可用")
    try:
        response = _get("https://fundf10.eastmoney.com/FundArchivesDatas.aspx",
                        {"type": "jjcc", "code": code, "topline": "100", "year": "", "month": ""})
        holdings = parse_holdings(_archive_content(response.text), code)
    except (httpx.HTTPError, ValueError, TimeoutError):
        warnings.append("披露持仓暂不可用")
    try:
        period = {"1m": "m", "3m": "q", "6m": "hy", "1y": "y", "2y": "try", "3y": "try"}[horizon]
        risk = parse_risk(_get("https://api.fund.eastmoney.com/pinzhong/LJSYLZS",
                              {"fundCode": code, "indexcode": "000300", "type": period}).json(), code, horizon)
    except (httpx.HTTPError, ValueError, TimeoutError):
        warnings.append("累计收益回撤暂不可用")
    if holdings["report_date"]:
        try:
            reports = _get("https://api.fund.eastmoney.com/f10/JJGG",
                           {"fundcode": code, "pageIndex": "1", "pageSize": "30", "type": "3"}).json()
            holdings["related_reports"] = _related_reports(reports.get("Data") or [] if isinstance(reports, dict) else [], holdings["report_date"])
        except (httpx.HTTPError, ValueError, TimeoutError):
            warnings.append("相关公告日期暂不可用")
    missing = ["统一榜单统计截止日", "持仓表对应的确切发布日期", "费率生效日期"]
    for key, label in (("management_pct", "管理费"), ("custody_pct", "托管费"), ("sales_service_pct", "销售服务费")):
        if fees[key] is None:
            missing.append(label)
    if not fees["subscription_rules"]:
        missing.append("申购费规则")
    if not fees["redemption_rules"]:
        missing.append("赎回费规则")
    if risk["status"] != "ok":
        missing.append("区间回撤")
    if holdings["status"] != "ok":
        missing.append("披露股票持仓")
    return {"thscode": f"{code}.OF", "source": "eastmoney", "retrieved_at_ms": round(time.time() * 1000),
            "horizon": horizon, "nav_date": None, "fees": fees, "risk": risk,
            "holdings": holdings, "missing_fields": missing, "warnings": warnings}
