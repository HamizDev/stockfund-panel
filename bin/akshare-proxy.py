#!/usr/bin/env python3
"""akshare 代理服务: 给 stockfund-panel 后端提供基金净值/资料。

端口: 127.0.0.1:3019
接口:
  GET /fund/name?code=021959          → {"code": "021959", "name": "南方黄金股指数C"}
  GET /fund/nav?code=021959           → {"code": "021959", "nav": [{date, unit_nav, growth}]}
  GET /fund/profile?code=021959       → {"code": "021959", "profile": {...}}
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import akshare as ak

PORT = 3019


def _ensure_fund_list() -> list[tuple[str, str]]:
    """全市场基金 (code, 简称) 列表，缓存 24 小时。"""
    global _FUND_LIST_CACHE, _FUND_LIST_TS
    import time
    now = time.time()
    if _FUND_LIST_CACHE is None or now - _FUND_LIST_TS > 86400:
        df = ak.fund_open_fund_daily_em()
        _FUND_LIST_CACHE = [
            (str(r["基金代码"]).strip(), str(r["基金简称"]).strip())
            for _, r in df.iterrows()
        ]
        _FUND_LIST_TS = now
    return _FUND_LIST_CACHE or []


def fund_name(code: str) -> str | None:
    # 先查本地全市场列表 (快)，查不到再调个股接口 (慢)
    try:
        for c, name in _ensure_fund_list():
            if c == code:
                return name
    except Exception:
        pass
    try:
        df = ak.fund_individual_basic_info_xq(symbol=code)
        row = df[df.iloc[:, 0] == "基金名称"]
        if not row.empty:
            return str(row.iloc[0, 1]).strip()
    except Exception:
        pass
    return None


# 全市场基金列表缓存 (code, name)，用于本地模糊搜索
_FUND_LIST_CACHE: list[tuple[str, str]] | None = None
_FUND_LIST_TS: float = 0


def fund_search(q: str, limit: int = 20) -> list[dict]:
    """本地模糊搜索: 基金代码或名称包含 q (不区分大小写)。"""
    q = q.strip().lower()
    if not q:
        return []
    try:
        fund_list = _ensure_fund_list()
    except Exception:
        return []
    out = []
    for code, name in fund_list:
        if q in code.lower() or q in name.lower():
            out.append({"thscode": f"{code}.OF", "name": name, "asset_type": "fund-otc"})
            if len(out) >= limit:
                break
    return out


def fund_nav(code: str) -> list[dict]:
    try:
        df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        out = []
        for _, r in df.iterrows():
            out.append({
                "date": str(r.get("净值日期", "")),
                "unit_nav": float(r.get("单位净值", 0) or 0),
                "growth": str(r.get("日增长率", "")),
            })
        return out
    except Exception:
        return []


def fund_profile(code: str) -> dict:
    try:
        df = ak.fund_individual_basic_info_xq(symbol=code)
        d = {}
        for _, r in df.iterrows():
            d[str(r.iloc[0]).strip()] = str(r.iloc[1]).strip()
        return {
            "fund_name": d.get("基金名称"),
            "fund_full_name": d.get("基金全称"),
            "estab_date": d.get("成立时间"),
            "fund_type": d.get("基金类型"),
            "mgmt_name": d.get("基金公司"),
            "manager_name": d.get("基金经理"),
            "benchmark": d.get("业绩比较基准"),
        }
    except Exception:
        return {}


_RANK_CACHE: dict = {}
_RANK_TS: dict = {}


_RANK_SORT_FIELDS = {"1w", "1m", "3m", "6m", "1y", "2y", "3y"}


def fund_rank(fund_type: str, limit: int = 100, sort_by: str = "1y") -> list[dict]:
    """基金排名 (缓存 6 小时)。返回代码、名称、近1周/1月/3月/6月/1年等。"""
    import time
    now = time.time()
    key = fund_type
    if key not in _RANK_CACHE or now - _RANK_TS.get(key, 0) > 21600:
        df = ak.fund_open_fund_rank_em(symbol=fund_type)
        items = []
        for _, r in df.iterrows():
            items.append({
                "code": str(r["基金代码"]).strip(),
                "name": str(r["基金简称"]).strip(),
                "nav": r.get("单位净值"),
                "nav_date": str(r.get("日期", "")),
                "purchase_fee_text": str(r.get("手续费", "")),
                "growth_1w": r.get("近1周"),
                "growth_1m": r.get("近1月"),
                "growth_3m": r.get("近3月"),
                "growth_6m": r.get("近6月"),
                "growth_1y": r.get("近1年"),
                "growth_2y": r.get("近2年"),
                "growth_3y": r.get("近3年"),
            })
        _RANK_CACHE[key] = items
        _RANK_TS[key] = now
    items = _RANK_CACHE[key]
    # 在全量榜单上排序后截取, 避免短期筛选被近一年榜单预先裁掉。
    field = f"growth_{sort_by}" if sort_by in _RANK_SORT_FIELDS else "growth_1y"
    def _sort_key(x):
        v = x.get(field)
        try:
            value = float(str(v).replace("%", ""))
            return value if value == value else float("-inf")
        except (TypeError, ValueError):
            return float("-inf")
    sorted_items = sorted(items, key=_sort_key, reverse=True)
    return sorted_items[:max(1, min(200, limit))]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path == "/fund/search":
            q = (qs.get("q") or [""])[0].strip()
            try:
                limit = int((qs.get("limit") or ["20"])[0])
            except ValueError:
                limit = 20
            try:
                self._send(200, {"q": q, "items": fund_search(q, limit)})
            except Exception as e:
                self._send(500, {"error": str(e)[:200]})
            return
        if parsed.path == "/fund/rank":
            # 基金排名: 按选定历史区间排序后截取候选。
            fund_type = (qs.get("type") or ["股票型"])[0].strip()
            sort_by = (qs.get("sort_by") or ["1y"])[0].strip()
            try:
                limit = int((qs.get("limit") or ["100"])[0])
            except ValueError:
                limit = 100
            try:
                self._send(200, {"type": fund_type, "items": fund_rank(fund_type, limit, sort_by)})
            except Exception as e:
                self._send(500, {"error": str(e)[:200]})
            return
        code = (qs.get("code") or [""])[0].strip()
        if not code:
            self._send(400, {"error": "missing code"})
            return
        # 去掉 .OF 后缀
        code = code.split(".")[0]
        try:
            if parsed.path == "/fund/name":
                self._send(200, {"code": code, "name": fund_name(code)})
            elif parsed.path == "/fund/nav":
                self._send(200, {"code": code, "nav": fund_nav(code)})
            elif parsed.path == "/fund/profile":
                self._send(200, {"code": code, "profile": fund_profile(code)})
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)[:200]})

    def _send(self, status: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    srv = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"akshare proxy on 127.0.0.1:{PORT}", flush=True)
    srv.serve_forever()
