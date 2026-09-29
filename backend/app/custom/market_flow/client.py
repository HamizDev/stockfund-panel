"""shy313.com market_flow 免费接口客户端。

接口数据为低频, 服务端缓存 30 分钟。
失败软返回空列表, 不抛异常阻断页面。
"""
from __future__ import annotations

import logging
import threading
import time

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://shy313.com/api/plugins/market_flow/exports"
_CACHE_TTL_S = 30 * 60

_cache: dict[str, tuple[float, list[dict]]] = {}
_cache_lock = threading.Lock()


def _fetch(endpoint: str) -> list[dict]:
    """拉取单个接口, 带 30 分钟缓存。失败返回 []。"""
    now = time.time()
    with _cache_lock:
        hit = _cache.get(endpoint)
        if hit and hit[0] > now:
            return hit[1]
    try:
        resp = httpx.get(f"{BASE_URL}/{endpoint}", timeout=20)
        resp.raise_for_status()
        data = resp.json()
        rows = data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("market_flow 接口 %s 拉取失败: %s", endpoint, e)
        return []
    with _cache_lock:
        _cache[endpoint] = (now + _CACHE_TTL_S, rows)
    return rows


def _pct_to_decimal(v) -> float | None:
    """上游 change_pct 为百分数 (如 1.74), 转小数制 (0.0174)。"""
    if v is None:
        return None
    try:
        return float(v) / 100
    except (TypeError, ValueError):
        return None


def get_popularity(limit: int = 100) -> list[dict]:
    """人气排行。"""
    rows = _fetch("popularity")
    out = []
    for r in rows:
        out.append({
            "rank": r.get("rank"),
            "symbol": r.get("symbol"),
            "name": r.get("name"),
            "heat": r.get("heat"),
            "change_pct": _pct_to_decimal(r.get("change_pct")),
        })
        if len(out) >= limit:
            break
    return out


def get_money_flow(limit: int = 100) -> list[dict]:
    """资金流向 (全市场)。"""
    rows = _fetch("money-flow")
    out = []
    for r in rows:
        out.append({
            "rank": r.get("rank"),
            "symbol": r.get("symbol"),
            "name": r.get("name"),
            "net": r.get("net"),
            "inflow": r.get("inflow"),
            "outflow": r.get("outflow"),
            "change_pct": _pct_to_decimal(r.get("change_pct")),
        })
        if len(out) >= limit:
            break
    return out


def get_money_flow_main(limit: int = 100) -> list[dict]:
    """主力资金。"""
    rows = _fetch("money-flow-main")
    out = []
    for r in rows:
        out.append({
            "rank": r.get("rank"),
            "symbol": r.get("symbol"),
            "name": r.get("name"),
            "net": r.get("net"),
            "buy": r.get("buy"),
            "sell": r.get("sell"),
            "change_pct": _pct_to_decimal(r.get("change_pct")),
        })
        if len(out) >= limit:
            break
    return out


def get_concepts(symbol: str) -> list[str]:
    """个股同花顺概念列表。symbol 格式如 600900.SH。"""
    rows = _fetch("ths-concepts")
    for r in rows:
        if r.get("symbol") == symbol:
            concepts = r.get("concepts")
            return list(concepts) if isinstance(concepts, list) else []
    return []


def get_industries(symbol: str) -> list[str]:
    """个股同花顺行业列表。"""
    rows = _fetch("ths-industries")
    for r in rows:
        if r.get("symbol") == symbol:
            industries = r.get("industries")
            return list(industries) if isinstance(industries, list) else []
    return []


def _find_in(rows: list[dict], symbol: str) -> dict | None:
    for r in rows:
        if r.get("symbol") == symbol:
            return r
    return None


def get_stock_flow(symbol: str) -> dict:
    """单只股票的市场数据聚合: 人气排名/资金流向/主力资金/概念/行业。"""
    pop = _find_in(_fetch("popularity"), symbol)
    mf = _find_in(_fetch("money-flow"), symbol)
    mfm = _find_in(_fetch("money-flow-main"), symbol)
    return {
        "symbol": symbol,
        "popularity": {
            "rank": pop.get("rank"),
            "heat": pop.get("heat"),
            "change_pct": _pct_to_decimal(pop.get("change_pct")),
        } if pop else None,
        "money_flow": {
            "rank": mf.get("rank"),
            "net": mf.get("net"),
            "inflow": mf.get("inflow"),
            "outflow": mf.get("outflow"),
            "change_pct": _pct_to_decimal(mf.get("change_pct")),
        } if mf else None,
        "money_flow_main": {
            "rank": mfm.get("rank"),
            "net": mfm.get("net"),
            "buy": mfm.get("buy"),
            "sell": mfm.get("sell"),
            "change_pct": _pct_to_decimal(mfm.get("change_pct")),
        } if mfm else None,
        "concepts": get_concepts(symbol),
        "industries": get_industries(symbol),
    }


def get_concept_members(concept: str, limit: int = 200) -> list[dict]:
    """概念成分股反查: 返回属于该概念的股票列表。"""
    rows = _fetch("ths-concepts")
    out = []
    for r in rows:
        concepts = r.get("concepts")
        if isinstance(concepts, list) and concept in concepts:
            out.append({"symbol": r.get("symbol"), "name": r.get("name")})
            if len(out) >= limit:
                break
    return out


def get_industry_members(industry: str, limit: int = 200) -> list[dict]:
    """行业成分股反查。"""
    rows = _fetch("ths-industries")
    out = []
    for r in rows:
        industries = r.get("industries")
        if isinstance(industries, list) and industry in industries:
            out.append({"symbol": r.get("symbol"), "name": r.get("name")})
            if len(out) >= limit:
                break
    return out
