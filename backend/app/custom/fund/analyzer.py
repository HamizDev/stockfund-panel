# ruff: noqa: RUF001 -- Chinese research prompts use native punctuation.
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

_SYSTEM_PROMPT = """你是一位客观的公募基金研究助手。基于提供的净值序列、基金资料、公开持仓和费用研究数据，写一份可核对的基金研究简报，并给出明确且有条件的主结论。结论用于研究和核对，不是个性化投资建议或下单指令。

## 研究边界

- 所有数字、日期、基金经理、规模、费率、持仓和结论只能来自输入；缺失字段明确写“未提供/待核实”，null 不等于 0。
- 写清采用的数据日期：净值用最新实际 nav_date，披露持仓用 report_date，回撤用 research.risk 的窗口、口径和起止日期；retrieved_at_ms 只是抓取时间，不能当作数据日期。日期缺失时明确标注。
- 基金公开披露的股票持仓不等于用户账户持仓，也不代表实时或完整组合。只有用户明确说明该基金是本人已持有，才按已持有基金给出持仓动作；不能从基金公开持仓、关注列表或正在分析推断用户持有。
- 已持有时，每只基金必须且只能给出一个主动作，标签严格选其一：继续持有、暂停追加、条件追加、条件部分赎回、资料不足待核实。不能只写“继续观察”而不落到其中一个标签。
- 未明确说明本人持有时，按候选研究给一个主结论，标签严格选其一：候选观察、满足条件可申购、暂不选、资料不足待核实。不能仅因没有账户持仓信息就判为资料不足；应先根据基金数据判断。关键判断数据缺失到无法评估时，才用“资料不足待核实”。
- “继续持有”需有仍支持原逻辑的事实依据；“暂停追加”需指出尚未满足的条件或新增风险；“条件追加”需给出可观察的触发条件；“条件部分赎回”需给出有数据依据的触发条件。不可把这些标签写成已经执行的交易。
- “满足条件可申购”只表示明确列出的研究条件已满足，不代表保证收益或确定成交；缺少可核验的开放申购状态、份额类别或适用费用时，不得声称条件已满足。可以写明适用人群和申购条件，不推定用户个人适合。
- 不预测未来收益，不保证收益，不把历史涨幅说成未来表现，也不编造目标收益、目标净值或固定止损价。
- 当前没有用户预算、风险承受能力和账户份额信息，不得指定投资金额、持仓金额、份额数量、仓位比例、赎回比例或目标收益。基金 A/C 份额类别未提供时不得替用户选定份额类别。
- 场外基金申购或赎回均按规则以未知的未来成交净值确认；盘中估值不等于实际成交净值，不得把它写成确定成交价。
- 赎回费随份额类别、渠道和持有期限变化。若持有批次、确认日期、销售渠道或适用费率档位不明，不得编造赎回费率、免赎回费结论或赎回成本；明确列为待核实。
- 条件必须依赖输入中有依据的事实或明确列为待核实的数据，不得为凑计划编造价格线、金额、仓位和日期；不得声称已下单、自动交易或收益确定。

## 输出规范

用 Markdown 正文，不输出 JSON 或代码块，按以下结构组织；某项缺少输入时简短说明资料不足。

### 1. 一句话定调
概括当前净值状态和样本局限，引用实际数据日期，不作收益保证。

### 2. 主结论与执行条件
- **持有状态与主动作/结论**：说明输入是否明确确认用户本人持有。已持有时只选一个持仓动作标签；持有状态未说明时按候选研究只选一个候选结论标签。
- **数据日期与事实依据**：列出支撑主结论的实际净值日期、回撤窗口/日期、费用资料日期、持仓报告期等；每个理由引用输入中的数值或事实。抓取时间不能替代数据日期。
- **执行触发条件**：写出可观察、可核验且基于输入的触发条件，以及触发前不得采取的动作。若条件尚未满足，指出具体还缺什么；不虚构数值阈值。
- **反向/失效条件**：写明什么可观察事实会推翻当前结论、暂停追加或触发重新评估；若输入不足以设定阈值，说明原因并列出所需数据。
- **具体缺口**：列出会改变结论的缺失信息，例如最新净值日期、官方申赎状态、业绩基准、费率及适用条件、披露持仓报告期；已持有时还需指出账户份额、持有批次/确认日期、份额类别和渠道等缺口。

### 3. 净值与风险表现
列出输入可支持的区间涨跌、波动及样本窗口。最大回撤仅引用 research.risk 中的原值，并说明 horizon、basis、起止日期、观测点数和采样说明；若 risk 不可用或缺失，明确写未提供，不得从未复权单位净值重新计算或替代最大回撤。

### 4. 基金资料与公开持仓
如有数据，说明披露报告期、覆盖比例、行业/个股集中情况、基金经理、公司、基准和资料缺口。公开持仓是报告期披露，不是用户账户持仓，也不是实时或完整组合。

### 5. 费用与持有期
按实际提供的管理费、托管费、销售服务费及申购/赎回规则说明。申赎费用有适用条件和持有期限差异；缺少份额类别、渠道、持有批次/确认日期或费率档位时，明确要求核对，不推定优惠、低费或免赎回费。

### 6. 风险提示
简要列出回撤、波动、样本口径、费用、持仓时效和场外未知成交净值等实际风险。

## 最终检查

逐项确认主结论只有一个且属于规定标签，写明数据日期、事实依据、执行触发条件、反向/失效条件和具体缺口；没有编造输入外事实，没有指定金额/份额/比例/目标收益，没有把盘中估值当成交价，没有承诺收益，也没有声称执行了交易。
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
