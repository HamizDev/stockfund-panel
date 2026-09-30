"""基金中心 API 路由 (prefix: /api/custom/fund)。

鉴权由全局 auth_middleware 统一处理 (/api/* 需登录)。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import AsyncIterable, AsyncIterator
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.custom.fund import service as svc
from app.custom.fund.client import FundError, FundNotConfiguredError, FuyaoFundClient

logger = logging.getLogger(__name__)

_cache = svc.TTLCache()
_client_lock = threading.Lock()
_client: FuyaoFundClient | None = None

_NAV_RANGES = {"week", "month", "tmonth", "hyear", "year", "twoyear", "tyear", "fyear"}
_MAX_KLINE_DAYS = 5 * 365


async def _ndjson_lines(chunks: AsyncIterable[str]) -> AsyncIterator[str]:
    # Heartbeat chunks have no newline; separate them from the next JSON event.
    async for chunk in chunks:
        yield chunk if chunk.endswith("\n") else chunk + "\n"


def _client_or_503() -> FuyaoFundClient:
    client = _optional_client()
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="扶摇未配置 API Key; 请在设置页配置, 或使用本机基金净值数据。",
        )
    return client


def _optional_client() -> FuyaoFundClient | None:
    global _client
    with _client_lock:
        if _client is None:
            try:
                _client = FuyaoFundClient()
            except FundNotConfiguredError:
                return None
        return _client


def _watchlist_names() -> dict[str, str]:
    try:
        return {w["thscode"]: w.get("name", "") for w in svc.load_watchlist()}
    except Exception:
        return {}


class WatchAddIn(BaseModel):
    thscode: str
    name: str | None = None
    asset_type: str | None = None


class PortfolioItemIn(BaseModel):
    thscode: str
    name: str | None = None
    amount: float = 0.0
    profit: float = 0.0


class AnalyzeIn(BaseModel):
    thscode: str
    focus: str = ""


class AiPickIn(BaseModel):
    fund_type: Literal["股票型", "混合型", "指数型", "债券型"] = "混合型"
    horizon: Literal["1m", "3m", "6m", "1y", "2y", "3y"] = "1y"
    share: Literal["all", "A", "C"] = "all"


_AI_PICK_PROMPT = """你是公募基金研究助手。
仅根据提供的历史收益榜单, 从候选中挑选最多 5 只值得进一步研究的基金, 并说明依据与局限。

严格要求:
- 只能引用候选清单内的代码、名称和历史收益数字。
  不得编造费率、回撤、规模、持仓、经理、净值日期或未来收益。
- 数据源没有提供榜单统计截止日期, 明确写出这一限制。
- A/C 份额不得仅凭名称假定费率; 同一基金的不同份额应指出需核对实际费用。
- 不能给买入、卖出或具体仓位建议; 历史涨幅不是未来收益预测。
- 输出 Markdown: 先写筛选范围与数据限制, 再列出最多 5 只研究候选。
  每只候选含代码、名称、引用的数值和待核对风险; 最后列出未入选的主要原因和数据缺口。
