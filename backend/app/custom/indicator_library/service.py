# ruff: noqa: RUF001 -- Chinese research messages.
"""Bounded public script discovery. No login, background harvesting or Pine execution.

Endpoint shape/normalization adapted from daviddme's MIT-licensed
tradingview-indicator-search-mcp-server; see docs/indicator-library.md.
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from app.config import settings

from .catalog import TOPICS

LIST_TTL = 6 * 3600
SOURCE_TTL = 6 * 3600
MAX_SOURCES = 40
MAX_SOURCE_BYTES = 256 * 1024
MAX_TOTAL_SOURCE_BYTES = 6 * 1024 * 1024
ID_PATTERN = re.compile(r"PUB;[A-Za-z0-9]{1,100}\Z")
_clock = time.time


class LibraryError(Exception):
    pass


@contextmanager
def _database():
    path = Path(settings.data_dir) / "cache" / "indicator-library.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=3)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA max_page_count=4096")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS scripts (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, seen REAL NOT NULL,
                source TEXT, source_version TEXT, source_at REAL, source_bytes INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS listings (key TEXT PRIMARY KEY, payload TEXT, fetched REAL);
            CREATE TABLE IF NOT EXISTS gate (id INTEGER PRIMARY KEY, day TEXT, calls INTEGER,
                last_call REAL, cooldown REAL);
        """)
        yield connection
        connection.commit()
    finally:
        connection.close()


def _claim_request():
    """SQLite serializes the reservation across processes; never sleep while locked."""
    now = _clock()
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    with _database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM gate WHERE id=1").fetchone()
        if row and row["cooldown"] > now:
            raise LibraryError("公开脚本服务暂时冷却；可继续查看本地研究和已缓存源码")
        calls = row["calls"] if row and row["day"] == day else 0
        if calls >= 100:
            raise LibraryError("今日研究查询已达本机 100 次上限；可继续使用离线库")
        if row and now - row["last_call"] < 2:
            raise LibraryError("查询过快，请稍候 2 秒再试")
        db.execute("INSERT OR REPLACE INTO gate VALUES(1,?,?,?,0)", (day, calls + 1, now))


def _cooldown():
    with _database() as db:
        db.execute("UPDATE gate SET cooldown=? WHERE id=1", (_clock() + 900,))


def _json(url: str, params: dict) -> dict:
    _claim_request()
    try:
        with (
            httpx.Client(timeout=httpx.Timeout(10, connect=4), follow_redirects=False) as client,
            client.stream("GET", url, params=params, headers={
                "User-Agent": "Mozilla/5.0 stockfund-panel research",
                "Origin": "https://www.tradingview.com", "Referer": "https://www.tradingview.com/",
            }) as response,
        ):
            if response.status_code in (401, 403, 429):
                _cooldown()
                raise LibraryError("公开脚本访问受限，已暂停在线请求 15 分钟；离线库仍可用")
            if response.status_code != 200:
                raise LibraryError("公开脚本暂不可用或没有该主题，请换主题或查看离线库")
            raw = bytearray()
            for part in response.iter_bytes():
                raw.extend(part)
                if len(raw) > 2 * 1024 * 1024:
                    raise LibraryError("公开脚本响应超过读取上限")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise LibraryError("公开脚本格式发生变化，请稍后再试")
        return result
    except (httpx.HTTPError, ValueError):
        raise LibraryError("公开脚本网络连接失败；可继续查看本地研究和已缓存源码") from None


def topic_slug(value: str) -> str:
    query = value.strip().lower()
    if not query:
        return ""
    for topic in TOPICS:
        if query in [topic["id"], topic["name"].lower(), *topic["aliases"]]:
            return topic["id"]
    if re.search(r"[^a-z0-9\s_-]", query):
        raise ValueError("该中文主题尚未映射，请选下方主题；离线检索支持任意中文")
    slug = re.sub(r"[^a-z0-9]+", "-", query).strip("-")
    if not slug or len(slug) > 80:
        raise ValueError("请输入有效主题")
    return slug


def _text(value, size=250):
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", value))).strip()[:size]


def _chart_url(value):
    if not isinstance(value, str) or len(value) > 500:
        return None
    try:
        url = urlsplit(value)
        if (url.scheme == "https" and url.netloc == "www.tradingview.com"
                and re.fullmatch(r"/script/[A-Za-z0-9-]+/?", url.path) and not url.query and not url.fragment):
            return value
    except ValueError:
        pass
    return None


