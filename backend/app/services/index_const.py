"""实时指数核心清单 — 产品级固定契约 (单一权威)。

指数展示层 (侧栏指数条 / 市场总览) 固定五只核心指数, 不开放配置:
- 指数行情复用当前实时 provider 的显式代码请求;覆盖范围与可用性取决于已配置 provider;
- 后端消费方 (quote_service / overview / sector_monitor) 引用此处;前端 Layout
  与指数页维护同步清单, 修改时需同步更新, 当前没有自动生成/校验契约。

监控规则的指数标的不受此限 — quote_service 会把启用规则的指数并入显式拉取。
"""

CORE_INDEX_NAMES: dict[str, str] = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000680.SH": "科创综指",
    "000688.SH": "科创50",
}

CORE_INDEX_SYMBOLS: tuple[str, ...] = tuple(CORE_INDEX_NAMES.keys())
