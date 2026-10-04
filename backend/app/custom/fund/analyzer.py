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
import math

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """你是一位客观的公募基金研究助手. 基于提供的净值序列, 基金资料, 公开持仓和费用研究数据, 写一份可核对的基金研究简报, 并给出有条件的研究买入计划. 计划用于继续观察和核对, 不是个性化投资建议或下单指令.

## 研究边界

- 所有数字, 日期, 基金经理, 规模, 费率, 持仓和结论只能来自输入; 缺失字段明确写 "未提供/待核实", null 不等于 0.
- 不预测未来收益, 不保证收益, 不把历史涨幅说成未来表现, 也不编造目标收益, 目标净值或固定止损价.
- 当前没有用户预算和风险承受信息. 不得指定投资金额, 仓位比例或目标收益.
- 场外基金按申购确认规则以未知的未来成交净值确认; 盘中估值不等于实际成交净值, 不得把它写成确定买入价.
- 费用按输入里的渠道, 份额, 适用条件和持有期限说明. 缺少赎回费率或持有期档位时明确要求核对, 不自行补值.
- 研究计划可以说明 "先观察/等待核实", 需要确认的数据, 条件式分阶段评估, 停止后续投入的条件和重新评估/退出的条件; 不得声称已下单, 自动交易或收益确定.
- 条件必须依赖输入中有依据的事实或明确列为待核实的数据, 不得为凑计划编造价格线, 仓位和日期.

## 输出规范

用 Markdown 正文, 不输出 JSON 或代码块, 按以下结构组织; 某项缺少输入时简短说明资料不足.

### 1. 一句话定调
概括当前净值状态和样本局限, 引用有效日期, 不作收益保证.

### 2. 净值与风险表现
列出输入可支持的区间涨跌、波动及样本窗口. 最大回撤仅引用 research.risk 中的原值, 并说明 horizon、basis、起止日期、观测点数和采样说明; 若 risk 不可用或缺失, 明确写未提供, 不得从未复权单位净值重新计算或替代最大回撤.

### 3. 持仓与基金资料
如有数据, 说明披露报告期, 覆盖比例, 行业/个股集中情况, 基金经理, 公司, 基准和资料缺口. 持仓是报告期披露, 不是实时或完整组合.

### 4. 费用与持有期
按实际提供的管理费, 托管费, 销售服务费及申购/赎回规则说明. 申赎费用有适用条件和持有期限差异; 缺数据时列出下单前需查证的规则.

### 5. 研究买入计划 (条件式研究, 不是下单指令)
- **是否先观察**: 结合净值日期, 回撤口径, 费率, 持仓报告期和其他已知风险, 说明先观察或进入下一步研究的依据. 关键资料缺失或过旧时, 默认先观察并列出待核实项.
- **需要确认的数据**: 列出会改变判断的最新净值日期, 官方申赎状态, 适用费率/赎回持有期, 业绩基准, 持仓报告期等实际缺口.
- **分阶段评估条件**: 描述每一阶段开始前必须满足的可观察条件, 以及什么情况暂停后续阶段; 只写条件, 不写金额, 比例仓位或未经提供的价格点.
- **费用与持有期**: 明确申购/赎回费用如何随条件变化; 资料不足时不得推定低费或免赎回费.
- **停止后续投入与重新评估/退出条件**: 基于投资逻辑失效, 风险数据恶化或官方资料变化等可观察事实描述复核条件; 无数据支撑时说明无法设定具体阈值.

### 6. 风险提示
简要列出回撤, 波动, 样本口径, 费用, 持仓时效和场外未知成交净值等实际风险.

## 最终检查

逐项确认没有编造输入外的事实, 没有指定金额/仓位/目标收益, 没有把盘中估值当成交价, 没有承诺收益, 也没有声称执行了交易.
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
                      "null 不等于 0; 费率需按条件/渠道核对; 最大回撤仅使用 research.risk, 并说明其窗口、basis、日期、观测数和采样局限。",
                      "未复权单位净值不得用于补算或替代最大回撤; 累计净值不等于复权净值。"])
    if focus:
        parts.extend(["", "用户特别关注: " + focus])
    return "\n".join(parts)


def _calc_stats(nav):
    """计算单位净值样本统计; 最大回撤由 research.risk 单独提供。"""
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
    stats["最新净值"] = vals[-1]
    stats["样本天数"] = len(vals)
    stats["统计口径"] = "单位净值样本, 未经复权; 最大回撤单独采用 research.risk 口径"
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


def _research_drawdown(research):
    """Normalize research.risk for the analysis metadata; never derive a fallback."""
    research = research if isinstance(research, dict) else {}
    risk = research.get("risk")
    risk = risk if isinstance(risk, dict) else {}
    drawdown = {
        "status": "unavailable",
        "basis": None,
        "max_drawdown_pct": None,
        "start_date": None,
        "end_date": None,
        "observations": 0,
        "source_url": None,
        "note": "区间观测最大回撤资料不可用; 不以未复权单位净值回撤替代。",
        "horizon": research.get("horizon"),
    }
    drawdown.update(risk)
    drawdown["horizon"] = research.get("horizon")

    try:
        value = float(drawdown["max_drawdown_pct"])
    except (TypeError, ValueError):
        value = float("nan")
    if drawdown.get("status") != "ok" or not math.isfinite(value) or value < 0:
        drawdown["status"] = "unavailable"
        drawdown["max_drawdown_pct"] = None
        if not drawdown.get("note"):
            drawdown["note"] = "区间观测最大回撤资料不可用; 不以未复权单位净值回撤替代。"
    else:
        drawdown["max_drawdown_pct"] = value
    return drawdown


def _drawdown_summary(drawdown):
    if drawdown["status"] != "ok":
        note = drawdown.get("note")
        suffix = f"; {note}" if note else ""
        return "区间观测最大回撤暂不可用(未复权单位净值回撤不作替代)" + suffix

    window = drawdown.get("horizon") or "窗口未提供"
    basis = drawdown.get("basis") or "口径未提供"
    start = drawdown.get("start_date") or "起始日未提供"
    end = drawdown.get("end_date") or "截止日未提供"
    observations = drawdown.get("observations")
    count = f"{observations} 个观测点" if observations is not None else "观测点数未提供"
    note = drawdown.get("note")
    suffix = f"; {note}" if note else ""
    return (
        f"区间观测最大回撤 {drawdown['max_drawdown_pct']}%"
        f"(窗口 {window}, 口径 {basis}, 日期 {start} 至 {end}, {count}){suffix}"
    )


async def analyze_fund_stream(nav_rows, profile, thscode, name, focus="", holdings=None, estimate=None, research=None):
    """流式基金分析: yield 出每个 NDJSON 事件。

    协议(与 stock_analyzer 一致):
        {"type":"meta","symbol","summary","stats","drawdown"}
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
    drawdown = _research_drawdown(research)
    stats["最大回撤%"] = drawdown["max_drawdown_pct"]
    summary = (
        "最新净值 " + str(stats.get("最新净值"))
        + ", 近1年 " + str(stats.get("近1年", "N/A")) + "%"
        + ", " + _drawdown_summary(drawdown)
    )
    research_holdings = research.get("holdings") if isinstance(research, dict) else None
    holdings_report_date = (research_holdings or {}).get("report_date") or (holdings or {}).get("report_date")

    yield json.dumps({
        "type": "meta",
        "symbol": thscode,
        "summary": summary,
        "stats": stats,
        "drawdown": drawdown,
        "nav_date": nav_tail[-1].get("nav_date") or nav_tail[-1].get("date"),
        "holdings_report_date": holdings_report_date,
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
