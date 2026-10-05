"""基金中心 API 路由 (prefix: /api/custom/fund)。

鉴权由全局 auth_middleware 统一处理 (/api/* 需登录)。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import AsyncIterable, AsyncIterator
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.custom.fund import service as svc
from app.custom.fund.client import FundError, FundNotConfiguredError, FuyaoFundClient
from app.services import holdings_review

logger = logging.getLogger(__name__)

_cache = svc.TTLCache()
_research_cache = svc.TTLCache(max_entries=96)
_research_locks = [threading.Lock() for _ in range(16)]
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
    fund_type: Literal["all", "股票型", "混合型", "指数型", "债券型"] = "all"
    horizon: Literal["1m", "3m", "6m", "1y", "2y", "3y"] = "1y"
    share: Literal["all", "A", "C"] = "all"
    limit: int = Field(default=10, ge=1, le=16)


_AI_PICK_TYPES = ("股票型", "混合型", "指数型", "债券型")
_AI_PICK_PROMPT = """你是公募基金研究助手。
仅根据提供的历史收益榜单和 research 公开资料, 从候选中挑选最多 5 只适合继续核对的研究候选, 并说明依据、数据限制和条件式研究计划。研究计划用于观察和核对, 不是个性化投资建议或下单指令。

严格要求:
- fund_type=all 仅表示股票型、混合型、指数型、债券型四类榜单; 按榜单位次轮流取样, 总数以实际候选为准, fund_type 字段标明其榜单类别。
  不得声称覆盖所有基金类别。不同类别的风险和业绩比较基准可能不同, 不得仅按历史收益给出跨类别的全局优劣结论;
  应先说明类别差异, 仅在口径相同或可比时比较收益, 缺少风险或基准信息时明确说明。
- 只能引用候选清单内的数值, 包括 research 中实际可用的费率、回撤、持仓和日期。
  null/不可用不等于 0; 不得编造缺失费用、规模、经理、日期或未来收益。
- nav_date 为每只基金榜单净值日期, 不代表统一榜单统计截止日。retrieved_at_ms 仅为抓取时间。
- 管理/托管/销售服务费为年费率; 申购赎回费用按条件变化, 平台折扣不代表所有渠道。
- 回撤是指定时间窗内来源累计收益曲线的观测回撤, 可能因稀疏采样低估每日回撤;
  不得把累计净值当复权净值, 不得把单位净值回撤写成总收益回撤。
- 持仓是报告期披露股票, 非实时/完整组合。相关公告发布日期不是已确认的持仓表发布日期。
  比较基金时列出日期、窗口及 missing_fields, 不得因某只资料较全就假定其风险更低。
