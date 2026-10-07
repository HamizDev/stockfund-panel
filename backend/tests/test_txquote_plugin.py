"""txquote 插件契约测试 (不依赖真实网络)。

覆盖:
1. 代码格式转换: 内部代码 (000001.SZ) → 腾讯/新浪代码 (sz000001); 北交所返回 None
2. 五档盘口解析: 字段位置、量单位为手 (源已是手, 不二次转换)、缺字段/全零跳过
3. 分钟K归一: 新浪股→手 (floor /100)、datetime 解析、列顺序
4. freq 映射: 1m/5m/15m/30m/60m 支持, 其他抛 ValueError 明示
5. 软失败: 网络异常返回空, 不抛错阻断上游
"""

from __future__ import annotations

import pytest

from app.plugins.txquote import client as txc
from app.plugins.txquote.client import TxQuoteClient, TxQuoteError, to_qq_symbol
from app.plugins.txquote.provider import TxQuoteProvider, _normalize_minute_frame

# ---------- to_qq_symbol ----------


def test_to_qq_symbol():
    assert to_qq_symbol("000001.SZ") == "sz000001"
    assert to_qq_symbol("600000.SH") == "sh600000"
    assert to_qq_symbol("300001.SZ") == "sz300001"
    assert to_qq_symbol("688001.SH") == "sh688001"
    # 小写/空格容忍
    assert to_qq_symbol(" 000001.sz ") == "sz000001"


def test_to_qq_symbol_bj_unsupported():
    # 北交所源不支持 → None, 调用方跳过
    assert to_qq_symbol("430001.BJ") is None
    assert to_qq_symbol("") is None
    assert to_qq_symbol("ABC") is None


# ---------- depth 解析 ----------

_QQ_BODY = (
    "51~平安银行~000001~11.30~11.35~11.35~1043819~475274~568545"
    "~11.30~2697~11.29~10758~11.28~5643~11.27~1406~11.26~2556"  # 买1-5
    "~11.31~2612~11.32~1174~11.33~785~11.34~140~11.35~904"  # 卖1-5
    "~~20260924161421~"
)


def test_parse_qq_depth():
    rec = txc._parse_qq_depth(_QQ_BODY)
    assert rec is not None
    assert rec["bid_prices"] == [11.30, 11.29, 11.28, 11.27, 11.26]
    assert rec["bid_volumes"] == [2697, 10758, 5643, 1406, 2556]
    assert rec["ask_prices"] == [11.31, 11.32, 11.33, 11.34, 11.35]
    assert rec["ask_volumes"] == [2612, 1174, 785, 140, 904]
    assert rec["last_price"] == pytest.approx(11.30)
    # 时间戳: 2026-09-24 16:14:21 北京 → epoch ms
    assert rec["timestamp"] == 1790237661000
    # 量单位为手: 源已是手, 不做 /100 二次转换 (CONTRIBUTING §3.1)


def test_parse_qq_depth_short_body():
    assert txc._parse_qq_depth("51~a~b") is None


def test_parse_qq_depth_all_zero():
    body = "~".join(["51", "停牌股", "000002", "0", "0", "0", "0", "0", "0"]
                    + ["0", "0"] * 10 + ["", "20260924161421", ""])
    assert txc._parse_qq_depth(body) is None


# ---------- depth_batch (mock HTTP) ----------

class _FakeResp:
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code


class _FakeHttp:
    def __init__(self, content: bytes, status_code: int = 200):
        self._content = content
        self._status = status_code
        self.urls: list[str] = []

    def get(self, url, params=None):
        self.urls.append(url)
        return _FakeResp(self._content, self._status)

    def close(self):
        pass


def _depth_client(body: str) -> TxQuoteClient:
    c = TxQuoteClient.__new__(TxQuoteClient)
    c._http = _FakeHttp(body.encode("gbk"))
    return c


def test_depth_batch_maps_symbols():
    body = f'v_sz000001="{_QQ_BODY}";\nv_sh600000="{_QQ_BODY}";\n'
    client = _depth_client(body)
    out = client.depth_batch(["000001.SZ", "600000.SH", "430001.BJ"])
    assert set(out) == {"000001.SZ", "600000.SH"}  # 北交所跳过
    assert out["000001.SZ"]["bid_volumes"][0] == 2697
    # 批量 URL 含逗号分隔
    assert "sz000001,sh600000" in client._http.urls[0]


def test_depth_batch_http_error():
    c = TxQuoteClient.__new__(TxQuoteClient)
    c._http = _FakeHttp(b"", status_code=502)
    with pytest.raises(TxQuoteError):
        c.depth_batch(["000001.SZ"])


# ---------- provider.get_depth_batch 软失败 ----------

def test_provider_depth_soft_fail(monkeypatch):
    p = TxQuoteProvider()

    def _boom(self, symbols):
        raise TxQuoteError("net down")

    monkeypatch.setattr(p, "_get_client", lambda: type("C", (), {"depth_batch": _boom})())
    assert p.get_depth_batch(["000001.SZ"]) == {}
    assert p.get_depth_batch([]) == {}


# ---------- minute 归一 ----------

