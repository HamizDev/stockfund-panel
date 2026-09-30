"""行业成分表缺失时, 指数降级数据应保持看板的涨跌幅量纲。"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.market_overview_builder import _em_industry_rank


def test_tencent_index_percentage_is_converted_to_ratio(monkeypatch):
    values = [""] * 38
    values[1] = "信息指数"
    values[32] = "0.93"
    values[37] = "1000"
    payload = f'v_sh000039="{"~".join(values)}";'.encode("gbk")
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout=payload),
    )

    rank = _em_industry_rank()

    assert abs(rank["leading"][0]["avg_pct"] - 0.0093) < 1e-10
    assert rank["leading"][0]["index_code"] == "000039.SH"
