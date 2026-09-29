"""fuyao 基金端点 HTTP 客户端。

只依赖 httpx(后端已有) 与 secrets_store, 不导入任何插件模块,
保证扩展目录自包含、可整体卸载。

Key 语义与插件一致: secrets.json 的 fuyao_api_key 优先, 回退
FUYAO_API_KEY 环境变量。未配置时抛 FundNotConfiguredError, 路由层转 503。
"""

from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://fuyao.aicubes.cn"
_TIMEOUT = 20.0
# 连续单标的请求时的礼貌间隔, 避免触发服务端限流
_POLITE_INTERVAL_S = 0.12


class FundError(Exception):
    """fuyao 基金接口业务错误 (code != 0)。"""


class FundNotConfiguredError(Exception):
    """未配置 fuyao API Key。"""


def _get_api_key() -> str:
    from app.secrets_store import get_env_backed_secret

    key = get_env_backed_secret("fuyao_api_key", "FUYAO_API_KEY")
    if not key:
        raise FundNotConfiguredError(
            "未配置 fuyao API Key: 请在设置页数据源卡片填写, 或在 .env 配置 FUYAO_API_KEY"
        )
    return key


class FuyaoFundClient:
    """fuyao 基金端点客户端: 检索 / 场内行情 / 净值 / 资料。"""

    def __init__(self, api_key: str | None = None, timeout: float = _TIMEOUT) -> None:
        self._api_key = api_key or _get_api_key()
        self._client = httpx.Client(timeout=timeout)
        self._last_call_ts = 0.0

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: dict, *, raw: bool = False) -> dict:
        # 单标的端点串行调用时加礼貌间隔
        now = time.monotonic()
        wait = _POLITE_INTERVAL_S - (now - self._last_call_ts)
        if wait > 0:
            time.sleep(wait)
        try:
            resp = self._client.get(
                BASE_URL + path,
                params=params,
                headers={"X-api-key": self._api_key},
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise FundError(f"扶摇基金服务暂不可用 ({path})") from e
        finally:
            self._last_call_ts = time.monotonic()
        payload = resp.json()
        if raw:
            return payload
        code = payload.get("code")
        if code != 0:
            raise FundError(
                f"扶摇基金接口错误 code={code}: {payload.get('message', '')} ({path})"
            )
        data = payload.get("data") or {}
        return data

    def search(
        self, q: str, asset_types: str = "fund-otc,fund-etf,fund-lof", limit: int = 20
    ) -> list[dict]:
        """标的检索: q 支持代码/名称子串; asset_types 为 fuyao 枚举逗号拼接。"""
        data = self._get(
            "/api/meta/tickers/search",
            {"q": q, "asset_type": asset_types, "limit": max(1, min(50, limit))},
        )
        rows = data.get("item")
        return rows if isinstance(rows, list) else []

    def snapshot(self, thscode: str) -> dict | None:
        """场内基金(ETF/LOF)实时快照。非场内基金返回 None:
        场外基金 → code=3004, 股票等非基金代码 → code=3001。"""
        try:
            data = self._get("/api/fund/market/snapshot", {"thscode": thscode})
        except FundError as e:
            if "code=3004" in str(e) or "code=3001" in str(e):
                return None
            raise
        items = data.get("item")
        if not items:
            return None
        row = dict(items[0])
        # 服务端快照时间 (行情归属), 行内无 timestamp 字段时补上
        if row.get("timestamp") is None and data.get("timestamp"):
            row["timestamp"] = data.get("timestamp")
        return row

    def kline(self, thscode: str, start_ms: int, end_ms: int) -> list[dict]:
        """场内基金历史日线 (仅 ETF; 上游为前复权口径, 调用方必须如实标注)。"""
        data = self._get(
            "/api/fund/market/historical",
            {"thscode": thscode, "interval": "1d", "start": int(start_ms), "end": int(end_ms)},
        )
        rows = data.get("item")
        return rows if isinstance(rows, list) else []

    def nav(
        self, thscode: str, range_: str = "year", nav_type: str = "unit,adj"
    ) -> list[dict]:
        """基金净值序列: unit_nav 单位净值, adj_nav 复权净值 (按 nav_date 升序)。"""
        data = self._get(
            "/api/fund/performance/nav",
            {"thscode": thscode, "range": range_, "nav_type": nav_type},
        )
        rows = data.get("item")
        return rows if isinstance(rows, list) else []

    def profile(self, thscode: str) -> dict | None:
        """基金基本资料: fund_name / estab_date / mgmt_name / manager_name 等。"""
        data = self._get("/api/fund/profile/detail", {"thscode": thscode})
        items = data.get("item")
        if not items:
            return None
        return items[0]

    def holdings(self, thscode: str) -> dict:
        """基金重仓持仓: 定期披露的股票/债券/基金持仓 (季度披露, 非实时)。

        返回: {stock_ratio_pct, total_stock_ratio_pct, concentration_ratio, item: [...]}
        item 每行: {thscode, ticker, stock_name, hold_ratio(%), asset_type}
        """
        return self._get("/api/fund/portfolio/holdings", {"thscode": thscode})
