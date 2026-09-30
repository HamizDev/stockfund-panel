"""AI 基金分析服务 — 净值走势 / 基金资料 / 风险收益分析。

职责:
    组合一只基金的净值序列(单位净值走势)+ 基金基本资料 →
    拼装客观基金分析系统提示词 → 流式调用 LLM → 逐 chunk 吐给前端。

与 stock_analyzer.py 的区别:
    - 数据源: 基金净值 (无 OHLC, 只有单位净值/日增长率)
    - 无 K 线技术指标 (MACD/KDJ/布林等不适用)、无关键价位
    - 核心维度: 净值走势、回撤与波动、基金经理与公司、规模与费用

不知道: HTTP、前端、配置持久化。
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """你是一位拥有 15 年公募基金研究经验的基金分析师,擅长从净值走势、回撤特征、基金经理与基金公司维度客观解读一只基金的状态。你的任务是:基于提供的基金净值数据与基本资料,产出一份客观、中立、不包含任何买卖或操作建议的基金分析报告。

## 核心红线(务必遵守)

- 绝对不输出"买入/卖出/加仓/减仓/观望/定投/赎回/建议持有"等任何交易指令或倾向性措辞
- 你的角色是客观陈述该基金当前的净值状态、风险收益特征与潜在风险,让读者自行判断
- 绝对不要编造基金经理变更、规模、分红等未提供的数据

## 输出规范

用 Markdown 格式输出,严格遵循以下结构。不要输出任何 JSON 或代码块,直接输出 Markdown 正文。

### 1. 一句话定调(1-2 句)

用一句话概括该基金当前的净值状态(如"近一年净值震荡上行,最大回撤控制较好"/"近期净值持续回调,波动有所加大")。不评价好坏、不下操作结论。

### 2. 净值走势分析(核心维度)

基于提供的净值序列,客观陈述:

- 收益表现:近 1 月/3 月/6 月/1 年的区间涨跌幅 (用数据说话)
- 波动与回撤:最大回撤幅度、净值波动率的客观水平
- 阶段特征:近期是单边趋势还是震荡,是否有加速/放缓迹象

每条结论必须引用具体数值,客观陈述,不下好坏定性。

### 3. 持仓穿透分析 (如有持仓数据)

基于提供的十大重仓股, 客观分析:

- 集中度: 前十大持仓合计占比, 第一大重仓占比
- 行业分布: 重仓股的行业集中情况 (如有行业信息)
- 个股特征: 重仓股的市值风格 (大盘/中小盘), 是否有单一行业或个股过度集中

只基于提供的持仓数据, 不编造行业分类。

### 4. 当日估值分析 (如有估值数据)

基于提供的盘中估值, 客观陈述:

- 估算涨跌幅与估算净值
- 估值走势: 盘中估值是单边还是震荡
- 与昨日净值的偏离

### 5. 关键净值位

基于统计数据, 列出:

- 历史最高/最低净值
- 60 日/120 日均线位置 (如有)
- 当前净值的历史分位 (0-100, 越高越接近历史高点)

### 6. 基金经理与公司 (资料维度)

基于提供的基本资料,客观列出:

- 基金经理姓名、任职时间 (如有)
- 基金公司 (如有)
- 基金类型、成立日期、业绩基准 (如有)

只陈述资料中的客观事实,不评价经理能力强弱。

### 4. 风险提示

2-3 条,只做客观描述:

- 该基金历史最大回撤水平与当前回撤位置
- 波动率特征
- 费率 (管理费/托管费/申购赎回费) 的客观水平 (如有数据)

## 分析准则(务必遵守)

