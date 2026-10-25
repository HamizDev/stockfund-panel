"""个股分析 API — 关键价位 + AI 四维分析 + 报告持久化。

路由前缀: /api/stock-analysis

端点:
  GET  /levels?symbol=         11 类关键价位(图表 markLine 数据源)
  POST /analyze                AI 流式四维分析(NDJSON)
  GET  /reports                历史报告列表
  POST /reports                保存一条报告
  DELETE /reports/{report_id}  删除一条报告
"""
from __future__ import annotations

import logging
import math
from datetime import timedelta

import polars as pl
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.indicators.levels import compute_levels, summarize_levels
from app.market_time import cn_now, cn_today
from app.services import analysis_snapshot, stock_reports
from app.services.ndjson_heartbeat import with_heartbeat
from app.services.stock_analyzer import analyze_stock_stream

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stock-analysis", tags=["stock-analysis"])


def _to_float_list(series: pl.Series, price_precision: int = 2) -> list:
    """polars Series → JSON 安全的 float 列表(null/NaN → None)。"""
    out: list = []
    for v in series.to_list():
        if v is None:
            out.append(None)
            continue
        try:
            f = float(v)
            out.append(round(f, price_precision) if math.isfinite(f) else None)
        except (TypeError, ValueError):
            out.append(None)
    return out


def _build_series(df: pl.DataFrame, *, price_precision: int = 2) -> dict:
    """提取带状指标(布林带 / Keltner通道 / ATR止损)的每日时间序列。

    这些指标的本质是"每日一条线",随 MA/ATR/σ 漂移,画成曲线才能体现通道形态。
    其余固定价位(枢轴/前高前低等)不在此,仍用水平 markLine。

    返回结构(每个 value 都是按日期对齐的数组):
      {
        "boll":      {"upper": [...], "lower": [...]},
        "keltner_s": {"upper": [...], "lower": [...]},   # 短期 MA20±2ATR
        "keltner_m": {"upper": [...], "lower": [...]},   # 中期 MA60±2.5ATR
        "keltner_l": {"upper": [...], "lower": [...]},   # 长期 MA120±3ATR
        "atr":       {"stop_loss": [...], "take_profit": [...]},  # close∓2ATR
      }
    """
    if df.is_empty() or "close" not in df.columns:
        return {}

    out: dict[str, dict] = {}
    def price_values(series):
        return _to_float_list(series, price_precision)
    close = df["close"]
    has_atr = "atr_14" in df.columns

    # 布林带(上/下/中轨;中轨 = MA20,数据层已预计算)
    if "boll_upper" in df.columns and "boll_lower" in df.columns:
        out["boll"] = {
            "upper": price_values(df["boll_upper"]),
            "lower": price_values(df["boll_lower"]),
            "mid": price_values(df["ma20"]) if "ma20" in df.columns else None,
        }

    # Keltner 通道三档(需要 ATR)
    if has_atr:
        atr = df["atr_14"]
        # MA120 现场算(不在预计算列中)
        ma120 = df.select(pl.col("close").rolling_mean(120))["close"] if df.height >= 120 else None

        def _channel(ma: pl.Series, n: float) -> dict:
            return {
                "upper": price_values(ma + n * atr),
                "lower": price_values(ma - n * atr),
            }

        if "ma20" in df.columns:
            out["keltner_s"] = _channel(df["ma20"], 2.0)
        if "ma60" in df.columns:
            out["keltner_m"] = _channel(df["ma60"], 2.5)
        if ma120 is not None:
            out["keltner_l"] = _channel(ma120, 3.0)

        # ATR 止损/止盈: close ± 2×ATR(跟随行情漂移的动态止损线)
        out["atr"] = {
            "stop_loss": price_values(close - 2 * atr),
            "take_profit": price_values(close + 2 * atr),
        }

    return out


def _levels_payload(df: pl.DataFrame, symbol: str, *, price_precision: int = 2) -> dict:
    if df.is_empty():
        return {"levels": {key: [] for key in ("sr", "pivot", "extreme", "boll", "keltner_s",
                "keltner_m", "keltner_l", "atr_stop", "gap", "fib", "round")},
                "close": None, "summary": "无数据", "symbol": symbol, "dates": [], "series": {}}
    levels = compute_levels(df, price_precision=price_precision)
    close = float(df["close"][-1])
    return {"levels": levels, "close": close,
            "summary": summarize_levels(levels, close, price_precision=price_precision),
            "symbol": symbol, "dates": [str(d) for d in df["date"].to_list()],
            "series": _build_series(df, price_precision=price_precision)}


