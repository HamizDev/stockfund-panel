# ruff: noqa: RUF001 -- Research captions.
"""Research orchestration reuses the repository and reads paper ledgers only."""
from datetime import timedelta

from app.market_time import cn_today
from app.strategy import paper


def paper_xray(repo, data_dir, account_id: str) -> dict:
    from .portfolio import calculate
    account_id = paper.validate_account_id(account_id)
    account = paper.get_account(data_dir,account_id)
    if account is None:
        raise ValueError('模拟账户不存在')
    positions = paper.load_positions(data_dir,account_id)
    held = [(symbol,row) for symbol,row in positions.items() if row.get('qty',0)>0]
    if len(held)>30:
        raise ValueError('一次最多核对30个持仓')
    names = repo.get_name_map([symbol for symbol,_ in held])
    today = cn_today()
    payload,histories,gaps = [],{},[]
    for symbol,row in held:
        asset_type = repo.resolve_asset_type(symbol)
        frame = repo.get_daily_asset(asset_type,symbol,today-timedelta(days=400),today)
        if frame.is_empty() or 'raw_close' not in frame.columns:
            gaps.append(f'{names.get(symbol,symbol)}：缺不复权市价，未纳入市值或权重')
            continue
        import polars as pl
        frame = frame.sort('date')
        latest = frame.tail(1).to_dicts()[0]
        price = latest.get('raw_close')
        if not isinstance(price,(int,float)) or isinstance(price,bool) or price<=0:
            gaps.append(f'{names.get(symbol,symbol)}：无有效不复权市价')
            continue
        payload.append({'symbol':symbol,'name':names.get(symbol,symbol),'market':'cn','currency':'CNY',
                        'qty':row['qty'],'price':price,'price_date':str(latest['date']),
                        'industry':'ETF / 基金（未穿透）' if asset_type=='etf' else '未分类'})
        # Risk uses completed adjusted bars; current partial bars never become
        # a full-day return. Valuation above explicitly uses the raw price.
        history = frame.filter(pl.col('date')<today).select('date','close').tail(260).to_dicts()
        histories[symbol] = [{'date':str(item['date']),'close':item['close']} for item in history]
    result = calculate(payload,histories)
    result['gaps'] = gaps+result.get('gaps',[])
    result.update(account=account_id,account_name=account.get('name') or account_id,
                  holdings_total=len(held),holdings_valued=len(payload),
                  note='模拟账户只读体检。市值用不复权价；风险用已完成前复权日线。静态当前权重试算不是账户真实净值或历史回测，ETF未穿透行业；现金未纳入持仓权重。')
    return result
