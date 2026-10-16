"""Read-only historical minute scenarios, stored outside automatic account ledgers."""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.market_time import CN_TZ

logger = logging.getLogger(__name__)
MAX_BYTES = 512 * 1024


class ReplayCase(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    initial_cash: float = Field(gt=0)
    max_positions: int = Field(ge=1, le=20)
    signal_at: datetime
    sample_at: datetime
    qty: int = Field(gt=0, multiple_of=100)
    assumed_fill_price: float = Field(gt=0)
    buy_fee: float = Field(ge=0)
    cost: float = Field(gt=0)
    remaining_cash: float = Field(ge=0)
    close_price: float = Field(gt=0)
    close_position_value: float = Field(gt=0)
    close_floating_pnl: float
    available_same_day: Literal[0] = 0

    @model_validator(mode="after")
    def validate_case(self):
        if (
            self.signal_at.tzinfo is None
            or self.sample_at.tzinfo is None
            or self.sample_at <= self.signal_at
        ):
            raise ValueError("Minute samples need an explicit later nominal timestamp")
        amount = Decimal(str(self.assumed_fill_price)) * self.qty
        cost = (amount + Decimal(str(self.buy_fee))).quantize(Decimal("0.01"))
        value = (Decimal(str(self.close_price)) * self.qty).quantize(Decimal("0.01"))
        expected = {
            "cost": cost,
            "remaining_cash": Decimal(str(self.initial_cash)) - cost,
            "close_position_value": value,
            "close_floating_pnl": value - cost,
        }
        if any(
            abs(Decimal(str(getattr(self, key))) - value) > Decimal("0.005")
            for key, value in expected.items()
        ):
            raise ValueError("Stored scenario cash and valuation do not reconcile")
        if cost > Decimal(str(self.initial_cash)) * Decimal("0.8") / self.max_positions:
            raise ValueError("Scenario exceeds its per-symbol budget")
        return self


class PaperReplay(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    title: str = Field(min_length=1, max_length=100)
    date: date
    symbol: str = Field(pattern=r"^\d{6}\.(SH|SZ)$")
    name: str = Field(min_length=1, max_length=60)
    basis: Literal["historical_minute_approx"]
    read_only: Literal[True]
    source_name: str = Field(min_length=1, max_length=100)
    source_response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    commission_pct: float = Field(ge=0, le=1)
    min_commission: float = Field(ge=0)
    slippage_bps: float = Field(ge=0, le=1000)
    raw_close: float = Field(gt=0)
    primary: list[ReplayCase] = Field(min_length=1, max_length=20)
    alternatives: list[ReplayCase] = Field(min_length=1, max_length=1000)
    limitations: list[str] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_bundle(self):
        keys = set()
        for row in self.alternatives:
            key = (row.initial_cash, row.signal_at)
            if key in keys:
                raise ValueError("Duplicate independent entry scenario")
            keys.add(key)
        for row in [*self.primary, *self.alternatives]:
            if any(
                at.astimezone(CN_TZ).date() != self.date for at in (row.signal_at, row.sample_at)
            ):
                raise ValueError("Scenario timestamp differs from its trading date")
            if row.close_price != self.raw_close:
                raise ValueError("Inconsistent closing price")
            fee = max(
                Decimal(str(self.min_commission)),
                Decimal(str(row.assumed_fill_price)) * row.qty * Decimal(str(self.commission_pct)),
            ).quantize(Decimal("0.01"))
            if abs(Decimal(str(row.buy_fee)) - fee) > Decimal("0.005"):
                raise ValueError("Scenario commission differs from disclosed fee model")
        if len({row.initial_cash for row in self.primary}) != len(self.primary):
            raise ValueError("Duplicate primary budget")
        for row in self.primary:
            matching = [r for r in self.alternatives if r.initial_cash == row.initial_cash]
            if not matching or row != min(matching, key=lambda r: r.signal_at):
                raise ValueError("Primary scenario must match the earliest independent entry")
        return self


def list_replays(data_dir: Path) -> dict:
    """Bounded file read; never initializes accounts or executes strategies."""
    root = Path(data_dir) / "paper" / "replays"
    if not root.exists():
        return {"replays": [], "errors": []}
    if not root.resolve().is_relative_to(Path(data_dir).resolve()):
        return {"replays": [], "errors": [{"message": "历史重放目录不可读取"}]}
    results, errors = [], []
    for path in sorted(root.glob("*.json"), reverse=True)[:20]:
        try:
            if (
                not path.resolve().is_relative_to(root.resolve())
                or path.is_symlink()
                or path.stat().st_size > MAX_BYTES
            ):
                raise ValueError("Unsupported replay file")
            replay = PaperReplay.model_validate_json(path.read_bytes())
            if replay.id != path.stem:
                raise ValueError("Replay filename and ID differ")
            results.append(replay.model_dump(mode="json"))
        except (OSError, ValueError):
            logger.warning("Historical paper replay ignored: %s", path.name)
            errors.append({"message": "一份历史重放数据未通过校验，已停止展示"})
    return {
        "replays": sorted(results, key=lambda r: (r["date"], r["id"]), reverse=True),
        "errors": errors,
    }
