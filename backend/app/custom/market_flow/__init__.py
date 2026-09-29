"""市场数据扩展 — 概念/行业成分 + 人气排行 + 资金流向 (shy313.com 免费接口)。

L2 二开模块: 通过 app.extensions 注册独立路由, 不修改任何核心文件。
删除本目录即可整体卸载, 不影响核心功能。

数据源: https://shy313.com/api/plugins/market_flow/exports/
- ths-concepts: 个股 → 同花顺概念列表
- ths-industries: 个股 → 同花顺行业列表
- popularity: 人气排行 (rank/symbol/name/heat/change_pct)
- money-flow: 资金流向 (symbol/name/net/inflow/outflow/rank/change_pct)
- money-flow-main: 主力资金 (symbol/name/net/buy/sell/rank/change_pct)

数据口径:
- change_pct 为小数制 (0.0174 = 1.74%), 上游给百分数原值时在 client 边界 /100。
- 资金金额单位为元。
- 低频数据, 服务端缓存 30 分钟。
- 缺字段置 None, 不启发式伪造; 失败软返回, 不抛异常阻断页面。

路由前缀: /api/custom/market-flow
"""
from __future__ import annotations

from app.extensions import (
    BACKEND_EXTENSION_API_VERSION,
    BackendExtensionRegistrar,
)

EXTENSION_ID = "market.flow"
EXTENSION_API_VERSION = BACKEND_EXTENSION_API_VERSION


def setup(registrar: BackendExtensionRegistrar) -> None:
    from app.custom.market_flow.routes import build_router

    registrar.include_router(build_router())
