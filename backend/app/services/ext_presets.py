# ruff: noqa: RUF001, RUF002, RUF003 -- Chinese labels and documentation use native punctuation.
"""内置扩展数据预设 — 概念/行业启动时只创建配置, 等待用户手动获取 (#199)。

设计原则:
  - provider 负责取得完整归属快照，本模块转换为本地 schema
  - 保留用户自定义 recipe，仅迁移已知旧预设并停止旧自动拉取
  - 当前归属不写成历史分区，完整快照成功后才替换成员表
  - 拉取失败只记 warning, 不阻断启动 (保持「没数据也能跑」)

种子数据通过 provider 的 board_membership 能力取得同花顺 Fuyao 完整快照。
目录与每个板块成分全部成功后才更新缓存；页面读取不触发批量请求。
沪深北 A 股归属按日手动更新，不把获取时间当成公告时间或历史归属证据。

接入点: app.main.lifespan → ensure_builtin_presets(store.data_dir)
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

from app.services.ext_data import (
    ExtConfig,
    ExtConfigStore,
    ExtField,
    PullConfig,
    rows_to_parquet,
)

logger = logging.getLogger(__name__)

# 配置展示地址；实际获取由 provider 完成鉴权、转换和完整快照缓存。
_CONCEPT_DATA_URL = "https://fuyao.aicubes.cn/api/a-share-index/catalog/ths-index-list?tag=cn_concept"
_INDUSTRY_DATA_URL = "https://fuyao.aicubes.cn/api/a-share-index/catalog/ths-index-list?tag=industry"


# ---------------------------------------------------------------------------
# 预设定义: 字段结构 + 拉取配方
# ---------------------------------------------------------------------------

def _concept_preset() -> ExtConfig:
    """扩展概念 (ext_gn_ths)。

    接口结构: [{symbol, name, concepts: [概念1, 概念2, ...]}]
    本地 schema: 股票代码 / 股票简称 / 所属概念(分号拼接) / symbol / code
    """
    return ExtConfig(
        id="ext_gn_ths",
        label="扩展概念",
        mode="snapshot",
        fields=[
            ExtField("symbol", "string", "标的代码"),
            ExtField("code", "string", "代码"),
            ExtField("股票代码", "string", "股票代码"),
            ExtField("股票简称", "string", "股票简称"),
            ExtField("所属概念", "string", "所属概念"),
        ],
        description="同花顺Fuyao概念归属；手动获取，完整快照缓存24小时，不在页面读取时批量请求",
        symbol_map={"type": "mapped", "col": "股票代码"},
        code_map={"type": "computed", "from": "symbol", "method": "strip_exchange"},
        pull=PullConfig(
            url=_CONCEPT_DATA_URL,
            method="GET",
            schedule_minutes=1440,
            # enabled=False: ensure_builtin_presets 承诺启动不拉取, PullScheduler
            # 只调度 enabled 配置; 手动获取走 fetch_preset 独立路径不受影响 (#199)
            enabled=False,
        ),
    )


def _industry_preset() -> ExtConfig:
    """扩展行业 (ext_hy_ths)。

    归属结构: [{symbol, name, industries: [行业归属名称, ...]}]
    本地 schema: 股票代码 / 股票简称 / 所属同花顺行业(分号分隔) / symbol / code
    """
    return ExtConfig(
        id="ext_hy_ths",
        label="扩展行业",
        mode="snapshot",
        fields=[
            ExtField("symbol", "string", "标的代码"),
            ExtField("code", "string", "代码"),
            ExtField("股票代码", "string", "股票代码"),
            ExtField("股票简称", "string", "股票简称"),
            ExtField("所属同花顺行业", "string", "所属同花顺行业"),
        ],
        description="同花顺Fuyao行业归属（不保证三级层级顺序）；手动获取，完整快照缓存24小时",
        symbol_map={"type": "mapped", "col": "股票代码"},
        code_map={"type": "computed", "from": "symbol", "method": "strip_exchange"},
        pull=PullConfig(
            url=_INDUSTRY_DATA_URL,
            method="GET",
            schedule_minutes=1440,
            # 同概念 preset: 出厂禁用, 避免启动即网络拉取 (#199)
            enabled=False,
        ),
    )


def _presets() -> list[ExtConfig]:
    return [_concept_preset(), _industry_preset()]


# ---------------------------------------------------------------------------
# 接口结构 → 本地 schema 转换 (仅预设使用)
# ---------------------------------------------------------------------------

def _symbol_to_code(symbol: str) -> str:
    """symbol (000001.SZ) → code (000001)。"""
    return symbol.split(".", 1)[0] if "." in symbol else symbol


def _dimension_label(value: object) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return ""
    text = str(value).strip()
    return "" if text.casefold() in {"nan", "none", "null"} else text


def _flatten_concept_rows(raw_rows: list[dict]) -> list[dict]:
    """概念: concepts 数组 → 分号拼接成「所属概念」字符串。

    [{symbol, name, concepts:[...]}] → [{股票代码, 股票简称, 所属概念, symbol, code}]
    注: code 由 symbol 派生 (000001.SZ → 000001), 因 rows_to_parquet 不执行 code_map。
    """
    out: list[dict] = []
    for r in raw_rows:
        sym = (r.get("symbol") or "").strip()
        if not sym:
            continue
        concepts = r.get("concepts") or []
        labels = [label for c in concepts if (label := _dimension_label(c))]
        out.append({
            "股票代码": sym,
            "股票简称": r.get("name") or "",
            "所属概念": ";".join(labels),
            "symbol": sym,
            "code": _symbol_to_code(sym),
        })
    return out


def _flatten_industry_rows(raw_rows: list[dict]) -> list[dict]:
    """行业: industries 数组 → 分号分隔的行业归属字符串。

    [{symbol, name, industries:[...]}] → [{股票代码, 股票简称, 所属同花顺行业, symbol, code}]
    """
    out: list[dict] = []
    for r in raw_rows:
        sym = (r.get("symbol") or "").strip()
        if not sym:
            continue
        inds = r.get("industries") or []
        labels = [label for i in inds if (label := _dimension_label(i))]
        out.append({
            "股票代码": sym,
            "股票简称": r.get("name") or "",
            "所属同花顺行业": ";".join(labels),
            "symbol": sym,
            "code": _symbol_to_code(sym),
        })
    return out


# ---------------------------------------------------------------------------
# 拉取执行 (复用 httpx, 不依赖 fetch_and_ingest 的 PullConfig 路径)
# ---------------------------------------------------------------------------

# 部分网络环境 (CDN/WAF/网关) 会把数组包成 {data: [...]}/{list: [...]}/{rows: [...]} 信封。
# 这里做一次兼容解包, 避免误判为「接口返回不是数组」。
_ENVELOPE_KEYS = ("data", "list", "rows", "result", "results")


async def _fetch_json(url: str) -> list[dict]:
    """请求 JSON 接口, 返回行数组。超时 30s, 失败抛异常由调用方兜底。

    兼容两种上游返回形态:
      - 直接是数组: [{...}, ...]          → 原样返回
      - 信封包裹: {data: [{...}]} 等       → 自动解包
    """
    import httpx

    # 延迟导入避免与 ext_pull 循环依赖; 出站请求带 tsp 标识头
    from app.services.ext_pull import outbound_headers

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=outbound_headers())
        resp.raise_for_status()
        data = resp.json()

    if isinstance(data, list):
        return data

    # 信封解包: 在常见键里找第一个值为数组的
    if isinstance(data, dict):
        for key in _ENVELOPE_KEYS:
            inner = data.get(key)
            if isinstance(inner, list):
                return inner
        # 兜底: 遍历所有值, 取第一个数组
        for v in data.values():
            if isinstance(v, list):
                return v

    raise ValueError(
        f"接口返回不是数组 (type={type(data).__name__}), "
        f"响应预览: {str(data)[:200]}"
    )


async def _seed_one(config: ExtConfig, flatten, data_dir: Path) -> int:
    """拉取 + 转换 + 写入单个预设。返回写入行数。"""
    from fastapi.concurrency import run_in_threadpool

    from app.market_time import cn_today
    from app.services.board_membership import get_snapshot

    kind = 'concept' if config.id == 'ext_gn_ths' else 'industry'
    snapshot = await run_in_threadpool(get_snapshot, kind, data_dir, refresh=True)
    if snapshot is None:
        raise RuntimeError('板块归属快照不可用，保留已有数据')
    raw = snapshot['rows']
    rows = flatten(raw)
    if not rows:
        raise RuntimeError('板块归属快照为空，保留已有数据')
    n = rows_to_parquet(rows, config, data_dir, snapshot_date=cn_today(), replace_snapshot=True)
    return n


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def get_preset(config_id: str) -> ExtConfig | None:
    """按 id 取预设定义 (供 API 层校验 id 合法性)。"""
    for c in _presets():
        if c.id == config_id:
            return c
    return None


def is_managed_preset_pull(config: ExtConfig) -> bool:
    preset = get_preset(config.id)
    if preset is None or not config.pull:
        return False
    endpoint = 'ths-concepts' if config.id == 'ext_gn_ths' else 'ths-industries'
    legacy = f'https://shy313.com/api/plugins/market_flow/exports/{endpoint}'
    return config.pull.url in {legacy, preset.pull.url}


async def ensure_builtin_presets(data_dir: Path) -> None:
    """启动时: 为缺失的预设创建 config.json (含 pull 配置), 但【不拉取数据】。

    设计: 数据获取改为用户在概念/行业页手动点「获取数据」触发, 避免启动时
    网络请求阻塞, 也避免「自动拉取」与「用户自主控制」的预期冲突。

    安全保证:
      - 保留用户名称、字段和自定义 URL; 仅迁移已知预设 URL 并关闭旧自动拉取
      - 只写 config.json, 失败只记 warning 不阻断启动
    """
    store = ExtConfigStore(data_dir)

    for config in _presets():
        existing = store.get(config.id)
        if existing is not None:
            if is_managed_preset_pull(existing) and (existing.pull.url != config.pull.url or existing.pull.enabled):
                existing.pull.url = config.pull.url
                existing.pull.enabled = False
                store.upsert(existing)
            # Preserve labels, schema and user-authored recipes; migrate only the known preset recipe.
            continue
        try:
            store.upsert(config)
            logger.info("内置扩展表 %s 配置已就绪 (待用户手动获取数据)", config.id)
        except Exception as e:
            logger.warning("内置扩展表 %s 配置写入失败 (不影响启动): %s", config.id, e)


async def fetch_preset(config_id: str, data_dir: Path) -> int:
    """手动触发某个预设的数据拉取 (供 API 调用)。

    Raises:
        ValueError: config_id 不是内置预设
        Exception: 网络请求/解析/写入失败 (由 API 层转 HTTP 错误)
    """
    config = get_preset(config_id)
    if config is None:
        raise ValueError(f"未知的内置预设: {config_id}")

    flatten = _flatten_concept_rows if config_id == "ext_gn_ths" else _flatten_industry_rows

    # 确保 config.json 存在 (用户可能从未启动过 ensure_builtin_presets)
    store = ExtConfigStore(data_dir)
    if store.get(config_id) is None:
        store.upsert(config)

    n = await _seed_one(config, flatten, data_dir)
    logger.info("内置扩展表 %s 手动拉取成功: %d 行", config_id, n)
    return n
