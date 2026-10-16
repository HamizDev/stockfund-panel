"""Small bounded ELTDX subprocess. No panel imports, secrets, or trading calls."""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import date, datetime, timedelta, timezone

SYMBOL = re.compile(r"^(?:(?:60|68)\d{4}\.SH|(?:00|30)\d{4}\.SZ|5\d{5}\.SH|1[56]\d{4}\.SZ|(?:[48]\d{5}|92\d{4})\.BJ)$")
HOSTS = ("116.205.183.150:7709", "116.205.171.132:7709")


def number(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) and (value > 0 if positive else value >= 0) else None


def is_fund(symbol):
    return (symbol.startswith("5") and symbol.endswith(".SH")) or (
        symbol[:2] in {"15", "16"} and symbol.endswith(".SZ"))


def same_price(left, right, symbol):
    scale = 1000 if is_fund(symbol) else 100
    return number(left, positive=True) is not None and number(right, positive=True) is not None and round(left * scale) == round(right * scale)


def formal_match(raw, symbol, target, anchor, *, is_today):
    """Reject virtual matches; raw price ticks avoid the installed ETF x10 bug."""
    if not isinstance(raw, dict) or raw.get("event_kind") != "opening_match" or raw.get("status_raw") == 8:
        return None
    if raw.get("time_label") != "09:25":
        return None
    stamp = raw.get("trade_datetime")
    if stamp is not None:
        if not isinstance(stamp, str) or stamp[:10] != target.isoformat():
            return None
    elif not is_today:
        return None
    ticks = raw.get("price_acc_raw")
    if not isinstance(ticks, int) or isinstance(ticks, bool) or ticks <= 0:
        return None
    price = ticks / (1000 if is_fund(symbol) else 100)
    opening = number(anchor.get("open"), positive=True) if anchor else None
    # Every formal price must match the requested day's bar, including historical stocks.
    if opening is None:
        return None
    if opening is not None and not same_price(price, opening, symbol):
        return None
    volume = number(raw.get("volume"), positive=True)
    if volume is None:
        return None
    return {"time": "09:25", "price": price, "volume_hand": volume,
            "amount_yuan": round(price * volume * 100, 2),
            "price_verified": opening is not None, "amount_method": "price_times_volume_hand_100"}


