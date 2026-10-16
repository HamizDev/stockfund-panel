"""Bounded local decision evidence; never changes orders or risk settings."""
# ruff: noqa: RUF001 -- Chinese research UI explanations.
from __future__ import annotations

import copy
import hashlib
import json
import math

from . import pine_research

LIMIT = 300


def record(account: dict, at_ms: int, bars: list[dict], trades_before: int, *, error: str | None = None) -> None:
    trades = [row for row in account['ledger']['trades'][trades_before:]
              if row.get('kind', 'trade') != 'funding']
    target = account.get('last_signal')
    bar_time = bars[-1].get('close_time_ms') if bars else None
    failure = error or account.get('last_error')
    status = account.get('status', 'waiting')
    risk_exit = any(str(row.get('request_id', '')).endswith('_risk')
                    or row.get('kind') == 'liquidation' for row in trades)
    if failure or status == 'catching_up':
        status, reason, target = 'blocked', failure or '资金费历史待补齐，暂停策略动作', None
    elif risk_exit:
        status, reason, target = 'risk_exit', '止盈、止损或强平研究规则触发；本轮不重新入场', None
    elif trades:
        status, reason = 'executed', '信号通过数量、余额与报价校验，已记录本地模拟成交'
    elif not account['enabled']:
        status, reason, target = 'paused', '账户暂停，仅维护已有持仓与估值', None
    elif target == 'hold':
        status, reason = 'waiting', '入场事件未触发，保持现有仓位或空仓等待'
    elif target == 'flat':
        status, reason = 'waiting', '目标为空仓；当前无须新增成交'
    else:
        status, reason = 'waiting', '已有同向持仓或本根信号已处理，不重复加仓'
    event = {'at_ms': at_ms, 'bar_time_ms': bar_time, 'target': target,
             'status': status, 'reason': str(reason)[:500], 'metrics': {}, 'conditions': {},
             'equity': account.get('equity'), 'trades_added': len(trades)}
    existing = account.setdefault('decisions', [])
    # Equity moves on every quote. It is evidence, not a reason to append an
    # identical trading decision every 30 seconds.
    identity = ('bar_time_ms', 'target', 'status', 'reason', 'trades_added')
    event['fingerprint'] = hashlib.sha256(json.dumps([event[key] for key in identity]).encode()).hexdigest()
    if existing and existing[-1].get('fingerprint') == event['fingerprint']:
        return
    if not failure and status not in ('paused', 'blocked') and bars and account['strategy_id'] in pine_research.IDS:
        try:
            detail = pine_research.analyse(account['strategy_id'], bars[-200:], account.get('strategy_params'))
            event['metrics'] = {key: value for key, value in detail['metrics'].items()
                                if isinstance(value, (int, float)) and math.isfinite(value)}
            event['conditions'] = copy.deepcopy(detail['conditions'])
        except (ValueError, KeyError, TypeError, IndexError):
            event['reason'] += '；指标解释暂不可计算，未填入估计值'
    existing.append(event)
    account['decisions'] = existing[-LIMIT:]
