"""基金中心扩展 — 场外基金净值 + ETF/LOF 行情 (fuyao 数据源)。

L2 二开模块: 通过 app.extensions 注册独立路由, 不修改任何核心文件。
删除本目录即可整体卸载, 不影响核心功能。

数据口径 (遵循 docs/plugin-development.md 内部数据契约):
- 快照 change_pct / turnover_rate / amplitude 均为小数制 (0.0174 = 1.74%),
  上游给百分数原值时在 client 边界 /100。
- volume 单位为手 (1 手 = 100 股/份), 上游给股/份时在边界 /100。
- ETF 历史日线: 上游仅提供前复权口径, 接口如实返回并标注
  ``adjusted="forward"``, 不得冒充不复权原始价接入日K管线。
- 场外基金净值: nav_date 为北京时间日期, unit_nav/adj_nav 为元。
- 缺字段置 None, 不启发式伪造; 失败软返回, 不抛异常阻断页面。

路由前缀: /api/custom/fund
"""

from __future__ import annotations

from app.extensions import (
    BACKEND_EXTENSION_API_VERSION,
    BackendExtensionRegistrar,
)

EXTENSION_ID = "fund.center"
EXTENSION_API_VERSION = BACKEND_EXTENSION_API_VERSION


def setup(registrar: BackendExtensionRegistrar) -> None:
    from app.custom.fund.routes import build_router

    registrar.include_router(build_router())