- A/C 份额不得仅凭名称假定费率; 同一基金的不同份额应指出需核对实际费用。
- 当前没有用户预算和风险承受数据, 不得指定投资金额、仓位比例、目标收益、目标净值或固定止损价。
- 场外基金按确认规则以未知的未来成交净值确认; 盘中估值不是确定成交价, 不得把估值当作买入价。
- 不预测未来收益、不保证收益, 不把历史涨幅写成未来表现, 不得声称已经下单或自动交易。
- 输出 Markdown: 先写筛选范围与数据限制, 再列出最多 5 只研究候选。
- 每只候选含代码、名称、引用的数值和待核对风险, 并增加“研究买入计划”小节, 仅描述研究观察和条件, 不生成下单指令:
  1. 是否先观察及依据; 净值日期、费率、回撤口径、持仓报告期等关键资料缺失或过旧时, 默认先观察并说明待核实项。
  2. 需要确认的数据, 只能列出与该候选相关且会改变判断的缺口。
  3. 分阶段评估条件: 写明进入下一阶段前需满足的可观察条件, 以及何种事实会暂停后续阶段; 不编造价格线、金额或仓位比例。
  4. 费用与持有期: 引用实际申购/赎回费规则及其适用条件; 缺少规则时明确要求核对, 不推定优惠或免赎回费。
  5. 停止后续投入和重新评估/退出条件: 只基于可观察的逻辑失效、风险数据变化或官方资料变化; 无依据时说明无法设定具体阈值。
  最后列出未入选的主要原因和数据缺口。
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
        import asyncio

        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import StreamingResponse

        from app.services.ai_provider import stream_ai_text
        from app.services.ndjson_heartbeat import with_heartbeat

        rank_types = _AI_PICK_TYPES if req.fund_type == "all" else (req.fund_type,)
        rank_results = await asyncio.gather(
            *(
                run_in_threadpool(svc.akshare_rank, fund_type, sort_by=req.horizon, limit=200)
                for fund_type in rank_types
            ),
            return_exceptions=True,
        )
        rankings: dict[str, list[dict]] = {}
        unavailable_types: list[str] = []
        for fund_type, rows in zip(rank_types, rank_results, strict=True):
            if isinstance(rows, asyncio.CancelledError):
                raise rows
            if isinstance(rows, Exception):
                logger.warning("Fund ranking unavailable for AI pick (%s): %s", fund_type, rows)
                unavailable_types.append(fund_type)
            elif not rows:
                logger.warning("Fund ranking returned no rows for AI pick (%s)", fund_type)
                unavailable_types.append(fund_type)
            else:
                rankings[fund_type] = rows
        if not rankings and unavailable_types:
            raise HTTPException(status_code=502, detail="基金历史收益榜单暂不可用")

        field = f"growth_{req.horizon}"
        candidates = []
        seen_codes = set()
        eligible = {}
        for fund_type in rank_types:
            eligible[fund_type] = []
            for row in rankings.get(fund_type, []):
                code = str(row.get("code") or "").strip()
                if (
                    not code
                    or code in seen_codes
                    or row.get(field) is None
                    or (req.share != "all" and row.get("share_class") != req.share)
                ):
                    continue
                eligible[fund_type].append({**row, "code": code, "fund_type": fund_type})
        # Round-robin preserves category coverage without pretending that bond
        # and equity historical returns form a comparable global top ranking.
        while len(candidates) < req.limit and any(eligible.values()):
            for fund_type in rank_types:
                rows = eligible[fund_type]
                while rows and rows[0]["code"] in seen_codes:
                    rows.pop(0)
                if rows:
                    row = rows.pop(0)
                    seen_codes.add(row["code"])
                    candidates.append(row)
                if len(candidates) >= req.limit:
                    break
        if not candidates:
            raise HTTPException(status_code=422, detail="当前筛选条件下没有可核对收益数据的基金")

        async def _gen():
            import asyncio
            import json

            yield json.dumps(
                {
                    "type": "meta",
                    "candidates": candidates,
                    "fund_type": req.fund_type,
                    "unavailable_types": unavailable_types,
                    "horizon": req.horizon,
                    "share": req.share,
                    "source": "东方财富公开基金排名" + (" (经 AKShare)" if candidates[0].get("source", "akshare") == "akshare" else " (直接回退)"),
                    "retrieved_at_ms": round(time.time() * 1000),
                    "data_as_of": None,
                },
                ensure_ascii=False,
            ) + "\n"
            semaphore = asyncio.Semaphore(4)

            async def enrich(row):
                async with semaphore:
                    try:
                        row["research"] = await run_in_threadpool(research, f"{row['code']}.OF", req.horizon)
                    except Exception:
                        logger.exception("Fund research unavailable for %s", row["code"])
                        row["research"] = None
                    return row

            tasks = [asyncio.create_task(enrich(row)) for row in candidates]
            try:
                for completed, task in enumerate(asyncio.as_completed(tasks), 1):
                    row = await task
                    yield json.dumps({"type": "research", "code": row["code"], "research": row["research"],
                                      "completed": completed, "total": len(candidates)}, ensure_ascii=False) + "\n"
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
            try:
                async for delta in stream_ai_text(
                    [
                        {"role": "system", "content": _AI_PICK_PROMPT},
                        {"role": "user", "content": json.dumps({
                            "fund_type": req.fund_type, "horizon": req.horizon,
                            "share": req.share, "candidates": [
                                {**row, "research": svc.research_model_context(row.get("research"))}
                                for row in candidates
                            ],
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

    @router.get("/portfolio/review")
    def portfolio_review_status(request: Request):
        return holdings_review.status(request.app.state.repo.store.data_dir, "fund", svc.load_portfolio())

    @router.post("/portfolio/review")
    async def portfolio_review_start(req: holdings_review.ReviewIn, request: Request):
        from concurrent.futures import ThreadPoolExecutor

        def collect_one(holding):
            symbol = str(holding.get("thscode") or "").strip().upper()
            item = {"symbol": symbol, "name": holding.get("name") or symbol,
                    "asset_type": "fund" if symbol.endswith(".OF") else "etf",
                    "data_date": None, "registered_holding": holding,
                    "registration_values_as_of": None, "warning": "登记金额和收益未经实时核验，不代表今日盈亏。"}
            try:
                source = nav(symbol, range="year", nav_type="unit")
                rows = sorted(source["nav"], key=lambda row: str(row.get("nav_date") or ""))
                from app.market_time import cn_today
                import math

                rows = [row for row in rows if row.get("nav_date")
                        and str(row["nav_date"]) <= cn_today().isoformat()
                        and isinstance(row.get("unit_nav"), (int, float))
                        and not isinstance(row["unit_nav"], bool)
                        and math.isfinite(row["unit_nav"]) and row["unit_nav"] > 0]
                item.update(nav=rows[-20:], nav_source=source.get("source"),
                            data_date=rows[-1]["nav_date"] if rows else None)
            except HTTPException:
                item["warning"] += " 净值暂不可用，无法形成当前操作判断。"
            try:
                item["profile"] = profile(symbol)["profile"]
            except HTTPException:
                item["profile"] = None
            if symbol.endswith(".OF"):
                try:
                    item["research"] = svc.research_model_context(research(symbol, "1y"))
                except HTTPException:
                    item["research"] = None
            return item

        def collect(rows):
            with ThreadPoolExecutor(max_workers=4) as pool:
                return list(pool.map(collect_one, rows))

        return await holdings_review.start(
            request.app.state.repo.store.data_dir, "fund", svc.load_portfolio(), collect, force=req.force,
        )

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
        if not mapped and thscode.endswith(".OF"):
            try:
                mapped = svc.eastmoney_nav(thscode, range)
                source = "eastmoney"
            except Exception as e:
                logger.warning("Eastmoney public fund NAV failed for %s: %s", thscode, e)
        if not mapped:
            raise HTTPException(
                status_code=503,
                detail=(
                    "基金净值不可用: 本机基金数据服务与东方财富均未返回净值, 且扶摇未配置 API Key"
                    if client is None else "基金净值不可用: 扶摇与本机基金数据服务、东方财富均未返回可用净值"
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

    @router.get("/research/{thscode}")
    def research(thscode: str, horizon: str = "1y") -> dict:
        """免费公开费率/观测回撤/股票持仓, 含明确口径与缺失字段。"""
        from app.custom.fund.public_data import HORIZONS, fund_code

        try:
            thscode = f"{fund_code(thscode)}.OF"
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if horizon not in HORIZONS:
            raise HTTPException(status_code=400, detail="不支持的研究区间")
        key = f"research:{thscode}:{horizon}"
        # Coalesce simultaneous detail/estimate/screener misses without an
        # ever-growing map of locks. A stripe may serialize unrelated funds.
        with _research_locks[hash(key) % len(_research_locks)]:
            hit = _research_cache.get(key)
            if hit is not None:
                return hit
            out = svc.fund_research(thscode, horizon)
            available = out["fees"]["status"] != "unavailable" or out["risk"]["status"] == "ok" or out["holdings"]["status"] == "ok"
            _research_cache.set(key, out, ttl_s=21600 if available and not out.get("warnings") else 60)
            return out

    @router.get("/holdings/{thscode}")
    def holdings(thscode: str) -> dict:
        """基金重仓持仓 (季报披露, 非实时)。持仓缓存 24h。"""
        thscode = thscode.strip().upper()
        key = f"holdings:{thscode}"
        hit = _cache.get(key)
        if hit is not None:
            return hit
        client = _optional_client()
        out = None
        if client is not None:
            try:
                mapped = svc.map_holdings(client.holdings(thscode))
                if mapped["items"]:
                    out = {"thscode": thscode, "source": "fuyao", **mapped}
            except FundError:
                logger.warning("Fuyao fund holdings unavailable for %s", thscode)
        if out is None:
            public = research(thscode)["holdings"]
            if public["status"] != "ok":
                raise HTTPException(status_code=503, detail="基金披露股票持仓暂不可用; 非实时持仓")
            out = {"thscode": thscode, "source": "eastmoney", "stock_ratio_pct": None,
                   "total_stock_ratio_pct": None, "concentration_ratio": None, "skipped": [], **public}
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
        # holdings 和 nav 并行; holdings 复用 /holdings 接口的 1 天缓存
        import concurrent.futures

        hkey = f"holdings:{thscode}"
        hhit = _cache.get(hkey)

        def _get_holdings():
            if hhit is not None:
                return hhit
            return holdings(thscode)

        def _get_nav():
            try:
                nav_list = nav(thscode, range="week", nav_type="unit")["nav"]
                if nav_list:
                    return nav_list[-1]["unit_nav"], nav_list[-1]["nav_date"]
            except HTTPException:
                pass
            return None, None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            fut_hold = pool.submit(_get_holdings)
            fut_nav = pool.submit(_get_nav)
            try:
                h_out = fut_hold.result()
            except FundError as e:
                raise HTTPException(status_code=502, detail=str(e)) from e
            estimate_items = [row for row in h_out["items"] if svc._to_qq_symbol(row["thscode"])][:10]
            holdings_data = {"thscode": thscode, "items": estimate_items,
                             "coverage_weight_pct": sum(row["hold_ratio"] for row in estimate_items),
                             "report_date": h_out.get("report_date"), "source": h_out.get("source")}
            prev_nav, nav_date = fut_nav.result()
        # estimate_nav 和 estimate_curve 并行 (各约 2-4s, 串行要 6-8s)
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            fut_est = pool.submit(svc.estimate_nav, holdings_data, prev_nav)
            fut_curve = pool.submit(svc.estimate_curve, holdings_data, prev_nav)
            est = fut_est.result()
            curve = fut_curve.result()
        out = {
            "thscode": thscode,
            "prev_nav": prev_nav,
            "nav_date": nav_date,
            "estimate": est,
            "curve": curve,
            "holdings": holdings_data,
        }
        _cache.set(key, out, ttl_s=60)
        return out

    @router.post("/analyze")
    async def analyze(req: AnalyzeIn):
        """AI 基金分析 (流式 NDJSON，与 /api/stock-analysis/analyze 协议一致)。"""
        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import StreamingResponse

        from app.custom.fund import analyzer as fund_analyzer
        from app.services.ndjson_heartbeat import with_heartbeat

        thscode = req.thscode.strip().upper()

        def collect():
            # Blocking public HTTP work stays off the event loop and within the
            # heartbeat stream, so other settings/market requests stay responsive.
            try:
                nav_rows = nav(thscode, range="year", nav_type="unit,adj")["nav"]
            except HTTPException:
                nav_rows = []
            try:
                profile_data = profile(thscode)["profile"]
            except HTTPException:
                profile_data = None
            name = _watchlist_names().get(thscode) or svc._akshare_name(thscode) or thscode
            try:
                holdings_data = holdings(thscode)
            except HTTPException:
                holdings_data = None
            try:
                research_data = research(thscode)
            except HTTPException:
                research_data = None
            estimate_data = None
            try:
                if holdings_data:
                    prev_nav = nav_rows[-1]["unit_nav"] if nav_rows else None
                    estimate_data = {"estimate": svc.estimate_nav(holdings_data, prev_nav)}
            except Exception:
                logger.warning("Fund estimate unavailable during analysis for %s", thscode)
            return nav_rows, profile_data, name, holdings_data, research_data, estimate_data

        async def _gen():
            nav_rows, profile_data, name, holdings_data, research_data, estimate_data = await run_in_threadpool(collect)
            async for chunk in fund_analyzer.analyze_fund_stream(
                nav_rows, profile_data, thscode, name, req.focus or "",
                holdings=holdings_data, estimate=estimate_data, research=research_data,
            ):
                yield chunk + "\n"

        return StreamingResponse(_ndjson_lines(with_heartbeat(_gen())), media_type="application/x-ndjson")

    return router
