"""基金中心业务层: 字段归一 + TTL 缓存 + 自选持久化。

字段归一口径 (与 docs/plugin-development.md 一致):
- 百分数原值 (如 1.74) → 小数制 (0.0174)
- volume: 股/份 → 手 (/100)
- 时间: 毫秒戳 → 北京时间日期 (date, naive)
- 缺字段 → None, 不伪造
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_BEIJING = timezone(timedelta(hours=8))

# fuyao asset_type → 本模块资产种类 (展示用, 不混入核心 AssetType)
ASSET_KIND_LABEL = {
    "fund-etf": "ETF",
    "fund-lof": "LOF",
    "fund-otc": "场外基金",
    "fund-reits": "REITs",
}


def _to_float(v) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _pct_to_decimal(v) -> float | None:
    """百分数原值 → 小数制。None 透传, 不做启发式。"""
    f = _to_float(v)
    return f / 100.0 if f is not None else None


def _beijing_date(ms) -> str | None:
    """毫秒戳 → 北京时间日期字符串 (YYYY-MM-DD)。"""
    try:
        ms = int(ms)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=_BEIJING).date().isoformat()


def map_quote(row: dict, name: str | None = None) -> dict | None:
    """场内基金快照行 → 标准 quote。"""
    symbol = row.get("thscode")
    if not symbol:
        return None
    last = _to_float(row.get("last_price"))
    prev = _to_float(row.get("prev_price"))
    change_pct = _pct_to_decimal(row.get("price_change_ratio_pct"))
    change_amount = _to_float(row.get("price_change"))
    if change_amount is None and last is not None and prev is not None:
        change_amount = last - prev
    if change_pct is None and change_amount is not None and prev not in (None, 0):
        change_pct = change_amount / prev
    volume = _to_float(row.get("volume"))
    return {
        "thscode": symbol,
        "symbol": symbol,
        "name": name,
        "last_price": last,
        "prev_close": prev,
        "open": _to_float(row.get("open_price")),
        "high": _to_float(row.get("high_price")),
        "low": _to_float(row.get("low_price")),
        "change_pct": change_pct,
        "change_amount": change_amount,
        "volume": math.floor(volume / 100.0) if volume is not None else None,
        "amount": _to_float(row.get("turnover")),
        "amplitude": _pct_to_decimal(row.get("price_amplitude_ratio_pct")),
        "turnover_rate": _pct_to_decimal(row.get("turnover_ratio_pct")),
        "timestamp": row.get("timestamp"),
    }


def map_kline(rows: list[dict]) -> dict:
    """场内基金历史日线 → 标准 K 线 (上游为前复权, 如实标注)。"""
    bars = []
    for r in rows:
        d = _beijing_date(r.get("date_ms") or r.get("date"))
        if not d:
            continue
        vol = _to_float(r.get("volume"))
        bars.append(
            {
                "date": d,
                "open": _to_float(r.get("open_price")),
                "high": _to_float(r.get("high_price")),
                "low": _to_float(r.get("low_price")),
                "close": _to_float(r.get("close_price")),
                "volume": math.floor(vol / 100.0) if vol is not None else None,
                "amount": _to_float(r.get("turnover")),
            }
        )
    bars.sort(key=lambda b: b["date"])
    return {"adjusted": "forward", "bars": bars}


def map_nav(rows: list[dict]) -> list[dict]:
    """基金净值序列 → [{nav_date, unit_nav, adj_nav}] (升序)。"""
    out = []
    for r in rows:
        d = _beijing_date(r.get("nav_date"))
        if d is None:
            continue
        out.append(
            {
                "nav_date": d,
                "unit_nav": _to_float(r.get("unit_nav")),
                "adj_nav": _to_float(r.get("adj_nav")),
            }
        )
    out.sort(key=lambda x: x["nav_date"])
    return out


# fuyao 返回的错误/截断基金名修正表 (经 akshare 核验)
# akshare-proxy (127.0.0.1:3019) 会动态补充, 这里是启动时的已知修正。
FUND_NAME_OVERRIDES = {
    "021959.OF": "南方黄金股指数C",
}

_AKSHARE_PROXY = "http://127.0.0.1:3019"


def _akshare_name(thscode: str) -> str | None:
    """经 akshare-proxy 查询基金正确名称, 失败返回 None。"""
    import urllib.request
    import urllib.parse

    code = thscode.split(".")[0]
    # 先查本地表
    if thscode in FUND_NAME_OVERRIDES:
        return FUND_NAME_OVERRIDES[thscode]
    try:
        url = f"{_AKSHARE_PROXY}/fund/name?code={urllib.parse.quote(code)}"
        req = urllib.request.Request(url, headers={"User-Agent": "tsp/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
            name = data.get("name")
            if name:
                FUND_NAME_OVERRIDES[thscode] = name  # 记入缓存
                return name
    except Exception:
        pass
    return None


def map_profile(row: dict) -> dict:
    """基金资料行 → 标准资料 (字段缺失置 None)。"""
    thscode = row.get("thscode")
    ak_name = _akshare_name(thscode) if thscode else None
    return {
        "thscode": thscode,
        "ticker": row.get("ticker"),
        "fund_name": ak_name or row.get("fund_name"),
        "estab_date": _beijing_date(row.get("estab_date")),
        "mgmt_name": row.get("mgmt_name"),
        "manager_name": row.get("manager_name"),
        "fund_type": row.get("fund_type"),
        "benchmark": row.get("benchmark"),
    }


# ---------------------------------------------------------------------------
# 场外基金盘中估值 (穿透自算): 持仓加权 × 实时行情
# 口径: 估算涨跌幅 = Σ(持仓权重% / 100 × 个股当日涨跌幅%)
#       估算净值 = 昨日单位净值 × (1 + 估算涨跌幅)
# 局限 (必须如实标注): 持仓为季报披露非实时; 港股/债券等无 A 股实时行情
# 的持仓不纳入, 覆盖权重不足 20% 时视为估算失败。
# ---------------------------------------------------------------------------

_QQ_QUOTE_URL = "https://qt.gtimg.cn/q="
_QQ_MINUTE_URL = "https://ifzq.gtimg.cn/appstock/app/minute/query"
_QQ_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
# 穿透估算最小覆盖权重 (参考 fund-advisor 的 min_covered_weight=0.2)
_MIN_COVERED_WEIGHT = 0.20


def _to_qq_symbol(thscode: str) -> str | None:
    """内部代码 → 腾讯代码。沪深 A 股 (600519.SH→sh600519), 港股 (0700.HK→hk00700)。"""
    if not thscode:
        return None
    s = thscode.strip().upper()
    if "." in s:
        code, suffix = s.split(".", 1)
    else:
        return None
    if suffix == "HK":
        if not code.isdigit():
            return None
        return f"hk{code.zfill(5)}"
    prefix = {"SH": "sh", "SZ": "sz"}.get(suffix)
    if prefix is None or not code.isdigit():
        return None
    return f"{prefix}{code}"


def map_holdings(data: dict) -> dict:
    """持仓数据 → {stock_ratio_pct, items: [{thscode, name, hold_ratio, asset_type}]}。

    hold_ratio 为百分数原值 (如 5.72), 保持原值不转小数 (前端直接展示 %)。
    A 股 + 港股纳入穿透估算 (腾讯 q= 均有实时行情), 债券/基金等标注跳过。
    """
    items = []
    skipped = []
    for r in data.get("item") or []:
        thscode = r.get("thscode") or ""
        name = r.get("stock_name") or ""
        ratio = _to_float(r.get("hold_ratio"))
        asset_type = r.get("asset_type") or ""
        if not thscode or ratio is None:
            continue
        qq = _to_qq_symbol(thscode)
        if asset_type == "stock" and qq:
            items.append(
                {
                    "thscode": thscode,
                    "name": name,
                    "hold_ratio": ratio,
                    "asset_type": asset_type,
                }
            )
        else:
            skipped.append({"thscode": thscode, "name": name, "hold_ratio": ratio,
                            "asset_type": asset_type, "reason": "无实时行情"})
    # 按权重降序
    items.sort(key=lambda x: x["hold_ratio"], reverse=True)
    return {
        "stock_ratio_pct": _to_float(data.get("stock_ratio_pct")),
        "total_stock_ratio_pct": _to_float(data.get("total_stock_ratio_pct")),
        "concentration_ratio": _to_float(data.get("concentration_ratio")),
        "report_note": "季报披露持仓，非实时",
        "report_date": None,
        "publication_date": None,
        "coverage_weight_pct": round(sum(row["hold_ratio"] for row in items), 4),
        "items": items,
        "skipped": skipped,
    }


def _qq_batch_quotes(thscode_list: list[str], timeout: float = 15.0) -> dict[str, dict]:
    """腾讯 q= 批量行情 → {thscode: {last, prev_close, change_pct}}。

    change_pct 为小数制 (如 0.0123 表示 +1.23%)。
    用 httpx (后端已有依赖); urllib 经代理访问腾讯会 RemoteDisconnected。
    """
    import re

    import httpx

    qq_map: dict[str, str] = {}  # qq_symbol -> thscode
    for t in thscode_list:
        q = _to_qq_symbol(t)
        if q:
            qq_map[q] = t
    if not qq_map:
        return {}
    out: dict[str, dict] = {}
    keys = list(qq_map)
    line_re = re.compile(r'v_([a-z]{2}\d+)="([^"]*)"')
    try:
        http = httpx.Client(timeout=timeout, headers={"User-Agent": _QQ_UA})
    except Exception as e:
        logger.warning("httpx 初始化失败: %s", e)
        return {}
    with http:
        for i in range(0, len(keys), 60):
            chunk = keys[i:i + 60]
            try:
                resp = http.get(_QQ_QUOTE_URL + ",".join(chunk))
            except httpx.HTTPError as e:
                logger.warning("腾讯批量行情失败: %s", e)
                continue
            if resp.status_code != 200:
                logger.warning("腾讯批量行情 HTTP %s", resp.status_code)
                continue
            text = resp.content.decode("gbk", errors="ignore")
            for m in line_re.finditer(text):
                qq_sym, body = m.group(1), m.group(2)
                ths = qq_map.get(qq_sym)
                if not ths:
                    continue
                f = body.split("~")
                if len(f) < 31:
                    continue
                last = _to_float(f[3])
                prev = _to_float(f[4])
                if last is None or prev in (None, 0):
                    continue
                out[ths] = {
                    "last": last,
                    "prev_close": prev,
                    "change_pct": (last - prev) / prev,
                }
    return out


def _qq_minute_today(thscode: str, timeout: float = 15.0) -> list[dict]:
    """腾讯当日分时 → [{time(HHMM), price}]。"""
    import urllib.parse

    import httpx

    qq = _to_qq_symbol(thscode)
    if not qq:
        return []
    try:
        url = f"{_QQ_MINUTE_URL}?code={urllib.parse.quote(qq)}"
        with httpx.Client(timeout=timeout, headers={"User-Agent": _QQ_UA}) as http:
            resp = http.get(url)
        payload = resp.json()
    except Exception as e:
        logger.warning("腾讯分时 %s 失败: %s", thscode, e)
        return []
    out = []
    try:
        node = payload["data"][qq]
    except (KeyError, TypeError):
        return []
    # A股: data[qq].qt.qt = [[time, price, ...], ...]
    # 港股: data[qq].data.data = ["HHMM price vol amt", ...]
    rows = None
    try:
        rows = node["qt"]["qt"]
        is_hk = False
    except (KeyError, TypeError):
        pass
    if rows is None:
        try:
            rows = node["data"]["data"]
            is_hk = True
        except (KeyError, TypeError):
            return []
    for r in rows:
        if is_hk:
            # "0930 441.400 380150 167797970.000"
            parts = str(r).split()
            if len(parts) < 2:
                continue
            t, price = parts[0], _to_float(parts[1])
        else:
            if not isinstance(r, list) or len(r) < 2:
                continue
            t, price = str(r[0]), _to_float(r[1])
        if price is None:
            continue
        out.append({"time": t, "price": price})
    return out


def estimate_nav(
    holdings: dict,
    prev_nav: float | None,
    quotes: dict[str, dict] | None = None,
) -> dict:
    """穿透估算: 持仓加权 × 实时行情 → {change_pct, est_nav, covered_weight, ...}。

    holdings: map_holdings() 的输出; quotes: _qq_batch_quotes() 的输出 (可外部传入)。
    change_pct 为小数制。覆盖权重不足 _MIN_COVERED_WEIGHT 时 status=insufficient。
    """
    items = holdings.get("items") or []
    if quotes is None:
        quotes = _qq_batch_quotes([it["thscode"] for it in items])
    covered = 0.0
    weighted = 0.0
    details = []
    for it in items:
        q = quotes.get(it["thscode"])
        if not q:
            continue
        w = it["hold_ratio"] / 100.0
        chg = q["change_pct"]
        covered += w
        weighted += w * chg
        details.append({
            "thscode": it["thscode"],
            "name": it["name"],
            "hold_ratio": it["hold_ratio"],
            "change_pct": chg,
            "contrib_pct": w * chg,  # 对估算涨跌幅的贡献 (小数制)
        })
    details.sort(key=lambda d: abs(d["contrib_pct"]), reverse=True)
    if not items or covered < _MIN_COVERED_WEIGHT:
        return {
            "status": "insufficient",
            "note": f"实时行情覆盖权重 {covered*100:.1f}% 不足 {_MIN_COVERED_WEIGHT*100:.0f}%，估算不可用",
            "covered_weight": covered,
            "details": details,
        }
    est_nav = prev_nav * (1 + weighted) if prev_nav else None
    return {
        "status": "ok",
        "note": "穿透估算（季报持仓加权），误差较大，仅供参考",
        "change_pct": weighted,
        "est_nav": est_nav,
        "prev_nav": prev_nav,
        "covered_weight": covered,
        "details": details,
    }


def estimate_curve(
    holdings: dict,
    prev_nav: float | None,
    as_of_date: str | None = None,
) -> dict:
    """当日估值走势: 每只持仓股的当日分时按权重加权 → 分钟级估值曲线。

    返回 {status, points: [{time, est_nav, change_pct}], ...}。
    time 为 HHMM 字符串。任一持仓股分时失败则跳过该股。
    """
    from datetime import date

    items = holdings.get("items") or []
    if not items:
        return {"status": "insufficient", "note": "无可用持仓", "points": []}
    today = as_of_date or date.today().isoformat()
    # 取每只持仓股的当日分时 + 昨收 (分时并行拉取, 10只约 12s → 2s)
    series: dict[str, list[dict]] = {}
    prev_map: dict[str, float] = {}
    quotes = _qq_batch_quotes([it["thscode"] for it in items])
    valid = [(it["thscode"], quotes[it["thscode"]]["prev_close"]) for it in items if quotes.get(it["thscode"])]
    if valid:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            fut_map = {pool.submit(_qq_minute_today, ths): (ths, prev) for ths, prev in valid}
            for fut in concurrent.futures.as_completed(fut_map):
                ths, prev = fut_map[fut]
                try:
                    mins = fut.result()
                except Exception:
                    continue
                if mins:
                    series[ths] = mins
                    prev_map[ths] = prev
    if not series:
        return {"status": "insufficient", "note": "持仓股分时数据不可用", "points": []}
    # 按权重归一化 (只计入有分时的持仓)
    weights = {}
    total_w = 0.0
    for it in items:
        if it["thscode"] in series:
            w = it["hold_ratio"] / 100.0
            weights[it["thscode"]] = w
            total_w += w
    if total_w < _MIN_COVERED_WEIGHT:
        return {"status": "insufficient",
                "note": f"分时覆盖权重 {total_w*100:.1f}% 不足", "points": []}
    # 时间轴取并集，按 HHMM 排序
    times = sorted({p["time"] for mins in series.values() for p in mins})
    # 每只股的分时转 dict 方便查找 (缺失分钟用前值填充)
    price_map: dict[str, dict[str, float]] = {}
    for ths, mins in series.items():
        pm = {p["time"]: p["price"] for p in mins}
        price_map[ths] = pm
    points = []
    last_px: dict[str, float] = {}
    for t in times:
        weighted_chg = 0.0
        for ths, w in weights.items():
            pm = price_map[ths]
            px = pm.get(t)
            if px is None:
                px = last_px.get(ths)
            if px is None:
                continue
            last_px[ths] = px
            prev = prev_map[ths]
            weighted_chg += w * (px - prev) / prev
        # 按总权重归一 (未覆盖部分视为 0 涨跌)
        chg = weighted_chg
        est = prev_nav * (1 + chg) if prev_nav else None
        points.append({"time": t, "change_pct": chg, "est_nav": est})
    return {
        "status": "ok",
        "note": "穿透估算（季报持仓加权），误差较大，仅供参考",
        "date": today,
        "covered_weight": total_w,
        "points": points,
    }


def akshare_search(q: str, limit: int = 20) -> list[dict]:
    """经本机基金代理搜索; 网络失败与零结果必须区分。"""
    payload = _akshare_proxy_json("/fund/search", {"q": q, "limit": limit})
    return payload.get("items") or []


def _akshare_proxy_json(path: str, params: dict[str, str | int], timeout: int = 15) -> dict:
    """从本机基金代理读取数据; 调用方决定缺数据时如何降级。"""
    import urllib.parse
    import urllib.request

    url = f"{_AKSHARE_PROXY}{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "tsp/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


_NAV_RANGE_DAYS = {
    "week": 7,
    "month": 31,
    "tmonth": 92,
    "hyear": 183,
    "year": 366,
    "twoyear": 732,
    "tyear": 1098,
    "fyear": 1464,
}


def akshare_nav(thscode: str, range_: str) -> list[dict]:
    """场外基金单位净值回退。代理不提供复权净值, 故 adj_nav 保持 None。"""
    if not thscode.endswith(".OF"):
        return []
    rows = _akshare_proxy_json(
        "/fund/nav", {"code": thscode.split(".")[0]}, timeout=20
    ).get("nav") or []
    return _map_public_nav(rows, range_)


def eastmoney_nav(thscode: str, range_: str) -> list[dict]:
    from app.custom.fund.public_nav import fetch_nav

    if not thscode.endswith(".OF"):
        return []
    return _map_public_nav(fetch_nav(thscode), range_)


def _map_public_nav(rows: list[dict], range_: str) -> list[dict]:
    from datetime import date

    out = []
    for row in rows:
        raw_date = str(row.get("date") or "")[:10]
        try:
            nav_date = date.fromisoformat(raw_date)
        except ValueError:
            continue
        unit_nav = _to_float(row.get("unit_nav"))
        if unit_nav is None or unit_nav <= 0:
            continue
        out.append({"nav_date": nav_date.isoformat(), "unit_nav": unit_nav, "adj_nav": None})
    out.sort(key=lambda row: row["nav_date"])
    if not out:
        return []
    cutoff = date.fromisoformat(out[-1]["nav_date"]) - timedelta(days=_NAV_RANGE_DAYS[range_])
    return [row for row in out if date.fromisoformat(row["nav_date"]) >= cutoff]


def akshare_profile(thscode: str) -> dict | None:
    """场外基金基本资料回退; 不可得字段不推测。"""
    from datetime import date

    if not thscode.endswith(".OF"):
        return None
    row = _akshare_proxy_json(
        "/fund/profile", {"code": thscode.split(".")[0]}
    ).get("profile") or {}
    if not row:
        return None
    raw_date = str(row.get("estab_date") or "")[:10]
    try:
        estab_date = date.fromisoformat(raw_date).isoformat()
    except ValueError:
        estab_date = None
    return {
        "thscode": thscode,
        "ticker": thscode.split(".")[0],
        "fund_name": row.get("fund_name"),
        "estab_date": estab_date,
        "mgmt_name": row.get("mgmt_name"),
        "manager_name": row.get("manager_name"),
        "fund_type": row.get("fund_type"),
        "benchmark": row.get("benchmark"),
    }


def akshare_rank(fund_type: str, sort_by: str = "1y", limit: int = 200) -> list[dict]:
    """基金历史收益榜单; 百分数字段保持百分数原值。"""
    source = "akshare"
    try:
        payload = _akshare_proxy_json(
            "/fund/rank", {"type": fund_type, "sort_by": sort_by, "limit": limit}, timeout=30
        )
        items = payload.get("items") or []
        if not isinstance(items, list) or not items:
            raise ValueError("Local fund rank returned no rows")
    except Exception:
        from app.custom.fund.public_nav import fetch_rank

        logger.warning("Local fund rank unavailable; trying Eastmoney public source")
        items = fetch_rank(fund_type)
        source = "eastmoney"
    if not isinstance(items, list):
        return []
    out = []
    for row in items:
        code = str(row.get("code") or "").strip()
        name = str(row.get("name") or "").strip()
        if not code.isdigit() or len(code) != 6 or not name:
            continue
        item = {"code": code, "name": name, "share_class": name[-1] if name[-1] in "AC" else ""}
        item["source"] = source
        from app.custom.fund.public_data import iso_date

        item["nav"] = _to_float(row.get("nav"))
        item["nav_date"] = iso_date(row.get("nav_date"))
        fee = str(row.get("purchase_fee_text") or "").strip()
        item["purchase_fee_text"] = fee if fee not in {"", "nan", "None", "—"} else None
        for period in ("1w", "1m", "3m", "6m", "1y", "2y", "3y"):
            value = row.get(f"growth_{period}")
            if isinstance(value, str):
                value = value.strip().removesuffix("%").replace(",", "")
            item[f"growth_{period}"] = _to_float(value)
        out.append(item)
    field = f"growth_{sort_by}"
    out.sort(key=lambda row: row.get(field) if row.get(field) is not None else float("-inf"), reverse=True)
    return out[:max(1, min(limit, 200))]


def fund_research(thscode: str, horizon: str = "1y") -> dict:
    """Credential-free, date-labelled public fund disclosures and research statistics."""
    from app.custom.fund.public_data import fetch_research

    return fetch_research(thscode, horizon)


def research_model_context(research: dict | None) -> dict | None:
    """Bound LLM input while the API/UI retain the complete disclosed list."""
    if research is None:
        return None
    holdings = research["holdings"]
    items = holdings.get("items") or []
    return {**research, "holdings": {
        **holdings, "items": items[:10], "available_holdings_count": len(items),
        "model_items_weight_pct": round(sum(row["hold_ratio"] for row in items[:10]), 4),
        "model_note": "模型仅收到权重前10只; coverage_weight_pct 为全表已披露股票权重, 不等于模型所列合计。",
    }}


def map_search(rows: list[dict], skip_akshare: bool = False) -> list[dict]:
    """检索结果 → [{thscode, ticker, name, asset_type, kind_label}]。

    skip_akshare=True 时跳过逐条 AkShare 名称修正 (AkShare 本地搜索返回的
    名称已是完整简称，无需再查；fuyao 结果仍需修正截断名)。
    """
    out = []
    for r in rows:
        thscode = r.get("thscode")
        if not thscode:
            continue
        at = r.get("asset_type") or ""
        if skip_akshare:
            name = r.get("name") or r.get("fund_name")
        else:
            ak_name = _akshare_name(thscode)
            name = ak_name or r.get("name") or r.get("fund_name")
        out.append(
            {
                "thscode": thscode,
                "ticker": r.get("ticker"),
                "name": name,
                "asset_type": at,
                "kind_label": ASSET_KIND_LABEL.get(at, at),
            }
        )
    return out


class TTLCache:
    """线程安全 TTL 缓存 (key → (expire_ts, value))。"""

    def __init__(self, max_entries: int | None = None) -> None:
        if max_entries is not None and max_entries < 1:
            raise ValueError("max_entries must be positive")
        self._lock = threading.Lock()
        self._store: dict[str, tuple[float, object]] = {}
        self._max_entries = max_entries

    def get(self, key: str):
        with self._lock:
            hit = self._store.get(key)
            if hit is None:
                return None
            exp, val = hit
            if exp < time.monotonic():
                del self._store[key]
                return None
            return val

    def set(self, key: str, value: object, ttl_s: float) -> None:
        with self._lock:
            now = time.monotonic()
            if self._max_entries is not None:
                for stale in [key for key, (expires, _) in self._store.items() if expires <= now]:
                    del self._store[stale]
                self._store.pop(key, None)
                while len(self._store) >= self._max_entries:
                    del self._store[next(iter(self._store))]
            self._store[key] = (now + ttl_s, value)

    def invalidate(self, prefix: str) -> None:
        with self._lock:
            for k in [k for k in self._store if k.startswith(prefix)]:
                del self._store[k]


def _watchlist_path() -> Path:
    from app.config import settings

    p = Path(settings.data_dir) / "user_data" / "fund_watchlist.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_watchlist() -> list[dict]:
    p = _watchlist_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("fund_watchlist.json 读取失败: %s", e)
        return []


def save_watchlist(items: list[dict]) -> None:
    p = _watchlist_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _portfolio_path() -> Path:
    from app.config import settings

    p = Path(settings.data_dir) / "user_data" / "fund_portfolio.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_portfolio() -> list[dict]:
    """持仓列表: [{thscode, name, amount, profit}]。"""
    p = _portfolio_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("fund_portfolio.json 读取失败: %s", e)
        return []


def save_portfolio(items: list[dict]) -> None:
    p = _portfolio_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
