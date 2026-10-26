"""Public AI research cannot emit structured position instructions or reuse old reports."""
import asyncio
import json
from datetime import date

import pytest
from pydantic import ValidationError

from app.custom.assistant.prompt import build_system_prompt
from app.custom.fund import analyzer
from app.custom.fund import routes as fund_routes
from app.services import holdings_review as review
from app.services import stock_analyzer


@pytest.mark.parametrize('prompt', [
    stock_analyzer._RESEARCH_PLAN_PROMPT, analyzer._SYSTEM_PROMPT,
    review._PROMPT, build_system_prompt(None),
])
def test_public_prompts_explicitly_exclude_operations(prompt):
    assert '不输出买卖、加减仓' in prompt
    assert '分批交易计划' in prompt


@pytest.mark.parametrize('label', ['继续持有', '暂停追加', '条件追加', '条件减仓', '条件部分赎回'])
def test_public_structured_assessment_rejects_position_actions(label):
    with pytest.raises(ValidationError):
        review.HoldingAction(symbol='510300.SH', action=label, reason='数据', trigger='条件', invalidation='反证')


@pytest.mark.parametrize('label', ['证据支持', '证据分歧', '风险需复核', '资料不足待核实'])
def test_assessment_keeps_coverage_dates_and_input_order(label):
    observations = [
        {'symbol': '510300.SH', 'asset_type': 'etf', 'data_date': '2026-10-23', 'daily': [{'raw_close': 4.0}]},
        {'symbol': '000001.OF', 'asset_type': 'fund', 'data_date': '2026-10-22', 'nav': [{'unit_nav': 1.0}]},
    ]
    def row(symbol):
        return {'symbol': symbol, 'action': label, 'reason': '事实依据', 'trigger': '复核条件',
                'invalidation': '反向证据', 'missing_data': []}
    payload = {'content': '客观研究', 'actions': [row('000001.OF'), row('510300.SH')]}
    _, items = review._parse_report(json.dumps(payload), observations)
    assert [item['symbol'] for item in items] == ['510300.SH', '000001.OF']
    assert [item['data_date'] for item in items] == ['2026-10-23', '2026-10-22']
    payload['actions'][0]['symbol'] = '510300.SH'
    with pytest.raises(ValueError):
        review._parse_report(json.dumps(payload), observations)


def test_missing_evidence_cannot_be_called_support():
    payload = {'content': '客观研究', 'actions': [{'symbol': '510300.SH', 'action': '证据支持',
        'reason': '事实依据', 'trigger': '复核条件', 'invalidation': '反向证据'}]}
    with pytest.raises(ValueError, match='dated evidence'):
        review._parse_report(json.dumps(payload), [{'symbol': '510300.SH'}])


@pytest.mark.parametrize('wrapped', [False, True])
def test_old_operation_report_is_not_displayed_or_reused(tmp_path, monkeypatch, wrapped):
    monkeypatch.setattr(review, 'cn_today', lambda: date(2026, 10, 24))
    monkeypatch.setattr(review, '_model', lambda: {'model': 'test'})
    monkeypatch.setattr(review.ai_provider, 'ai_configured', lambda: True)
    holdings = [{'symbol': '510300.SH'}]
    old = {'scope': 'lots', 'status': 'complete', 'version': review.REPORT_VERSION - 1,
           'report_date': '2026-10-24', 'auto_attempt_date': '2026-10-24',
           'holdings_fingerprint': review._fingerprint(holdings), 'model_identity': {'model': 'test'},
           'content': '旧操作结论', 'actions': [{'action': '条件追加'}]}
    report = ({**old, 'version': review.REPORT_VERSION, 'status': 'failed', 'previous_report': old}
              if wrapped else old)
    review._save(tmp_path, 'lots', report)
    original = review._path(tmp_path, 'lots').read_bytes()
    state = review.status(tmp_path, 'lots', holdings)
    assert state['report'] is None
    assert not state['report_current']
    assert review._path(tmp_path, 'lots').read_bytes() == original
    # Reading or reopening an existing same-day attempt never calls the model.
    assert asyncio.run(review.start(tmp_path, 'lots', holdings, lambda _: []))['report'] is None


def test_research_plan_has_no_execution_stages():
    prompt = stock_analyzer._RESEARCH_PLAN_PROMPT
    assert '研究核对表' in prompt
    for heading in ['条件买入计划', '停止加仓与退出条件', '阶段包括观察、首批']:
        assert heading not in prompt


def test_fund_candidate_prompt_uses_neutral_research_labels():
    prompt = fund_routes._AI_PICK_PROMPT
    for action_label in ['满足条件可申购', '候选观察', '暂不选']:
        assert action_label not in prompt
    assert '研究核验标签' in prompt
    assert '证据支持、证据分歧、风险需复核、资料不足待核实' in prompt
    assert '不输出申购、赎回、买卖、加减仓或持有等操作结论' in prompt
