"""txquote HTTP 客户端: 腾讯 q= (五档盘口/分时) + 新浪 K 线 (分钟K)。

职责: 纯 HTTP 抓取与原始解析, 不知道 provider / services 层。
均为公开页面接口 (非官方 API), 结构变化时抛 TxQuoteError, 由 provider 层软处理。

代理: httpx 默认读取 https_proxy/HTTP_PROXY 环境变量, 不 hardcode 绕过。
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

# 腾讯行情: q= 接口, 逗号批量。返回 v_sz000001="..." 文本行。
_QQ_QUOTE_URL = "https://qt.gtimg.cn/q="
# 腾讯分时: 当日逐分钟 (time price volume amount), volume 单位为手 (金额反推验证)。
_QQ_MINUTE_URL = "https://ifzq.gtimg.cn/appstock/app/minute/query"
# 腾讯近5日分时: 每天 1 分钟 K (time price volume amount), volume 单位为手
# (金额=价x量x100 反推验证, 2026-09-27 实测)。单标的, 不支持批量。
_QQ_DAY_MINUTE_URL = "https://web.ifzq.gtimg.cn/appstock/app/day/query"
# 腾讯原生分钟 OHLC (m1/m5/m15/m30/m60); amount is not supplied by this endpoint.
_QQ_ETF_KLINE_URL = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
_QQ_ETF_KLINE_MAX_BYTES = 2 * 1024 * 1024
_QQ_ETF_KLINE_MAX_BARS = 320
# 新浪分钟K: scale=5/15/30/60, 需 UA 头; 返回被 =(...) 包裹的 JSON 数组。
_SINA_KLINE_URL = "https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# q= 响应行: v_sz000001="..."; 字段以 ~ 分隔。
# 0:未知 1:名称 2:代码 3:现价 4:昨收 5:今开 6:成交量(手) 7:外盘 8:内盘
# 9-18: 买1价/买1量 ... 买5价/买5量; 19-28: 卖1价/卖1量 ... 卖5价/卖5量
# 30: 时间(YYYYMMDDHHMMSS)。量已是手, 无需转换。
_QQ_LINE_RE = re.compile(r'v_([a-z]{2}\d+)="([^"]*)"')
_QQ_BATCH_SIZE = 60  # q= 批量上限保守值


class TxQuoteError(Exception):
    """txquote interface error (network failure / structure change / rate limited)."""


def to_qq_symbol(symbol: str) -> str | None:
    """Convert internal code (e.g. 000001.SZ) to Tencent/Sina code (sz000001).

    Returns None for unsupported markets (e.g. Beijing .BJ).
    """
    if not symbol:
        return None
    s = symbol.strip().upper()
    if "." in s:
        code, suffix = s.split(".", 1)
    else:
        code, suffix = s, ""
    prefix = {"SH": "sh", "SZ": "sz"}.get(suffix)
    if prefix is None:
        return None
    if not code.isdigit():
        return None
    return f"{prefix}{code}"


class TxQuoteClient:
    """txquote HTTP 客户端 (线程安全: httpx.Client 可并发复用)。"""

    def __init__(self, timeout: float = 20.0) -> None:
        self._http = httpx.Client(timeout=timeout, headers={"User-Agent": _UA})

    def close(self) -> None:
        self._http.close()

    # ---- 五档盘口 (腾讯 q=) ----
    def depth_batch(self, symbols: list[str]) -> dict[str, dict]:
        """批量取五档盘口。返回 {内部symbol: {bid_prices, bid_volumes, ask_prices,
        ask_volumes, last_price, timestamp}}。量单位为手 (源已是手)。

        单只解析失败跳过不抛错; 整批网络失败抛 TxQuoteError。
        """
        qq_syms: dict[str, str] = {}  # qq_symbol -> internal symbol
        for s in symbols:
            q = to_qq_symbol(s)
            if q:
                qq_syms[q] = s
        if not qq_syms:
            return {}
        out: dict[str, dict] = {}
        keys = list(qq_syms)
        for i in range(0, len(keys), _QQ_BATCH_SIZE):
            chunk = keys[i : i + _QQ_BATCH_SIZE]
            try:
                resp = self._http.get(_QQ_QUOTE_URL + ",".join(chunk))
            except httpx.HTTPError as e:
                raise TxQuoteError(f"腾讯盘口请求失败: {e}") from e
            if resp.status_code != 200:
                raise TxQuoteError(f"腾讯盘口 HTTP {resp.status_code}")
            # gbk 解码: 中文名称为 gbk 编码
            text = resp.content.decode("gbk", errors="ignore")
            for m in _QQ_LINE_RE.finditer(text):
                qq_sym, body = m.group(1), m.group(2)
                internal = qq_syms.get(qq_sym)
                if internal is None:
                    continue
                rec = _parse_qq_depth(body)
                if rec is not None:
                    out[internal] = rec
        return out

    # ---- 分钟K (新浪) ----
    def minute_kline(self, symbol: str, scale: int, datalen: int = 1000) -> list[dict]:
        """新浪分钟K。scale ∈ {5, 15, 30, 60}。返回原始 bar 列表
        (day/open/high/low/close/volume/amount, volume 单位为股)。

        datalen: 取最近 N 根 (上限约 1000)。网络失败/结构变化抛 TxQuoteError。
        """
        qq_sym = to_qq_symbol(symbol)
        if qq_sym is None:
            raise TxQuoteError(f"不支持的代码格式: {symbol}")
        params = {
            "symbol": qq_sym,
            "scale": str(scale),
            "ma": "no",
            "datalen": str(datalen),
        }
        try:
            resp = self._http.get(_SINA_KLINE_URL, params=params)
        except httpx.HTTPError as e:
            raise TxQuoteError(f"新浪分钟K请求失败: {e}") from e
        if resp.status_code != 200:
            raise TxQuoteError(f"新浪分钟K HTTP {resp.status_code}")
        text = resp.text.strip()
        # 剥掉 JSONP 包裹: 实测格式为 /*<script>...</script>*/\n=([...]);
        # 找到 =( 起始位置, 之前的内容全部丢弃
        jsonp_start = text.find("=(")
        if jsonp_start >= 0:
            text = text[jsonp_start + 2:]
        if text.endswith(");"):
            text = text[:-2]
        elif text.endswith(")"):
            text = text[:-1]
        try:
            data = json.loads(text)
        except ValueError as e:
            raise TxQuoteError(f"新浪分钟K响应解析失败: {text[:80]}") from e
        if not isinstance(data, list):
            raise TxQuoteError(f"新浪分钟K响应非数组: {text[:80]}")
        return data

    # ---- 当日分时 (腾讯, 1m 用) ----
    def minute_today(self, symbol: str) -> tuple[str, list[str]]:
        """腾讯当日分时。返回 (交易日期 YYYY-MM-DD, ["HHMM price volume amount", ...])。

        volume 单位为手 (金额=量x价反推验证)。非交易日返回上一交易日数据。
        """
        qq_sym = to_qq_symbol(symbol)
        if qq_sym is None:
            raise TxQuoteError(f"不支持的代码格式: {symbol}")
        try:
            resp = self._http.get(_QQ_MINUTE_URL, params={"code": qq_sym})
        except httpx.HTTPError as e:
            raise TxQuoteError(f"腾讯分时请求失败: {e}") from e
        if resp.status_code != 200:
            raise TxQuoteError(f"腾讯分时 HTTP {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError as e:
            raise TxQuoteError("腾讯分时响应非 JSON") from e
        try:
            node = payload["data"][qq_sym]["data"]
            date_str = node["date"]  # YYYYMMDD
            rows = node["data"]
        except (KeyError, TypeError) as e:
            raise TxQuoteError(f"腾讯分时结构变化: {e}") from e
        date_iso = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        return date_iso, [str(r) for r in rows]

    # ---- 近5日 1 分钟K (腾讯 day/query, 1m 用) ----
    def fetch_day_minute(self, symbol: str) -> list[tuple[str, list[str]]]:
        """腾讯近 5 个交易日 1 分钟K。返回 [(日期 YYYY-MM-DD, ["HHMM price volume amount", ...]), ...]。

        volume 单位为手 (金额=价x量x100 反推验证, 非股, 直接透传)。
        price/amount 单位为元。单标的接口, 批量会报 code param error。
        网络失败/结构变化抛 TxQuoteError。
        """
        qq_sym = to_qq_symbol(symbol)
        if qq_sym is None:
            raise TxQuoteError(f"不支持的代码格式: {symbol}")
        try:
            resp = self._http.get(_QQ_DAY_MINUTE_URL, params={"code": qq_sym})
        except httpx.HTTPError as e:
            raise TxQuoteError(f"腾讯5日分时请求失败: {e}") from e
        if resp.status_code != 200:
            raise TxQuoteError(f"腾讯5日分时 HTTP {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError as e:
            raise TxQuoteError("腾讯5日分时响应非 JSON") from e
        try:
            days = payload["data"][qq_sym]["data"]
        except (KeyError, TypeError) as e:
            raise TxQuoteError(f"腾讯5日分时结构变化: {e}") from e
        out: list[tuple[str, list[str]]] = []
        for day_node in days:
            date_str = day_node.get("date", "")  # YYYYMMDD
            rows = day_node.get("data", [])
            if len(date_str) != 8 or not date_str.isdigit():
                continue
            date_iso = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
            out.append((date_iso, [str(r) for r in rows]))
        return out

    # ---- ETF 原生分钟 OHLC (腾讯 mkline) ----
    def minute_etf_kline(self, symbol: str, period: str, count: int = 320) -> list[dict]:
        """腾讯 ETF 分钟 OHLC。period 为 m1/m5/m15/m30/m60, 最多 320 根。

        返回不复权、北京时间墙钟的 bar。腾讯数组字段顺序为
        time/open/close/high/low/volume/...; 成交额未提供, amount 固定为 None。
        ETF 成交量按腾讯手数口径透传。错误不包含响应正文。
        """
        qq_sym = to_qq_symbol(symbol)
        if qq_sym is None:
            raise TxQuoteError(f"不支持的代码格式: {symbol}")
        period = period.lower() if isinstance(period, str) else ""
        if period not in {"m1", "m5", "m15", "m30", "m60"}:
            raise ValueError("腾讯 ETF 分钟K周期仅支持 m1/m5/m15/m30/m60")
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or not 1 <= count <= _QQ_ETF_KLINE_MAX_BARS
        ):
            raise ValueError(f"腾讯 ETF 分钟K count 范围为 1-{_QQ_ETF_KLINE_MAX_BARS}")

        params = {"param": f"{qq_sym},{period},,{count}"}
        body = bytearray()
        try:
            with self._http.stream(
                "GET",
                _QQ_ETF_KLINE_URL,
                params=params,
                headers={"Referer": "https://gu.qq.com/"},
            ) as response:
                if response.status_code != 200:
                    raise TxQuoteError(f"腾讯 ETF 分钟K HTTP {response.status_code}")
                for chunk in response.iter_bytes():
                    if len(body) + len(chunk) > _QQ_ETF_KLINE_MAX_BYTES:
                        raise TxQuoteError("腾讯 ETF 分钟K响应超过大小限制")
                    body.extend(chunk)
        except TxQuoteError:
            raise
        except httpx.HTTPError as exc:
            raise TxQuoteError("腾讯 ETF 分钟K请求失败") from exc

        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, ValueError) as exc:
            raise TxQuoteError("腾讯 ETF 分钟K响应不是有效 JSON") from exc
        if not isinstance(payload, dict):
            raise TxQuoteError("腾讯 ETF 分钟K响应结构无效")
        if payload.get("msg") == "param error":
            raise TxQuoteError("腾讯 ETF 分钟K请求参数无效")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise TxQuoteError("腾讯 ETF 分钟K响应缺少数据")
        node = data.get(qq_sym)
        if node is None:
            if data:
                raise TxQuoteError("腾讯 ETF 分钟K返回标的与请求不匹配")
            return []
        if not isinstance(node, dict) or not isinstance(node.get(period), list):
            raise TxQuoteError("腾讯 ETF 分钟K响应周期结构无效")
        rows = node[period]
        if len(rows) > count or len(rows) > _QQ_ETF_KLINE_MAX_BARS:
            raise TxQuoteError("腾讯 ETF 分钟K返回根数超过请求上限")

        bars: list[dict] = []
        seen: set[datetime] = set()
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                raise TxQuoteError("腾讯 ETF 分钟K记录字段不足")
            try:
                stamp = datetime.strptime(str(row[0]), "%Y%m%d%H%M")
                values = [self._required_finite_number(row[index]) for index in (1, 2, 3, 4, 5)]
            except (ValueError, TypeError, IndexError, OverflowError) as exc:
                raise TxQuoteError("腾讯 ETF 分钟K记录格式无效") from exc
            if stamp in seen:
                raise TxQuoteError("腾讯 ETF 分钟K存在重复时间")
            seen.add(stamp)
            open_price, close_price, high_price, low_price, volume = values
            if min(open_price, close_price, high_price, low_price) <= 0:
                raise TxQuoteError("腾讯 ETF 分钟K价格必须为正数")
            if high_price < low_price:
                raise TxQuoteError("腾讯 ETF 分钟K最高价低于最低价")
            if high_price < max(open_price, close_price) or low_price > min(open_price, close_price):
                raise TxQuoteError("腾讯 ETF 分钟K高低价与开收盘价不一致")
            if volume < 0:
                raise TxQuoteError("腾讯 ETF 分钟K成交量不能为负数")
            bars.append({
                "datetime": stamp,
                "open": open_price,
                "close": close_price,
                "high": high_price,
                "low": low_price,
                "volume": volume,
                "amount": None,
            })
        return bars

    @staticmethod
    def _required_finite_number(value: object) -> float:
        """Parse a required finite Tencent numeric field without bool coercion."""
        if isinstance(value, bool) or value is None:
            raise ValueError("number required")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("finite number required")
        return number


def _parse_qq_depth(body: str) -> dict | None:
    """解析 q= 单行 body → depth record。字段不足/无盘口返回 None (不伪造)。"""
    f = body.split("~")
    if len(f) < 31:
        return None
    try:
        bid_prices = [float(f[9 + i * 2]) for i in range(5)]
        bid_volumes = [int(float(f[10 + i * 2])) for i in range(5)]
        ask_prices = [float(f[19 + i * 2]) for i in range(5)]
        ask_volumes = [int(float(f[20 + i * 2])) for i in range(5)]
        last_price = float(f[3])
    except (ValueError, IndexError):
        return None
    # 全零盘口 (如停牌) 视为无数据
    if all(p == 0 for p in bid_prices + ask_prices):
        return None
    ts_raw = f[30].strip()  # YYYYMMDDHHMMSS (北京时间)
    timestamp: int | None = None
    if len(ts_raw) == 14 and ts_raw.isdigit():
        try:
            from datetime import datetime, timedelta, timezone

            beijing = timezone(timedelta(hours=8))
            dt = datetime.strptime(ts_raw, "%Y%m%d%H%M%S").replace(tzinfo=beijing)
            timestamp = int(dt.timestamp() * 1000)
        except ValueError:
            timestamp = None
    return {
        "bid_prices": bid_prices,
        "bid_volumes": bid_volumes,  # 手 (源已是手, CONTRIBUTING §3.1)
        "ask_prices": ask_prices,
        "ask_volumes": ask_volumes,  # 手
        "last_price": last_price,
        "timestamp": timestamp,
    }