- 如果候选数据不足, 请如实说明, 不要为了凑数而挑选。
"""


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/custom/fund", tags=["custom-fund"])

    @router.get("/search")
    def search(
        q: str = Query(..., min_length=1, max_length=40),
        types: str = Query("fund-otc,fund-etf,fund-lof"),
        limit: int = Query(20, ge=1, le=50),
    ) -> dict:
        """基金/ETF 检索: q=代码或名称子串。fuyao 搜不到时用 AkShare 本地补充。"""
        client = _optional_client()
        rows = []
        if client is not None:
            try:
                rows = client.search(q.strip(), asset_types=types, limit=limit)
            except Exception as e:
                logger.warning("Fuyao fund search failed: %s", e)
        # fuyao 中文模糊搜索弱 (如"南方信息"返回 0 条)，用 AkShare 本地补充
        # (AkShare 返回的名称已是完整简称，跳过逐条修正，省 30+s)
        if not rows:
            try:
                ak_rows = svc.akshare_search(q.strip(), limit=limit)
            except Exception as e:
                logger.warning("Local fund search failed: %s", e)
                if client is None:
                    raise HTTPException(
                        status_code=503,
                        detail="基金检索不可用: 本机基金数据服务未响应, 且扶摇未配置 API Key",
                    ) from e
                ak_rows = []
            if ak_rows:
                return {"items": svc.map_search(ak_rows, skip_akshare=True)}
        return {"items": svc.map_search(rows)}

    @router.get("/screener")
    def screener(
        fund_type: str = Query("股票型", description="股票型/混合型/指数型/债券型"),
        share: str = Query("all", description="all/A/C"),
        sort_by: str = Query("1y", description="1w/1m/3m/6m/1y/2y/3y"),
        limit: int = Query(50, ge=10, le=200),
        recommend: bool = Query(False, description="是否返回历史收益加权榜单(短期C+长期A)"),
    ) -> dict:
        """基金历史收益筛选。

        手动筛选: 按类型 + A/C + 排序指标返回列表。
        加权榜单 (recommend=true): 短期C类与长期A类分别从相应区间榜单选取。
        数据来源: AkShare 东方财富排名 (缓存 6 小时)。
        """
        try:
            items = svc.akshare_rank(fund_type, sort_by="1y" if recommend else sort_by, limit=200)
            short_items = svc.akshare_rank(fund_type, sort_by="1m", limit=200) if recommend else []
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"排名数据获取失败: {e}") from e

        def _num(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        if recommend:
            # 历史收益加权; 缺任何输入都不以 0 伪装。
            short_candidates = []
            for it in short_items:
                if it["share_class"] != "C":
                    continue
                m1, m3, m6 = _num(it.get("growth_1m")), _num(it.get("growth_3m")), _num(it.get("growth_6m"))
                if m1 is None or m3 is None or m6 is None:
                    continue
                score = m1 * 0.5 + m3 * 0.3 + m6 * 0.2
                it["short_score"] = round(score, 2)
                reasons = []
                if m1 > 5:
                    reasons.append(f"近1月+{m1:.1f}%")
                if m3 > 10:
                    reasons.append(f"近3月+{m3:.1f}%")
                if not reasons:
                    reasons.append(f"近1月{m1:+.1f}%, 近3月{m3:+.1f}%")
                it["reason"] = ", ".join(reasons) + "; 按历史收益加权"
                short_candidates.append(it)
            short_candidates.sort(key=lambda x: x["short_score"], reverse=True)

            long_candidates = []
            for it in items:
                if it["share_class"] != "A":
                    continue
                y1, y2, y3 = _num(it.get("growth_1y")), _num(it.get("growth_2y")), _num(it.get("growth_3y"))
                if y1 is None or y2 is None or y3 is None:
                    continue
                score = y1 * 0.4 + y2 * 0.3 + y3 * 0.3
                it["long_score"] = round(score, 2)
                reasons = []
                if y1 and y1 > 20:
                    reasons.append(f"近1年+{y1:.1f}%")
                if y3 and y3 > 50:
                    reasons.append(f"近3年+{y3:.1f}%")
                if not reasons:
                    reasons.append(f"近1年{y1:+.1f}%")
                it["reason"] = ", ".join(reasons) + "; 按历史收益加权"
                long_candidates.append(it)
            long_candidates.sort(key=lambda x: x["long_score"], reverse=True)

            return {
                "fund_type": fund_type,
                "mode": "recommend",
                "short_term": short_candidates[:10],
                "long_term": long_candidates[:10],
            }
        else:
            # 手动筛选模式
            sort_map = {
                "1w": "growth_1w", "1m": "growth_1m", "3m": "growth_3m",
                "6m": "growth_6m", "1y": "growth_1y", "2y": "growth_2y", "3y": "growth_3y",
            }
            sort_field = sort_map.get(sort_by, "growth_1y")

            filtered = []
            for it in items:
                if share != "all" and it["share_class"] != share.upper():
                    continue
                v = _num(it.get(sort_field))
                if v is None:
                    continue
                it["_sort_v"] = v
                filtered.append(it)

            filtered.sort(key=lambda x: x["_sort_v"], reverse=True)
            for it in filtered:
                it.pop("_sort_v", None)

            return {
                "fund_type": fund_type,
                "mode": "manual",
                "share": share,
                "sort_by": sort_by,
                "count": len(filtered[:limit]),
                "items": filtered[:limit],
            }

    @router.post("/screener/ai")
    async def ai_pick(req: AiPickIn):
        """从当前历史收益榜单生成研究候选, 沿用已配置的 AI 模型。"""
        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import StreamingResponse

        from app.services.ai_provider import stream_ai_text
        from app.services.ndjson_heartbeat import with_heartbeat

        try:
            rows = await run_in_threadpool(
                svc.akshare_rank, req.fund_type, sort_by=req.horizon, limit=200
            )
        except Exception as e:
            logger.warning("Fund ranking unavailable for AI pick: %s", e)
            raise HTTPException(status_code=502, detail="基金历史收益榜单暂不可用") from e
        field = f"growth_{req.horizon}"
        candidates = [
            row
            for row in rows
            if row[field] is not None
            and (req.share == "all" or row["share_class"] == req.share)
        ][:16]
        if not candidates:
            raise HTTPException(status_code=422, detail="当前筛选条件下没有可核对收益数据的基金")

        async def _gen():
            import json

            yield json.dumps(
                {
                    "type": "meta",
                    "candidates": candidates,
                    "fund_type": req.fund_type,
                    "horizon": req.horizon,
                    "share": req.share,
                    "source": "东方财富基金排名 (经 AKShare)",
                    "retrieved_at_ms": round(time.time() * 1000),
                    "data_as_of": None,
                },
                ensure_ascii=False,
            ) + "\n"
            try:
                async for delta in stream_ai_text(
                    [
                        {"role": "system", "content": _AI_PICK_PROMPT},
                        {"role": "user", "content": json.dumps({
                            "fund_type": req.fund_type, "horizon": req.horizon,
                            "share": req.share, "candidates": candidates,
                        }, ensure_ascii=False)},
                    ],
                    temperature=0.2,
                    max_tokens=None,
                    prefer_final_answer=True,
                ):
                    yield json.dumps(
                        {"type": "delta", "content": delta}, ensure_ascii=False
                    ) + "\n"
            except Exception:
                logger.exception("AI fund pick failed")
                yield json.dumps(
                    {"type": "error", "message": "AI 选基暂不可用, 请检查模型设置"},
                    ensure_ascii=False,
                ) + "\n"
                return
            yield '{"type":"done"}\n'

        return StreamingResponse(_ndjson_lines(with_heartbeat(_gen())), media_type="application/x-ndjson")

    @router.get("/watchlist")
    def watchlist() -> dict:
        items = svc.load_watchlist()
        # 读取时动态修正名称 (自选存的是添加时的旧名, 可能是 fuyao 截断名)
        for it in items:
            thscode = it.get("thscode") or ""
            if thscode:
                ak_name = svc._akshare_name(thscode)
                if ak_name:
                    it["name"] = ak_name
        return {"items": items}

    @router.post("/watchlist")
    def watchlist_add(req: WatchAddIn) -> dict:
        thscode = req.thscode.strip().upper()
        if not thscode:
            raise HTTPException(status_code=400, detail="thscode 不能为空")
        items = svc.load_watchlist()
        if not any(w.get("thscode") == thscode for w in items):
            items.append(
                {
                    "thscode": thscode,
                    "name": req.name,
                    "asset_type": req.asset_type,
                    "kind_label": svc.ASSET_KIND_LABEL.get(req.asset_type or "", ""),
                    "added_at": int(time.time()),
                }
            )
            svc.save_watchlist(items)
        return {"items": items}

    @router.delete("/watchlist/{thscode}")
    def watchlist_del(thscode: str) -> dict:
        items = [w for w in svc.load_watchlist() if w.get("thscode") != thscode.upper()]
        svc.save_watchlist(items)
        return {"items": items}

    @router.get("/portfolio")
    def portfolio() -> dict:
        """持仓列表: [{thscode, name, amount, profit}]。"""
        return {"items": svc.load_portfolio()}

    @router.put("/portfolio")
    def portfolio_put(req: list[PortfolioItemIn]) -> dict:
        """全量更新持仓 (前端编辑后整体提交)。"""
        items = []
        for r in req:
            thscode = r.thscode.strip().upper()
            if not thscode:
                continue
            items.append(
                {
                    "thscode": thscode,
                    "name": r.name,
                    "amount": r.amount,
                    "profit": r.profit,
                }
            )
        svc.save_portfolio(items)
        return {"items": items}

    @router.delete("/portfolio/{thscode}")
    def portfolio_del(thscode: str) -> dict:
        items = [w for w in svc.load_portfolio() if w.get("thscode") != thscode.upper()]
        svc.save_portfolio(items)
        return {"items": items}

    @router.get("/quote/{thscode}")
    def quote(thscode: str) -> dict:
        """场内基金(ETF/LOF)实时快照。场外基金返回 null(无场内行情)。"""
        thscode = thscode.strip().upper()
        key = f"quote:{thscode}"
        hit = _cache.get(key)
        if hit is not None:
            return {"quote": hit}
        client = _client_or_503()
        try:
            row = client.snapshot(thscode)
        except FundError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        q = svc.map_quote(row, name=_watchlist_names().get(thscode)) if row else None
        _cache.set(key, q, ttl_s=15)
        return {"quote": q}

    @router.get("/kline/{thscode}")
    def kline(
        thscode: str, days: int = Query(250, ge=5, le=_MAX_KLINE_DAYS)
    ) -> dict:
        """场内基金历史日线。上游为前复权口径, 如实标注 adjusted=forward。"""
        thscode = thscode.strip().upper()
        key = f"kline:{thscode}:{days}"
        hit = _cache.get(key)
        if hit is not None:
            return hit
        client = _client_or_503()
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - days * 86400 * 1000
        try:
            rows = client.kline(thscode, start_ms, end_ms)
        except FundError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        out = {"thscode": thscode, **svc.map_kline(rows)}
        _cache.set(key, out, ttl_s=3600)
        return out

    @router.get("/nav/{thscode}")
    def nav(
        thscode: str, range: str = Query("year"), nav_type: str = Query("unit,adj")
    ) -> dict:
        """基金净值序列 (场外基金 T-1 净值; ETF 也可用)。"""
        thscode = thscode.strip().upper()
        if range not in _NAV_RANGES:
            raise HTTPException(status_code=400, detail=f"range 非法: {range}")
        key = f"nav:{thscode}:{range}:{nav_type}"
        hit = _cache.get(key)
        if hit is not None:
            return hit
        client = _optional_client()
        mapped = []
        source = "fuyao"
        if client is not None:
            try:
                mapped = svc.map_nav(client.nav(thscode, range_=range, nav_type=nav_type))
            except Exception as e:
                logger.warning("Fuyao fund NAV failed for %s: %s", thscode, e)
        if not mapped and thscode.endswith(".OF"):
            try:
                mapped = svc.akshare_nav(thscode, range)
                source = "akshare"
            except Exception as e:
                logger.warning("Local fund NAV failed for %s: %s", thscode, e)
        if not mapped:
            raise HTTPException(
                status_code=503,
                detail=(
                    "基金净值不可用: 本机基金数据服务未返回净值, 且扶摇未配置 API Key"
                    if client is None else "基金净值不可用: 扶摇与本机基金数据服务均未返回可用净值"
                ),
            )
        out = {"thscode": thscode, "nav": mapped, "source": source}
        _cache.set(key, out, ttl_s=3600)
        return out

    @router.get("/profile/{thscode}")
    def profile(thscode: str) -> dict:
        """基金基本资料。"""
        thscode = thscode.strip().upper()
        key = f"profile:{thscode}"
        hit = _cache.get(key)
        if hit is not None:
            return hit
        client = _optional_client()
        p = None
        source = "fuyao"
        if client is not None:
            try:
                row = client.profile(thscode)
                p = svc.map_profile(row) if row else None
            except Exception as e:
                logger.warning("Fuyao fund profile failed for %s: %s", thscode, e)
        if p is None and thscode.endswith(".OF"):
            try:
                p = svc.akshare_profile(thscode)
                source = "akshare"
            except Exception as e:
                logger.warning("Local fund profile failed for %s: %s", thscode, e)
        if p is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "基金资料不可用: 本机基金数据服务未响应, 且扶摇未配置 API Key"
                    if client is None else "基金资料不可用: 扶摇与本机基金数据服务均未返回资料"
                ),
            )
        out = {"profile": p, "source": source}
        _cache.set(key, out, ttl_s=86400)
        return out

    @router.get("/holdings/{thscode}")
    def holdings(thscode: str) -> dict:
        """基金重仓持仓 (季报披露, 非实时)。持仓缓存 24h。"""
        thscode = thscode.strip().upper()
        key = f"holdings:{thscode}"
        hit = _cache.get(key)
        if hit is not None:
            return hit
        client = _client_or_503()
        try:
            data = client.holdings(thscode)
        except FundError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        out = {"thscode": thscode, **svc.map_holdings(data)}
        _cache.set(key, out, ttl_s=86400)
        return out

    @router.get("/estimate/{thscode}")
    def estimate(thscode: str) -> dict:
        """场外基金当日盘中估值 (穿透自算)。

        口径: 季报前十大重仓 × A股实时行情加权。
        估算涨跌幅/估算净值 + 分钟级估值走势。缓存 60s。
        """
        thscode = thscode.strip().upper()
        key = f"estimate:{thscode}"
        hit = _cache.get(key)
        if hit is not None:
            return hit
        client = _client_or_503()
        # holdings 和 nav 并行; holdings 复用 /holdings 接口的 1 天缓存
        import concurrent.futures

        hkey = f"holdings:{thscode}"
        hhit = _cache.get(hkey)

        def _get_holdings():
            if hhit is not None:
                return hhit
            data = client.holdings(thscode)
            out = {"thscode": thscode, **svc.map_holdings(data)}
            _cache.set(hkey, out, ttl_s=86400)
            return out

        def _get_nav():
            try:
                rows = client.nav(thscode, range_="week", nav_type="unit")
                nav_list = svc.map_nav(rows)
                if nav_list:
                    return nav_list[-1]["unit_nav"], nav_list[-1]["nav_date"]
            except FundError:
                pass
            return None, None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            fut_hold = pool.submit(_get_holdings)
            fut_nav = pool.submit(_get_nav)
            try:
                h_out = fut_hold.result()
            except FundError as e:
                raise HTTPException(status_code=502, detail=str(e)) from e
            holdings = {"thscode": thscode, "items": h_out["items"], "total_weight": h_out.get("total_weight")}
            prev_nav, nav_date = fut_nav.result()
        # estimate_nav 和 estimate_curve 并行 (各约 2-4s, 串行要 6-8s)
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            fut_est = pool.submit(svc.estimate_nav, holdings, prev_nav)
            fut_curve = pool.submit(svc.estimate_curve, holdings, prev_nav)
            est = fut_est.result()
            curve = fut_curve.result()
        out = {
            "thscode": thscode,
            "prev_nav": prev_nav,
            "nav_date": nav_date,
            "estimate": est,
            "curve": curve,
            "holdings": holdings,
        }
        _cache.set(key, out, ttl_s=60)
        return out

    @router.post("/analyze")
    async def analyze(req: AnalyzeIn):
        """AI 基金分析 (流式 NDJSON，与 /api/stock-analysis/analyze 协议一致)。"""
        from fastapi.responses import StreamingResponse

        from app.custom.fund import analyzer as fund_analyzer
        from app.services.ndjson_heartbeat import with_heartbeat

        thscode = req.thscode.strip().upper()
        client = _optional_client()

        # 与详情页共用 Fuyao → 本机代理回退及缓存口径。
        try:
            nav_rows = nav(thscode, range="year", nav_type="unit,adj")["nav"]
        except HTTPException:
            nav_rows = []

        # 资料
        try:
            profile_data = profile(thscode)["profile"]
        except HTTPException:
            profile_data = None

        # 名称 (自选表 > AkShare > thscode)
        name = _watchlist_names().get(thscode) or svc._akshare_name(thscode) or thscode

        # 持仓 (用于穿透分析)
        holdings = None
        if client is not None:
            try:
                h_data = client.holdings(thscode)
                holdings = svc.map_holdings(h_data)
            except FundError:
                pass

        # 当日估值 (用于估值分析, 失败不阻塞)
        estimate = None
        try:
            if holdings:
                prev_nav = nav[-1]["unit_nav"] if nav else None
                est = svc.estimate_nav(holdings, prev_nav)
                estimate = {"estimate": est}
        except Exception:
            pass

        async def _gen():
            async for chunk in fund_analyzer.analyze_fund_stream(
                nav_rows, profile_data, thscode, name, req.focus or "",
                holdings=holdings, estimate=estimate,
            ):
                yield chunk + "\n"

        return StreamingResponse(_ndjson_lines(with_heartbeat(_gen())), media_type="application/x-ndjson")

    return router
