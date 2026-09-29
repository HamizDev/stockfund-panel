"""AI 助手 system prompt 的「数据口径」必须与工具实际返回的单位一致。

CONTRIBUTING §3.1: enriched 的 turnover_rate 是百分数 (5 = 5%), 指数实时展示缓存
(get_index_quotes) 的 change_pct / amplitude 也是百分数 (1.23 = 1.23%); 只有
change_pct / amplitude 在 enriched 里是小数。提示词却写「涨跌幅/换手率/振幅均为小数
(0.0366 = 3.66%), 表述时转为百分比」—— 模型照做会把换手率 5.2 说成 520%,
把上证指数 +1.23 说成「上涨 123%」(与 #285 同一类单位错位)。
"""
from __future__ import annotations

import polars as pl

from app.custom.assistant import tools as assistant_tools
from app.custom.assistant.prompt import build_system_prompt
from app.services.quote_service import QuoteService


def _unit_lines() -> list[str]:
    """提示词「数据口径」小节的条目行 (续行已由模板的反斜杠拼接)。"""
    lines = build_system_prompt(None).splitlines()
    start = lines.index("数据口径:") + 1
    out: list[str] = []
    for line in lines[start:]:
        if not line.startswith("- "):
            break
        out.append(line)
    return out


class _IndexQuoteService:
    def __init__(self, df: pl.DataFrame) -> None:
        self._df = df

    def get_index_quotes(self, symbols=None) -> pl.DataFrame:
        return self._df


async def test_index_quote_tool_returns_percent_units() -> None:
    """实际口径: get_indices 透传实时指数缓存, 涨跌幅已是百分数。"""
    cache = QuoteService._build_index_quotes([{
        "symbol": "000001.SH", "name": "上证指数", "last_price": 3850.0, "prev_close": 3803.23,
        "change_pct": 0.0123, "amplitude": 0.0156,
    }])
    ctx = assistant_tools.ToolContext.build(quote_service=_IndexQuoteService(cache))
    payload = await assistant_tools.execute_assistant_tool("get_indices", {}, ctx)

    row = payload["result"]["rows"][0]
    assert row["change_pct"] == 1.23
    assert row["amplitude"] == 1.56


def test_enriched_turnover_is_percent_units() -> None:
    """实际口径: 实时入口的小数换手率在进入 enriched 前就乘了 100。"""
    extra = QuoteService._build_quote_extra([{"symbol": "600519.SH", "turnover_rate": 0.052}])
    assert extra["turnover_rate"][0] == 5.2


def test_prompt_does_not_declare_turnover_or_index_pct_as_decimal() -> None:
    decimal_lines = [line for line in _unit_lines() if "0.0366" in line]
    assert decimal_lines, "提示词应保留小数口径示例"
    for line in decimal_lines:
        assert "换手率" not in line
        assert "turnover_rate" not in line
        assert "指数" not in line


def test_prompt_declares_turnover_rate_as_percent() -> None:
    lines = [line for line in _unit_lines() if "turnover_rate" in line]
    assert lines
    assert all("百分数" in line for line in lines)


def test_prompt_declares_index_quotes_as_percent() -> None:
    lines = [line for line in _unit_lines() if "get_indices" in line]
    assert lines
    assert all("百分数" in line and "change_pct" in line for line in lines)
    assert any("get_market_overview" in line for line in lines)