def normalize_item(raw: dict) -> dict | None:
    if not isinstance(raw, dict) or raw.get("script_access") != 1:
        return None
    script_id = raw.get("script_id_part")
    if not isinstance(script_id, str) or not ID_PATTERN.fullmatch(script_id):
        return None
    version = str(raw.get("version", ""))
    if not re.fullmatch(r"[0-9]{1,6}(?:\.[0-9]{1,6})?", version):
        version = "last"
    kind = raw.get("script_type")
    user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
    return {"id": script_id, "name": _text(raw.get("name"), 160) or "未命名脚本",
            "author": _text(user.get("username"), 80),
            "kind": kind if kind in ("indicator", "strategy", "library") else "indicator",
            "version": version, "description": _text(raw.get("description"), 500),
            "url": _chart_url(raw.get("chart_url")), "source_cached": False}


def search(query: str = "", author: str = "", kind: str = "all", page: int = 1,
           offline: bool = False) -> dict:
    query, author = query.strip(), author.strip()
    if len(query) > 80 or kind not in ("all", "indicator", "strategy", "library") or not 1 <= page <= 20:
        raise ValueError("查询条件无效")
    if author and not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", author):
        raise ValueError("作者账号仅支持英文字母、数字、下划线和短横线")
    if offline:
        return _offline(query, author, kind, page)
    slug = "" if author else topic_slug(query)
    key = json.dumps([slug, query if author else "", author, kind, page])
    with _database() as db:
        hit = db.execute("SELECT * FROM listings WHERE key=?", (key,)).fetchone()
        if hit and _clock() - hit["fetched"] < LIST_TTL:
            result = json.loads(hit["payload"])
            # A listing can outlive a catalog row evicted by other search pages.
            # Restore selectable IDs, without replacing metadata from a newer fetch.
            for item in result["items"]:
                db.execute("""INSERT INTO scripts(id,payload,seen) VALUES(?,?,?)
                    ON CONFLICT(id) DO UPDATE SET seen=excluded.seen""",
                           (item["id"], json.dumps(item, ensure_ascii=False), _clock()))
            db.execute("DELETE FROM scripts WHERE id IN (SELECT id FROM scripts ORDER BY seen DESC LIMIT -1 OFFSET 200)")
            return {**result, "cached": True}
    if author:
        payload = _json("https://www.tradingview.com/api/v1/scripts/",
                        {"by": author, "q": query, "page": page, "per_page": 24})
        raw_items, has_more = payload.get("results"), bool(payload.get("next"))
    else:
        path = "/scripts/" + (slug + "/" if slug else "") + (f"page-{page}/" if page > 1 else "")
        types = {"indicator": "indicators", "strategy": "strategies", "library": "libraries", "all": "all"}
        payload = _json("https://www.tradingview.com" + path,
                        {"component-data-only": "1", "script_access": "open", "script_type": types[kind]})
        try:
            feed = payload["data"]["ideas"]["data"]
            raw_items, has_more = feed["items"], bool(feed.get("next"))
        except (KeyError, TypeError):
            raise LibraryError("公开主题列表格式发生变化") from None
    if not isinstance(raw_items, list):
        raise LibraryError("公开脚本列表格式发生变化")
    unique = {}
    for raw in raw_items[:100]:
        item = normalize_item(raw)
        if item and (kind == "all" or item["kind"] == kind):
            item["topic"] = slug
            item["topic_name"] = next((topic["name"] for topic in TOPICS if topic["id"] == slug), "")
            unique[item["id"]] = item
    items = list(unique.values())[:24]
    now = _clock()
    with _database() as db:
        for item in items:
            db.execute("""INSERT INTO scripts(id,payload,seen) VALUES(?,?,?)
                ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,seen=excluded.seen""",
                       (item["id"], json.dumps(item, ensure_ascii=False), now))
        db.execute("DELETE FROM scripts WHERE id IN (SELECT id FROM scripts ORDER BY seen DESC LIMIT -1 OFFSET 200)")
        result = {"items": items, "page": page, "has_more": has_more and page < 20,
                  "cached": False, "offline": False, "fetched_at_ms": int(now * 1000),
                  "topic": slug, "note": "在线按主题发现；并非任意全文搜索。仅保留公开源码脚本。"}
        db.execute("INSERT OR REPLACE INTO listings VALUES(?,?,?)", (key, json.dumps(result), now))
        db.execute("DELETE FROM listings WHERE key IN (SELECT key FROM listings ORDER BY fetched DESC LIMIT -1 OFFSET 20)")
    return result


