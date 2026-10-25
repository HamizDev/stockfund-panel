# ruff: noqa: RUF001 -- Native Chinese research text.
from app.services import research_evidence as evidence


def ledger(symbol='515880.SH'):
    return evidence.build(symbol,[{'date':'2026-10-23','close':.512,'raw_close':.512,'change_pct':-.02}],
                          {'sr':[{'value':.532}]},{},{'as_of':'2026-10-23','price_basis':'unadjusted'})


def test_security_date_unit_and_deterministic_calculation_chain():
    result = ledger()
    assert result==ledger()
    assert result['facts']!=ledger('512800.SH')['facts']
    calc = result['calculations'][0]
    assert abs(calc['value']-(.532/.512-1))<1e-12
    ids = {r['id'] for r in result['facts']}
    assert set(calc['inputs']['refs'])<=ids
    assert calc['symbol']=='515880.SH' and calc['as_of']=='2026-10-23'


def test_equal_unreferenced_number_is_not_evidence():
    result=ledger()
    assert evidence.verify('当前价格0.512',result)['issues']
    fact=next(r for r in result['facts'] if r['field'].endswith('.close'))
    check=evidence.verify(f"当前价格0.512 [{fact['id']}]",result)
    assert check['bound']==1 and not check['issues']
    assert evidence.verify(f"当前价格0.600 [{fact['id']}]",result)['issues']


def test_percent_conversion_only_uses_ratio_facts():
    result=ledger()
    price=next(r for r in result['facts'] if r['field'].endswith('.close'))
    ratio=next(r for r in result['facts'] if r['field'].endswith('.change_pct'))
    assert evidence.verify(f"涨幅-2.00% [{ratio['id']}]",result)['bound']==1
    assert evidence.verify(f"涨幅51.2% [{price['id']}]",result)['bound']==0


def test_unknown_references_and_explicit_scenario_are_separate():
    result=ledger()
    check=evidence.verify('假设目标仓位50%，并非真实持仓\n参考[ev-not-found]',result)
    assert check['checked']==0 and check['unknown_references']==['ev-not-found']
    assert '操作建议权限' in evidence.instruction(result)


def test_financial_units_are_not_guessed_from_field_names():
    result=evidence.build('600000.SH',[],{}, {'roe_rate':12.5},{'as_of':'2026-10-23'})
    assert result['facts'][0]['unit']=='原字段单位'


def test_scientific_notation_and_unicode_minus_never_match_their_prefix_only():
    result=evidence.build('600000.SH',[{'date':'2026-10-23','close':1}],{}, {},{'as_of':'2026-10-23'})
    ref=result['facts'][0]['id']
    for value in ['1e6','−1','–1','1,000','1万元','1e9999','1e-9999','0e9999','1e99999999999999999999999999','0e99999999999999999999999999']:
        assert evidence.verify(f'现价{value} [{ref}]',result)['issues']
    assert evidence.verify(f'现价1e0 [{ref}]',result)['bound']==1
