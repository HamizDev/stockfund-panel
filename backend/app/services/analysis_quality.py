# ruff: noqa: RUF001 -- localized Chinese research messages.
"""Deterministic input checks for research; never a model accuracy score."""
from __future__ import annotations

from datetime import date

import polars as pl


def inspect_frame(frame: pl.DataFrame, symbol: str, cutoff: date, *, strict: bool = False) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    required = {'symbol', 'date', 'open', 'high', 'low', 'close', 'volume'}
    if strict and not required.issubset(frame.columns):
        errors.append('行情缺少必要字段，不能生成本次分析。')
    if frame.is_empty():
        errors.append('暂无可分析的日K。')
    if 'symbol' in frame.columns and frame.filter(pl.col('symbol').is_null() | (pl.col('symbol') != symbol)).height:
        errors.append('行情中存在其他标的或缺失代码。')
    as_of = None
    if 'date' in frame.columns and not frame.is_empty():
        dates = frame['date'].cast(pl.String).str.slice(0, 10).str.to_date(format='%Y-%m-%d', strict=False)
        if dates.null_count() or dates.n_unique() != frame.height or not dates.is_sorted():
            errors.append('行情日期无效、重复或顺序错误。')
        elif dates[-1] > cutoff:
            errors.append('行情包含分析日期之后的数据。')
        else:
            as_of = dates[-1].isoformat()
    else:
        warnings.append('未记录行情交易日，不能确认时效。')
    for name in ('open', 'high', 'low', 'close', 'volume'):
        if name not in frame.columns:
            continue
        if frame.schema[name] == pl.Boolean:
            errors.append('价格或成交量不能为布尔值。')
            break
        values = frame[name].cast(pl.Float64, strict=False)
        invalid = values.is_null() | ~values.is_finite() | (values < 0 if name == 'volume' else values <= 0)
        if invalid.any():
            errors.append('行情含有无效价格或成交量。')
            break
    if {'open', 'high', 'low', 'close'}.issubset(frame.columns):
        prices = frame.select(pl.col(name).cast(pl.Float64, strict=False) for name in ('open', 'high', 'low', 'close'))
        if prices.filter((pl.col('high') < pl.max_horizontal('open', 'close'))
                         | (pl.col('low') > pl.min_horizontal('open', 'close'))).height:
            errors.append('行情高低价与开收盘价矛盾。')
    if frame.height < 60:
        warnings.append('历史样本不足60根，长周期指标或趋势证据可能不完整。')
    return {'as_of': as_of, 'bars': frame.height, 'errors': errors, 'warnings': warnings}


def published_rows(frame: pl.DataFrame, cutoff: date) -> pl.DataFrame:
    """A period end never substitutes for the date information became public."""
    if frame.is_empty() or 'period_end' not in frame.columns:
        return frame.clear()
    published = next((name for name in ('announce_date', 'announcement_date', 'published_at')
                      if name in frame.columns), None)
    if published is None:
        return frame.clear()
    return frame.filter(
        pl.col('period_end').cast(pl.String).str.slice(0, 10).str.to_date(format='%Y-%m-%d', strict=False) <= cutoff,
        pl.col(published).cast(pl.String).str.slice(0, 10).str.to_date(format='%Y-%m-%d', strict=False) <= cutoff,
    )


def evidence_instruction(quality: dict, snapshot: dict, *, financials_available: bool, asset_type: str) -> str:
    lines = [f"输入核验：{quality['bars']}根日K；行情交易日截至 {quality['as_of'] or '未记录'}。"]
    lines.extend(quality['warnings'])
    if snapshot.get('warning'):
        lines.append(str(snapshot['warning']))
    if snapshot.get('is_provisional'):
        lines.append('最后一根日K尚未定版，不得写成收盘确认。')
    if asset_type == 'stock' and not financials_available:
        lines.append('没有可确认在分析日期前已公告的财务数据，不能推断盈利质量。')
    lines.append('行情交易日、分钟实际时间、财务公告日与本次抓取时间是不同字段，不得互相替代。')
    lines.append('结论分别列出支持证据、相反证据与失效条件；缺失的相反证据写未提供，不编造。'
                 '引用输入日期及数值；核验只检查输入完整性，不代表预测准确或策略已经通过回测。')
    return '\n'.join(lines)
