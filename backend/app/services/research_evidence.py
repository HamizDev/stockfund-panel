# ruff: noqa: RUF001 -- Research provenance labels.
"""Security-bound evidence IDs and conservative report checks, independent of advice policy.

The check never proves a forecast correct. Only explicitly cited values are
matched; an unrelated equal number elsewhere in a prompt is not evidence.
"""
import hashlib
import json
import math
import re
from decimal import Decimal, DecimalException

NUM = re.compile(r'(?<![A-Za-z0-9_.])[+\-−﹣－–—]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+\-]?\d+)?(?:%|％)?')
SCALE = re.compile(r'^\s*(万亿|千万|百万|亿|万)')
MULTIPLIERS = {'万亿':1e12,'千万':1e7,'百万':1e6,'亿':1e8,'万':1e4}
REF = re.compile(r'\[((?:ev|calc)-[A-Za-z0-9_-]{1,40})\]')
DATES = re.compile(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}|\b\d{1,2}:\d{2}(?::\d{2})?\b')


def _finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def build(symbol: str, rows: list[dict], levels: dict, financials: dict, snapshot: dict) -> dict:
    facts, calculations = [],[]
    basis = snapshot.get('price_basis') or '前复权研究价；raw字段为原价'

    def add(path,value,as_of,unit='原字段单位', *, record_type='ev',inputs=None):
        row = {'symbol':symbol,'field':path,'value':value,'as_of':str(as_of or '未记录'),
               'unit':unit,'price_basis':basis}
        if inputs:
            row['inputs'] = inputs
        identity = json.dumps(row,sort_keys=True,ensure_ascii=False,default=str,allow_nan=False)
        row['id'] = record_type+'-'+hashlib.sha256(identity.encode()).hexdigest()[:12]
        (calculations if record_type=='calc' else facts).append(row)
        return row['id']

    def walk(obj,path,as_of,depth=0):
        if len(facts)>=160 or depth>5:
            return
        if _finite(obj):
            unit = '小数比例' if path.startswith(('日K','分钟')) and path.endswith('.change_pct') else '原字段单位'
            if (path.startswith(('日K','分钟')) and path.rsplit('.',1)[-1] in {'close','raw_close','open','high','low','ma5','ma10','ma20','ma60'}) or (path.startswith('关键价位') and path.endswith('.value')):
                unit = '行情价格/点位'
            add(path,float(obj),as_of,unit)
        elif isinstance(obj,dict):
            stamp = obj.get('announce_date') or obj.get('datetime') or obj.get('date') or obj.get('period_end') or as_of
            for key,value in obj.items():
                if key not in {'symbol','date','datetime','quote_ts','requested_at'}:
                    walk(value,f'{path}.{key}',stamp,depth+1)
        elif isinstance(obj,list):
            for index,row in enumerate(obj[-6:]):
                walk(row,f'{path}[{index}]',as_of,depth+1)

    walk(rows[-3:],'日K',snapshot.get('as_of'))
    walk((snapshot.get('minute_rows') or [])[-5:],'分钟',snapshot.get('intraday_as_of'))
    walk(levels,'关键价位',snapshot.get('as_of'))
    walk(financials,'财务',snapshot.get('as_of'))
    if rows and _finite(rows[-1].get('close')) and rows[-1]['close']>0:
        close = float(rows[-1]['close'])
        close_ref = next((r['id'] for r in reversed(facts) if r['field'].startswith('日K') and r['field'].endswith('.close')),None)
        for row in list(facts):
            if close_ref and row['field'].startswith('关键价位') and row['field'].endswith('.value'):
                add(row['field']+'距当前研究价',row['value']/close-1,row['as_of'],'小数比例',
                    record_type='calc',inputs={'formula':'level / close - 1','refs':[row['id'],close_ref]})
    return {'version':1,'symbol':symbol,'as_of':snapshot.get('as_of'),'facts':facts,'calculations':calculations,
            'limits':'只核对引文数值及计算绑定，不验证语义因果、未来预测或操作建议的正确性。'}


def instruction(ledger: dict) -> str:
    return ('\n## 数字证据附件\n以下附件属于本次指定标的。引用事实数字后标[ev-编号]，派生数字标[calc-编号]；'
            '不要引用其他标的或日期。源字段保持原单位，小数比例展示百分数时显式换算。'
            '目标仓位或举例需明确写“假设”，不能冒充已知事实；不要为满足格式编造证据编号。'
            '这项核对不改变当前报告的操作建议权限和输出结构。\n'+json.dumps(ledger,ensure_ascii=False,default=str))


def verify(report: str, ledger: dict) -> dict:
    by_id = {r['id']:r for r in ledger['facts']+ledger['calculations']}
    issues, checked, bound = [],0,0
    unknown = sorted(set(REF.findall(report))-set(by_id))
    for lineno,line in enumerate(report.splitlines(),1):
        probe = DATES.sub('',REF.sub('',line)).replace(ledger['symbol'],'')
        probe = re.sub(r'^\s*#{0,6}\s*\d+[.、)）]\s*','',probe)
        # Target allocation is a proposed scenario, not a historical fact.
        if re.match(r'^\s*(?:[-*]\s*)?(?:假设|假如|目标仓位假设)',probe):
            continue
        tokens = list(NUM.finditer(probe))
        refs = [by_id[r] for r in REF.findall(line) if r in by_id]
        for token in tokens:
            raw = token.group()
            checked += 1
            numeric = raw.rstrip('%％').translate(str.maketrans({'−':'-','﹣':'-','－':'-','–':'-','—':'-',',':''}))
            try:
                decimal = Decimal(numeric)
                value = float(decimal)
            except (DecimalException, ValueError, OverflowError):
                if len(issues)<40:
                    issues.append({'line':lineno,'number':raw,'reason':'数值超出可核对范围'})
                continue
            if not math.isfinite(value) or (value==0 and decimal!=0) or not -308<=decimal.as_tuple().exponent<=308:
                if len(issues)<40:
                    issues.append({'line':lineno,'number':raw,'reason':'数值超出可核对范围'})
                continue
            if raw.endswith(('%','％')):
                value /= 100
            scale = SCALE.match(probe[token.end():])
            multiplier = MULTIPLIERS[scale.group(1)] if scale else 1
            value *= multiplier
            if not math.isfinite(value):
                if len(issues)<40:
                    issues.append({'line':lineno,'number':raw,'reason':'单位换算后超出可核对范围'})
                continue
            # Tolerance follows displayed precision, with a small floor.
            tolerance = .5*10.0**decimal.as_tuple().exponent*multiplier/(100 if raw.endswith(('%','％')) else 1)+1e-10
            compatible = [r for r in refs if r['symbol']==ledger['symbol'] and _finite(r['value'])
                          and (not raw.endswith(('%','％')) or r['unit']=='小数比例')]
            if any(abs(r['value']-value)<=tolerance for r in compatible):
                bound += 1
            elif len(issues)<40:
                issues.append({'line':lineno,'number':raw,'reason':'未与该行引用的本标的数据对应' if refs else '缺少逐项证据引用'})
    return {'status':'needs_review' if issues or unknown else ('checked' if checked else 'no_numeric_claims'),
            'checked':checked,'bound':bound,'issues':issues,'unknown_references':unknown,
            'limits':ledger['limits']}
