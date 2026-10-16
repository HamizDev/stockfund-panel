"""On-demand read-only auction source with bounded, reconstructible day caches."""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import threading
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.services.fs_utils import atomic_write_text
from .worker import SYMBOL

CN = ZoneInfo("Asia/Shanghai")
MAX_BYTES = 512 * 1024
SOURCE = "ELTDX 3.2.2 · 免费公开主站"
_SLOT = threading.BoundedSemaphore(1)
_TIME = re.compile(r"^(09:([12]\d):\d{2}|14:5[789]:\d{2}|15:00:0[0-5])$")
_STATES = {"ok", "partial", "empty", "error"}


def _runtime():
    project = Path(__file__).resolve().parents[4]
    root = project / ".eltdx-venv"
    executable = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    # The optional library stays in its existing independent environment.
    metadata = root / "Lib/site-packages/eltdx-3.2.2.dist-info/METADATA"
    return executable if executable.is_file() and metadata.is_file() else None


def status():
    return {"available": _runtime() is not None, "source": SOURCE, "mode": "on_demand",
            "history_days": 30, "full_market": False,
            "hint": "复用本机独立ELTDX 3.2.2环境；无需付费Key，网关3022无需启动"}


def validate(symbol, trade_date, today=None):
    if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
        raise ValueError("请选择带交易所后缀的股票、ETF或LOF代码")
    try:
        target = date.fromisoformat(trade_date)
    except (ValueError, TypeError):
        raise ValueError("交易日期格式须为YYYY-MM-DD") from None
    today = today or datetime.now(CN).date()
    if not 0 <= (today - target).days <= 30:
        raise ValueError("竞价按需查询限今天及此前30个自然日；历史覆盖以实际返回为准")
    return target


def _path(data_dir, symbol, target):
    root = Path(data_dir).resolve() / "auction_research"
    path = root / target.isoformat() / f"{symbol}.json"
    if path.resolve() != path:
        raise ValueError("竞价缓存目录不能链接到其他位置")
    return path


def _empty(symbol, target, state="not_cached"):
    return {"symbol": symbol, "trade_date": target.isoformat(), "source": SOURCE,
            "state": state, "observed_at": None, "cached": False, "stale": False,
            "points": [], "opening_match": None, "reference_price": None,
            "open_change_pct": None, "notes": [], "errors": [],
            "units": {"price": "元", "volume": "手", "amount": "元"}}


def _num(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) and (value > 0 if positive else value >= 0) else None


def _normalize(payload, symbol, target, now):
    result = _empty(symbol, target)
    wire = symbol[-2:].lower() + symbol[:6]
    if not isinstance(payload, dict) or payload.get("wire_symbol") != wire or payload.get("trade_date") != target.isoformat():
        raise ValueError("竞价响应的证券或交易日不匹配")
    rows = payload.get("points")
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError("竞价过程结构或数量无效")
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("time"), str) or not _TIME.fullmatch(row["time"]):
            continue
        if row.get("phase") not in {"opening", "closing"}:
            continue
        if _num(row.get("price"), True) is None or _num(row.get("matched_volume_hand")) is None or _num(row.get("unmatched_volume_hand")) is None:
            continue
        result["points"].append({key: row.get(key) for key in (
            "time", "phase", "price", "matched_volume_hand", "unmatched_volume_hand", "unmatched_direction")})
    result["points"].sort(key=lambda row: row["time"])
    match = payload.get("opening_match")
    if isinstance(match, dict) and match.get("time") == "09:25":
        price, volume, amount = (_num(match.get("price"), True), _num(match.get("volume_hand")), _num(match.get("amount_yuan")))
        if price is not None and volume is not None and amount is not None and abs(amount - price * volume * 100) <= 0.02:
            result["opening_match"] = {"time": "09:25", "price": price, "volume_hand": volume,
                                       "amount_yuan": amount, "amount_method": "成交价×手数×100"}
    ref = _num(payload.get("reference_price"), True)
    result["reference_price"] = ref
    if ref and result["opening_match"]:
        pct = (result["opening_match"]["price"] / ref - 1) * 100
        result["open_change_pct"] = pct if math.isfinite(pct) else None
    for key in ("notes", "errors"):
        values = payload.get(key)
        result[key] = [text[:250] for text in values[:10] if isinstance(text, str)] if isinstance(values, list) else []
    has_points, has_match = bool(result["points"]), result["opening_match"] is not None
    result["state"] = "ok" if has_points and has_match and not result["errors"] else "partial" if has_points or has_match else "error" if result["errors"] else "empty"
    result["observed_at"] = now.isoformat(timespec="seconds")
    return result


def _read(path, symbol, target, now):
    try:
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("symbol") != symbol or data.get("trade_date") != target.isoformat() or data.get("state") not in _STATES:
            return None
        stamp = datetime.fromisoformat(data["observed_at"])
        age = (now - stamp).total_seconds()
        if age < 0 or stamp.astimezone(CN).date() < target:
            return None
        wire_payload = {**data, "wire_symbol": symbol[-2:].lower() + symbol[:6]}
        result = _normalize(wire_payload, symbol, target, stamp)
        # Partial/empty responses are retriable even for historical dates.
        ttl = 86400 if target < now.date() and result["state"] == "ok" else 30
        result.update(cached=True, stale=age >= ttl)
        return result
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _run(executable, symbol, target, today):
    # Do not forward credentials/proxy settings into the research source process.
    allowed = {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "COMSPEC", "TEMP", "TMP", "APPDATA", "LOCALAPPDATA"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1", NO_PROXY="*", no_proxy="*")
    proc = subprocess.run([str(executable), "-B", str(Path(__file__).with_name("worker.py"))],
        input=json.dumps({"symbol": symbol, "trade_date": target.isoformat(), "today": today.isoformat()}),
        capture_output=True, text=True, encoding="utf-8", errors="strict", timeout=28,
        env=env, cwd=Path(__file__).parent,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode != 0 or len(proc.stdout.encode("utf-8")) > MAX_BYTES:
        raise ValueError("竞价源进程未返回有效结果")
    return json.loads(proc.stdout)


def evidence(data_dir, symbol, trade_date, *, refresh=False, now=None):
    now = now or datetime.now(CN)
    target = validate(symbol, trade_date, today=now.date())
    path = _path(data_dir, symbol, target)
    cached = _read(path, symbol, target, now)
    if not refresh:
        return cached or _empty(symbol, target)
    if cached and not cached["stale"]:
        return cached
    executable = _runtime()
    if executable is None:
        result = _empty(symbol, target, "source_unavailable")
        result["errors"] = ["本机未找到独立ELTDX 3.2.2环境；未切换其他行情源"]
        return result
    if not _SLOT.acquire(blocking=False):
        result = _empty(symbol, target, "busy")
        result["errors"] = ["已有竞价查询运行，请稍后重试"]
        return result
    try:
        payload = _run(executable, symbol, target, now.date())
        result = _normalize(payload, symbol, target, datetime.now(CN))
        # Only successful/partial observed evidence is cached. Errors cannot erase earlier evidence.
        if result["points"] or result["opening_match"]:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, json.dumps(result, ensure_ascii=False, allow_nan=False))
        return result
    except (OSError, ValueError, subprocess.SubprocessError, UnicodeError):
        result = _empty(symbol, target, "error")
        result["errors"] = ["免费竞价源超时或不可用，请稍后重试；已有缓存保留"]
        return result
    finally:
        _SLOT.release()