def _offline(query, author, kind, page):
    with _database() as db:
        rows = db.execute("SELECT payload,source,source_at,source_version FROM scripts ORDER BY seen DESC LIMIT 200").fetchall()
    items = []
    for row in rows:
        item = json.loads(row["payload"])
        if author and item["author"].lower() != author.lower():
            continue
        if kind != "all" and item["kind"] != kind:
            continue
        text = " ".join([item["name"], item["author"], item["description"],
                         item.get("topic", ""), item.get("topic_name", ""), row["source"] or ""]).lower()
        if query.lower() not in text:
            continue
        item["source_cached"] = row["source"] is not None
        item["cached_version"] = row["source_version"]
        items.append(item)
    start = (page - 1) * 24
    return {"items": items[start:start + 24], "page": page, "has_more": len(items) > start + 24,
            "cached": True, "offline": True, "fetched_at_ms": None, "topic": "",
            "note": "仅检索曾查询的脚本和按需保存的源码；离线模式不访问网络。"}


def source(script_id: str, version: str = "last", offline: bool = False) -> dict:
    if not ID_PATTERN.fullmatch(script_id) or not re.fullmatch(r"last|[0-9]{1,6}(?:\.[0-9]{1,6})?", version):
        raise ValueError("公开脚本 ID 或版本无效")
    with _database() as db:
        row = db.execute("SELECT * FROM scripts WHERE id=?", (script_id,)).fetchone()
    if not row:
        raise ValueError("请先从公开搜索或离线库选择脚本")
    def matches(resolved):
        # An integer addresses a major revision; a decimal addresses that exact revision.
        return version == "last" or version == resolved or ('.' not in version and version == resolved.split('.')[0])

    hit = row["source"] is not None and matches(row["source_version"] or '')
    if hit and (offline or version != "last" or _clock() - row["source_at"] < SOURCE_TTL):
        return _source_result(row, True)
    if offline:
        raise LibraryError("该脚本的此版本尚未缓存；切到在线发现后按需读取")
    payload = _json("https://pine-facade.tradingview.com/pine-facade/get/"
                    + quote(script_id, safe="") + "/" + quote(version, safe=""), {"no_4xx": "true"})
    text = payload.get("source")
    if (payload.get("success") is False or payload.get("scriptAccess") not in (None, 1, "open_no_auth", "open")
            or not isinstance(text, str) or not text.strip()):
        raise LibraryError("此脚本未提供公开源码；不会尝试读取受保护内容")
    size = len(text.encode("utf-8"))
    if size > MAX_SOURCE_BYTES:
        raise LibraryError("源码超过 256 KiB，未保存；请在原始脚本页查看")
    resolved = str(payload.get("version", ""))
    if not re.fullmatch(r"[0-9]{1,6}(?:\.[0-9]{1,6})?", resolved):
        raise LibraryError("源码未标注有效版本，未保存")
    if not matches(resolved):
        raise LibraryError("返回的源码版本与所选版本不符，未保存")
    with _database() as db:
        # A concurrent listing may have evicted an old catalog row during the HTTP call.
        db.execute("""INSERT INTO scripts(id,payload,seen,source,source_version,source_at,source_bytes)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET seen=excluded.seen,
            source=excluded.source,source_version=excluded.source_version,
            source_at=excluded.source_at,source_bytes=excluded.source_bytes""",
                   (script_id, row["payload"], _clock(), text, resolved, _clock(), size))
        old = db.execute("SELECT id,source_bytes FROM scripts WHERE source IS NOT NULL ORDER BY (id=?) DESC,source_at DESC", (script_id,)).fetchall()
        total = 0
        for index, entry in enumerate(old):
            total += entry["source_bytes"]
            if index >= MAX_SOURCES or total > MAX_TOTAL_SOURCE_BYTES:
                db.execute("UPDATE scripts SET source=NULL,source_version=NULL,source_at=NULL,source_bytes=0 WHERE id=?", (entry["id"],))
        db.execute("DELETE FROM scripts WHERE id IN (SELECT id FROM scripts ORDER BY (id=?) DESC,seen DESC LIMIT -1 OFFSET 200)", (script_id,))
        row = db.execute("SELECT * FROM scripts WHERE id=?", (script_id,)).fetchone()
    return _source_result(row, False)


def _source_result(row, cached):
    text = row["source"]
    version = re.search(r"(?m)^\s*//\s*@version\s*=\s*(\d+)", text)
    return {"id": row["id"], "name": json.loads(row["payload"])["name"],
            "version": row["source_version"], "pine_version": int(version[1]) if version else None,
            "source": text, "bytes": row["source_bytes"], "cached": cached,
            "fetched_at_ms": int(row["source_at"] * 1000)}