- 所有数字必须来自提供的数据,不得编造
- 不预测未来走势,不下"值得长期持有"等结论
- 保持客观中立,像一份基金研究简报
"""


def _build_user_prompt(nav_tail, profile, thscode, name, focus="", holdings=None, estimate=None, research=None):
    """构建用户消息: 基金代码 + 名称 + 净值 JSON + 资料 + 持仓 + 估值 + 关注点。"""
    parts = [
        "基金代码: " + thscode,
        "基金名称: " + name,
        "",
        "以下是该基金近期单位净值数据(JSON,含净值日期、单位净值、日增长率,升序):",
        "```json",
        json.dumps(nav_tail, ensure_ascii=False),
        "```",
    ]
    if profile:
        parts.extend([
            "",
            "以下是该基金基本资料(JSON):",
            "```json",
            json.dumps(profile, ensure_ascii=False),
            "```",
        ])
    else:
        parts.extend([
            "",
            "(该基金暂无基本资料,请仅基于净值数据做分析,不要编造基金经理/规模等信息。)",
        ])
    if holdings and holdings.get("items"):
        parts.extend([
            "",
            "以下是该基金已披露股票持仓(JSON,含代码、名称、持仓占比,不代表实时完整组合):",
            "```json",
            json.dumps(holdings["items"], ensure_ascii=False),
            "```",
            f"可用披露股票合计占比: {holdings.get('coverage_weight_pct', 'N/A')}%",
            f"本消息所列 {len(holdings['items'])} 只, 可能截取了权重前10只; 全表合计不等于所列合计。",
            f"持仓报告期: {holdings.get('report_date') or '未知'}; 非实时、非完整组合。",
        ])
    if estimate and estimate.get("estimate"):
        est = estimate["estimate"]
        parts.extend([
            "",
            "以下是该基金当日盘中估值:",
            f"估算涨跌幅(小数制): {est.get('change_pct', 'N/A')}, 估算净值: {est.get('est_nav', 'N/A')}",
        ])
    if research:
        parts.extend(["", "公开研究资料 (费率、观测回撤、持仓报告/公告日期及缺失字段):",
                      json.dumps(research, ensure_ascii=False),
                      "null 不等于 0; 费率需按条件/渠道核对; 回撤的窗口与采样局限必须说明。",
                      "单位净值未经复权, 分红可能影响其回撤; 累计净值不等于复权净值。"])
    if focus:
        parts.extend(["", "用户特别关注: " + focus])
    return "\n".join(parts)


def _calc_stats(nav):
    """计算净值序列的简单统计: 区间涨跌幅、最大回撤、波动率、历史分位。"""
    import math

    from app.custom.fund.public_data import number

    valid = [(row, number(row.get("unit_nav"))) for row in nav]
    valid = [(row, value) for row, value in valid if value is not None and value > 0]
    vals = [value for _, value in valid]
    if len(vals) < 2:
        return {}
    stats = {}
    for label, n in [("近1月", 22), ("近3月", 66), ("近6月", 132), ("近1年", 250)]:
        if len(vals) >= n:
            base = vals[-n]
            if base > 0:
                stats[label] = round((vals[-1] - base) / base * 100, 2)
    peak = vals[0]
    max_dd = 0.0
    for v in vals:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    stats["最大回撤%"] = round(max_dd * 100, 2)
    stats["最新净值"] = vals[-1]
    stats["样本天数"] = len(vals)
    stats["统计口径"] = "单位净值样本, 未经复权; 分红可能影响回撤, 不代表总收益回撤"
    stats["统计起始日"] = valid[0][0].get("nav_date") or valid[0][0].get("date")
    stats["统计截止日"] = valid[-1][0].get("nav_date") or valid[-1][0].get("date")
    # 历史分位: 当前净值在样本中的位置 (0-100, 越高越接近历史高点)
    sorted_vals = sorted(vals)
    pos = sorted_vals.index(vals[-1]) if vals[-1] in sorted_vals else 0
    stats["历史分位%"] = round(pos / len(sorted_vals) * 100, 1)
    # 年化波动率 (用日收益率标准差)
    rets = [(vals[i] - vals[i-1]) / vals[i-1] for i in range(1, len(vals)) if vals[i-1] > 0]
    if len(rets) > 1:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        stats["年化波动率%"] = round(math.sqrt(var * 250) * 100, 2)
    # 关键净值位
    stats["历史最高"] = round(max(vals), 4)
    stats["历史最低"] = round(min(vals), 4)
    if len(vals) >= 60:
        stats["60日均线"] = round(sum(vals[-60:]) / 60, 4)
    if len(vals) >= 120:
        stats["120日均线"] = round(sum(vals[-120:]) / 120, 4)
    return stats


async def analyze_fund_stream(nav_rows, profile, thscode, name, focus="", holdings=None, estimate=None, research=None):
    """流式基金分析: yield 出每个 NDJSON 事件。

    协议(与 stock_analyzer 一致):
        {"type":"meta","symbol","summary","stats"}
        {"type":"delta","content":"..."}
        {"type":"error","message":"..."}
        {"type":"done"}
    """
    nav_tail = nav_rows[-250:] if len(nav_rows) > 250 else nav_rows
    if not nav_tail:
        yield json.dumps({
            "type": "error",
            "message": "基金 " + thscode + " 暂无净值数据",
        }, ensure_ascii=False)
        return

    stats = _calc_stats(nav_tail)
    summary = (
        "最新净值 " + str(stats.get("最新净值"))
        + ", 近1年 " + str(stats.get("近1年", "N/A")) + "%"
        + ", 单位净值样本回撤 " + str(stats.get("最大回撤%", "N/A")) + "% (未复权)"
    )

    yield json.dumps({
        "type": "meta",
        "symbol": thscode,
        "summary": summary,
        "stats": stats,
    }, ensure_ascii=False)

    try:
        from app.services.ai_provider import stream_ai_text

        clean_nav = [
            {
                "date": r.get("date") or r.get("nav_date"),
                "unit_nav": r.get("unit_nav"),
                "growth": r.get("growth"),
            }
            for r in nav_tail
        ]
        from app.custom.fund.service import research_model_context

        prompt_holdings = {**holdings, "items": holdings["items"][:10]} if holdings else None
        user_prompt = _build_user_prompt(clean_nav, profile, thscode, name, focus, prompt_holdings, estimate, research_model_context(research))
        async for delta in stream_ai_text(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.5,
            max_tokens=None,
            prefer_final_answer=True,
        ):
            yield json.dumps({"type": "delta", "content": delta}, ensure_ascii=False)

    except Exception as e:
        logger.exception("AI fund analysis failed for %s: %s", thscode, e)
        yield json.dumps({"type": "error", "message": "AI 分析失败: " + str(e)}, ensure_ascii=False)
        return

    yield json.dumps({"type": "done"}, ensure_ascii=False)