def collect(symbol, target, today):
    from eltdx import TdxClient, to_jsonable
    from eltdx.protocol.commands import command_code

    wire = symbol[-2:].lower() + symbol[:6]
    result = {"wire_symbol": wire, "trade_date": target.isoformat(), "points": [],
              "opening_match": None, "reference_price": None, "open_change_pct": None,
              "errors": [], "notes": []}
    with TdxClient(timeout=2, pool_size=1, server_count=1, hosts=HOSTS,
                   probe_hosts=False, heartbeat_interval=None) as client:
        try:
            raw = to_jsonable(client.auctions.series(wire, target.isoformat()))
            if (raw.get("code") != symbol[:6] or raw.get("exchange") != symbol[-2:].lower()
                    or raw.get("trading_date") != target.isoformat()):
                raise ValueError("source identity")
            # Auction prices are integer milli-yuan independent of the asset's trade tick size.
            for row in raw.get("points", [])[:1000]:
                seconds = row.get("time_seconds")
                if not isinstance(seconds, int):
                    continue
                phase = "opening" if 33300 <= seconds <= 33905 else "closing" if 53820 <= seconds <= 54005 else None
                if phase is None:
                    continue
                milli, matched, unmatched = row.get("price_milli"), row.get("matched_volume"), row.get("unmatched_volume")
                if not isinstance(milli, int) or number(milli, positive=True) is None:
                    continue
                if number(matched) is None or number(unmatched) is None:
                    continue
                if target == today:
                    # Independent ELTDX venv has no tzdata on Windows. China has
                    # no DST in this 30-day research window; avoid a new dependency.
                    now = datetime.now(timezone(timedelta(hours=8)))
                    if seconds > now.hour * 3600 + now.minute * 60 + now.second + 5:
                        continue
                result["points"].append({"time": row["time_label"], "phase": phase,
                    "price": milli / 1000, "matched_volume_hand": matched,
                    "unmatched_volume_hand": unmatched, "unmatched_direction": row.get("unmatched_direction_raw")})
        except Exception:
            result["errors"].append("未取得有效竞价过程，主站可能无该日覆盖")

        anchor = None
        previous = None
        try:
            bars = to_jsonable(client.bars.get(wire, period="1d", count=30, adjust="none"))
            if bars.get("code") != symbol[:6] or bars.get("exchange") != symbol[-2:].lower():
                raise ValueError("bar identity")
            rows = sorted(bars.get("bars", []), key=lambda row: row["time"])
            anchor = next((row for row in rows if row["time"][:10] == target.isoformat()), None)
            prior = [row for row in rows if row["time"][:10] < target.isoformat()]
            previous = prior[-1] if prior else None
        except Exception:
            result["notes"].append("对应日K开盘校验不可用；本次不采用正式成交价格")

        try:
            if target == today:
                raw = to_jsonable(client.trades.opening_match_today(wire, max_pages=8))
            else:
                raw = to_jsonable(client.trades.opening_match_history(wire, target.isoformat(), max_pages=8))
            result["opening_match"] = formal_match(raw, symbol, target, anchor, is_today=target == today)
            if raw is not None and result["opening_match"] is None:
                result["errors"].append("09:25成交的日期、价格或类型未通过校验，本次不采用")
        except Exception:
            result["errors"].append("未取得09:25正式成交；分页上限8页，覆盖以主站实际返回为准")

        try:
            ref = None
            if target == today and anchor is not None:
                # In pinned 3.2.2 the high-level quote API discovers every market
                # security's precision. The 0x054c parser uses milli-yuan directly;
                # call its public transport command for this one code only.
                quotes = to_jsonable(client.transport.execute(command_code("snapshots"), {"codes": [wire]}))
                quote = next((q for q in quotes if q.get("code") == symbol[:6]
                              and q.get("exchange") == symbol[-2:].lower()), None)
                if quote and same_price(quote.get("open_price"), anchor["open"], symbol):
                    ref = number(quote.get("pre_close_price"), positive=True)
            elif target != today and previous is not None:
                page = to_jsonable(client.trades.history(wire, target.isoformat(), count=1))
                base = number(page.get("price_base_raw_f32"), positive=True)
                if base is not None and same_price(base, previous["close"], symbol):
                    ref = round(base, 3)
            result["reference_price"] = ref
            opening = result["opening_match"]
            if ref is not None and opening:
                result["open_change_pct"] = (opening["price"] / ref - 1) * 100
        except Exception:
            pass
    result["notes"].extend([
        "过程为虚拟撮合，匹配量和未匹配量单位为手，不能当成已成交订单。",
        "09:25金额按已校验成交价×成交量(手)×100计算；与过程虚拟量分开。",
        "源库ETF逐笔价格按原始三位小数重新解码，并核对对应日K开盘。",
        "历史参考价仅在分笔价格基数与前一日原始收盘相符时展示；不相符时留空。",
        "个人研究用免费公开主站；单只按需查询，未做全市场扫描。",
    ])
    return result


def main():
    payload = json.loads(sys.stdin.buffer.read(4097))
    symbol = payload["symbol"]
    target, today = date.fromisoformat(payload["trade_date"]), date.fromisoformat(payload["today"])
    if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol) or not 0 <= (today - target).days <= 30:
        raise ValueError("unsupported request")
    try:
        result = collect(symbol, target, today)
    except Exception:
        result = {"wire_symbol": symbol[-2:].lower() + symbol[:6], "trade_date": target.isoformat(),
                  "points": [], "opening_match": None, "errors": ["免费主站连接暂不可用，请稍后重试"]}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