_SINA_BARS = [
    {"day": "2026-09-24 14:55:00", "open": "11.310", "high": "11.310",
     "low": "11.300", "close": "11.310", "volume": "2770279",
     "amount": "31315029.9984"},
    {"day": "2026-09-24 15:00:00", "open": "11.310", "high": "11.310",
     "low": "11.290", "close": "11.300", "volume": "3713240",
     "amount": "41958346.8986"},
]


def test_normalize_minute_frame_units():
    recs = [
        {"symbol": "000001.SZ", "datetime": b["day"], "open": float(b["open"]),
         "high": float(b["high"]), "low": float(b["low"]), "close": float(b["close"]),
         # 模拟 provider 层已做的股→手转换 (floor /100)
         "volume": int(b["volume"]) // 100, "amount": float(b["amount"])}
        for b in _SINA_BARS
    ]
    df = _normalize_minute_frame(recs)
    assert df.columns == ["symbol", "datetime", "open", "high", "low",
                          "close", "volume", "amount"]
    assert df["volume"].to_list() == [27702, 37132]
    # datetime 为 Datetime(us)
    import polars as pl

    assert df.schema["datetime"] == pl.Datetime("us")
    assert df["close"].to_list() == pytest.approx([11.310, 11.300])


def test_provider_minute_unsupported_freq():
    p = TxQuoteProvider()
    with pytest.raises(ValueError, match="不支持 freq"):
        p.get_minute(["000001.SZ"], None, None, freq="2m")


def test_provider_minute_empty_symbols():
    p = TxQuoteProvider()
    import polars as pl

    out = p.get_minute([], None, None)
    assert isinstance(out, pl.DataFrame) and out.is_empty()
    out = p.get_minute(["510300.SH"], None, None, asset_type="etf")
    assert out.is_empty()


# ---------- provider 数据集声明 ----------

def test_provider_datasets():
    from app.plugins.txquote.provider import _DATASETS

    assert set(_DATASETS) == {"minute", "depth5"}
    p = TxQuoteProvider()
    assert p.name == "txquote"
    assert set(p.config.datasets) == {"minute", "depth5"}


# ---------- fetch_day_minute (近5日 1 分钟K) ----------


class _FakeJsonResp:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _FakeJsonHttp:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self._status = status_code
        self.urls: list[str] = []

    def get(self, url, params=None):
        self.urls.append(url + "?" + str(params))
        return _FakeJsonResp(self._payload, self._status)

    def close(self):
        pass


_DAY_MINUTE_PAYLOAD = {
    "code": 0,
    "msg": "",
    "data": {
        "sh600519": {
            "data": [
                {"date": "20260924",
                 "data": ["0930 1250.01 183 22875182.71",
                          "0931 1249.99 1393 174122899.37"]},
                {"date": "20260923",
                 "data": ["0930 1248.50 200 24970000.00"]},
            ]
        }
    },
}


def _day_minute_client(payload: dict = _DAY_MINUTE_PAYLOAD) -> TxQuoteClient:
    c = TxQuoteClient.__new__(TxQuoteClient)
    c._http = _FakeJsonHttp(payload)
    return c


def test_fetch_day_minute_parses_dates():
    client = _day_minute_client()
    out = client.fetch_day_minute("600519.SH")
    assert len(out) == 2
    assert out[0][0] == "2026-09-24"
    assert out[0][1] == ["0930 1250.01 183 22875182.71",
                         "0931 1249.99 1393 174122899.37"]
    assert out[1][0] == "2026-09-23"
    # code 参数为单只腾讯代码
    assert "sh600519" in client._http.urls[0]


def test_fetch_day_minute_bad_symbol():
    client = _day_minute_client()
    with pytest.raises(TxQuoteError):
        client.fetch_day_minute("430001.BJ")


def test_provider_1m_uses_5day_window(monkeypatch):
    """1m 走 day/query: 窗口过滤多天数据, volume 为手直接透传 (不 /100)。"""
    from datetime import datetime

    p = TxQuoteProvider()
    client = _day_minute_client()
    monkeypatch.setattr(p, "_get_client", lambda: client)

    # 窗口覆盖两天 → 两天数据都回来
    df = p.get_minute(
        ["600519.SH"],
        datetime(2026, 9, 23), datetime(2026, 9, 24, 23, 59), freq="1m",
    )
    assert len(df) == 3
    assert df["symbol"].to_list() == ["600519.SH"] * 3
    # volume 为手: 不做 /100 (金额=价x量x100 反推验证); 按 datetime 升序
    assert df["volume"].to_list() == pytest.approx([200.0, 183.0, 1393.0])
    assert df["close"].to_list() == pytest.approx([1248.50, 1250.01, 1249.99])
    # datetime 为北京时间墙钟 naive
    dts = df["datetime"].to_list()
    assert str(dts[0]) == "2026-09-23 09:30:00"
    assert str(dts[1]) == "2026-09-24 09:30:00"

    # 窗口只覆盖 09-24 → 09-23 被滤掉
    df2 = p.get_minute(
        ["600519.SH"],
        datetime(2026, 9, 24), datetime(2026, 9, 24, 23, 59), freq="1m",
    )
    assert len(df2) == 2

    # minute_history_days 已更新为 5
    assert p.minute_history_days == 5