@router.get("/snapshot")
def get_snapshot(
    request: Request,
    symbol: str = Query(..., pattern=r"^\d{6}\.(SH|SZ|BJ)$"),
    days: int = Query(250, ge=30, le=500),
):
    frame, payload = analysis_snapshot.load_snapshot(
        request.app.state.repo, getattr(request.app.state, "capabilities", None), symbol, days,
        include_current=True)
    return {**payload, "analysis": _levels_payload(
        frame, symbol, price_precision=3 if payload.get('asset_type') == 'etf' else 2)}


@router.get("/levels")
def get_levels(
    request: Request,
    symbol: str = Query(..., description="标的代码,如 000001.SZ"),
    days: int = Query(120, ge=30, le=500, description="计算样本天数"),
):
    """计算 11 类关键价位(成交密集区压力支撑 / 枢轴点 / 前高前低 /
    布林带 / Keltner短中长 / ATR止损 / 缺口 / 斐波那契 / 整数关口)。

    返回 {levels: {sr, pivot, extreme, boll, keltner_s, keltner_m, keltner_l,
    atr_stop, gap, fib, round}, close, summary, dates, series}。
    前端按 levels 的 key 渲染开关按钮,逐组显隐 markLine / 曲线。
    """
    if not symbol:
        raise HTTPException(400, "symbol 不能为空")

    repo = request.app.state.repo
    end = cn_today()
    start = end - timedelta(days=days * 2)
    # 按资产类型分流: ETF/指数走独立 enriched 存储, 股票保持原路径
    df = repo.get_daily_asset(repo.resolve_asset_type(symbol), symbol, start, end)
    return _levels_payload(df, symbol, price_precision=3 if repo.resolve_asset_type(symbol) == 'etf' else 2)


class AnalyzeRequest(BaseModel):
    """AI 个股分析请求。"""
    symbol: str
    focus: str = ""  # 可选:用户追加的分析关注点
    research_plan: bool = False  # 选股页的条件研究计划; 不执行任何交易


@router.post("/analyze")
async def analyze_stock(request: Request, req: AnalyzeRequest):
    """AI 个股四维分析 — NDJSON 流式返回。

    组合 K 线(技术指标)+ 财务表 + 关键价位 → 客观技术分析提示词 →
    流式调用 LLM → 逐 chunk 以 NDJSON 推给前端(每行一个 JSON)。
    """
    if not req.symbol:
        raise HTTPException(400, "symbol 不能为空")

    repo = request.app.state.repo
    data_dir = repo.store.data_dir
    request_time = cn_now()

    async def stream_gen():
        stream = analyze_stock_stream(
            repo, data_dir, req.symbol, req.focus, research_plan=req.research_plan,
            refresh=True, capset=getattr(request.app.state, "capabilities", None), request_time=request_time)
        async for chunk in with_heartbeat(stream):
            yield chunk + "\n"

    return StreamingResponse(
        stream_gen(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ================================================================
# 报告 CRUD(历史报告持久化)
# ================================================================

class SaveReportRequest(BaseModel):
    """保存一条 AI 个股分析报告。"""
    symbol: str
    name: str = ""
    focus: str = ""
    content: str
    summary: str = ""
    close: float | None = None
    levels: dict | None = None
    as_of: str | None = None
    daily_as_of: str | None = None
    intraday_as_of: str | None = None
    is_provisional: bool = False
    price_basis: str | None = None
    requested_at: str | None = None
    evidence: dict | None = None


@router.get("/reports")
def list_reports(request: Request):
    """获取全部历史报告(按时间降序,后端已裁剪到上限)。"""
    return {"reports": stock_reports.list_reports()}


@router.post("/reports")
def save_report(request: Request, req: SaveReportRequest):
    """保存一条报告。"""
    report = stock_reports.save_report({
        "symbol": req.symbol,
        "name": req.name,
        "focus": req.focus,
        "content": req.content,
        "summary": req.summary,
        "close": req.close,
        "levels": req.levels,
        "as_of": req.as_of,
        "daily_as_of": req.daily_as_of,
        "intraday_as_of": req.intraday_as_of,
        "is_provisional": req.is_provisional,
        "price_basis": req.price_basis,
        "requested_at": req.requested_at,
        "evidence": req.evidence,
    })
    return {"ok": True, "report": report}


@router.delete("/reports/{report_id}")
def delete_report(request: Request, report_id: str):
    """删除一条报告。"""
    ok = stock_reports.delete_report(report_id)
    return {"ok": ok}
