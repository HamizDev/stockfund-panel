"""Reproducible word-list screening. Scores are relevance, never probability.

View names follow the requested reference UI; these are our disclosed local
word lists, not an implementation or endorsement of anyone's investing method.
"""
# ruff: noqa: RUF001 -- Chinese labels and punctuation are intentional.
from __future__ import annotations

import hashlib
import re

from app.data_providers.news import SOURCE_LABELS, NewsRecord

RULES_VERSION = "1"
# Order matters: explicit risk hits take precedence over thematic keywords.
SERENITY = (
    ("risk", "风险·负面", 35, ("立案", "处罚", "退市", "违约", "亏损", "减持", "诉讼", "召回", "未达", "禁止", "失信", "违规")),
    ("upstream", "卡脖子·上游", 40, ("卡脖子", "先进封装", "光刻", "芯片设备", "国产替代", "关键材料", "半导体材料")),
    ("revalue", "估值重估", 30, ("估值重估", "资产注入", "分拆上市", "业绩超预期", "扭亏", "提价")),
    ("institution", "机构行为", 25, ("机构增持", "机构调研", "基金增持", "社保基金", "北向资金", "险资", "被机构加仓")),
    ("trend", "产业趋势", 20, ("人工智能", "新能源", "机器人", "创新药", "算力", "低空经济", "产业升级")),
    ("capital", "资本运作", 15, ("并购", "重组", "回购", "增持", "定增", "股权转让")),
)
WOJIANSHAN = (
    ("risk", "排雷·规避", 40, SERENITY[0][3]),
    ("policy", "政策·国家级", 35, ("国务院", "央行", "财政部", "国家发改委", "证监会", "全国人大")),
    ("local_policy", "政策·行业地方", 25, ("地方政府", "省政府", "市政府", "工信部", "行业政策", "行动方案")),
    ("event", "事件·重大", 30, ("重大突破", "取得突破", "战略合作", "重大合同", "获批", "制裁", "关税")),
    ("capital", "资本运作", 20, SERENITY[5][3]),
    ("company", "公司·公告", 20, ("公告", "净利润", "营业收入", "业绩预告", "订单", "中标", "分红")),
    ("theme", "产业·题材", 15, SERENITY[3][3] + SERENITY[1][3]),
)
POSITIVE = ("增持", "回购", "中标", "获批", "突破", "扭亏", "增长", "利好", "上调", "降息")
NEGATIVE = ("减持", "亏损", "立案", "处罚", "退市", "违约", "下滑", "利空", "制裁", "下调", "未达", "禁止", "失信", "违规")


def _matches(text: str, word: str) -> bool:
    # Narrow exclusions for observed macro uses and explicit denials. This is
    # still word screening, not a semantic assertion about the whole article.
    patterns = {"回购": r"(?<!逆)回购(?!协议)", "中标": r"中标(?!利率)"}
    pattern = patterns.get(word, re.escape(word))
    for match in re.finditer(pattern, text):
        prefix = text[max(0, match.start() - 3):match.start()]
        if not prefix.endswith(("未被", "未受", "没有", "并未", "不涉及")):
            return True
    return False


def _classification(text: str, rules: tuple) -> dict:
    for category, label, weight, words in rules:
        matched = [word for word in words if _matches(text, word)]
        if matched:
            return {"category": category, "label": label, "score": min(100, weight + 3 * len(matched)), "matched": matched}
    return {"category": "other", "label": "其他", "score": 0, "matched": []}


def classify(text: str) -> dict:
    positive = [word for word in POSITIVE if _matches(text, word)]
    negative = [word for word in NEGATIVE if _matches(text, word)]
    direction = "mixed" if positive and negative else "positive" if positive else "negative" if negative else "neutral"
    return {"classifications": {"serenity": _classification(text, SERENITY),
                                "wojianshan": _classification(text, WOJIANSHAN)},
            "sentiment": {"direction": direction, "matched": positive + negative}}


def _canonical(title: str) -> str:
    title = re.sub(r"^财联社\d{1,2}月\d{1,2}日电[，,:：\s]*", "", title)
    return re.sub(r"[\s【】\[\]，。！？：；、,.!?;:'\"“”‘’]", "", title).casefold()


def _summary_key(text: str) -> str:
    # The CLS attribution may follow a bracketed headline in the body. Removing
    # that wrapper allows an otherwise identical EM excerpt to coalesce.
    return _canonical(re.sub(r"财联社\d{1,2}月\d{1,2}日电[，,:：\s]*", "", text))


def normalize_news(records: list[NewsRecord]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for record in records:
        # Avoid fuzzy merging of different updates. The same headline on another
        # date is a different article even when its text is identical.
        identity = record.published_at[:10] + ":" + _canonical(record.title)
        origin = {"source": record.source, "label": SOURCE_LABELS[record.source],
                  "url": record.url, "published_at": record.published_at}
        buckets = grouped.setdefault(identity, [])
        summary = _summary_key(record.summary)
        item = next((row for row in buckets if not summary or not row["summary"]
                     or summary.startswith(_summary_key(row["summary"]))
                     or _summary_key(row["summary"]).startswith(summary)), None)
        if item is None:
            item = {"title": record.title, "summary": record.summary,
                    "published_at": record.published_at, "origins": [], "associations": []}
            buckets.append(item)
        if origin not in item["origins"]:
            item["origins"].append(origin)
        # Preserve the richer text and the earliest report time; each source's
        # publication time remains visible in origins.
        if len(record.summary) > len(item["summary"]):
            item["summary"] = record.summary
        item["published_at"] = min(item["published_at"], record.published_at)
    items = []
    for identity, buckets in grouped.items():
        for item in buckets:
            evidence_id = identity if len(buckets) == 1 else identity + ":" + _canonical(item["summary"])
            item["id"] = hashlib.sha256(evidence_id.encode()).hexdigest()[:24]
            item.update(classify(item["title"] + " " + item["summary"]))
            items.append(item)
    return sorted(items, key=lambda row: (row["published_at"], row["id"]), reverse=True)[:240]


def associate(text: str, instruments: list[dict]) -> list[dict]:
    """Direct exact mentions only. No partial-name/industry implication guessing.

    Short Chinese names (<4 characters) require a qualified stock code. This
    intentionally prefers missing an association over linking unrelated firms.
    """
    found = []
    seen = set()
    qualified_symbols = set(re.findall(r"(?<![A-Za-z0-9])\d{6}\.(?:SH|SZ|BJ)(?![A-Za-z0-9])", text.upper()))
    stock_codes = set(re.findall(r"(?:股票|证券)代码\s*[:：]?\s*(?<![A-Za-z0-9])(\d{6})(?![A-Za-z0-9])", text))
    etf_codes = set(re.findall(r"ETF代码\s*[:：]?\s*(?<![A-Za-z0-9])(\d{6})(?![A-Za-z0-9])", text, re.IGNORECASE))
    for item in instruments:
        symbol = item.get("symbol", "")
        name = item.get("name", "")
        asset = item.get("asset_type")
        if asset not in {"stock", "etf"} or not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", symbol) or symbol in seen:
            continue
        code = symbol[:6]
        qualified = symbol in qualified_symbols or code in (stock_codes if asset == "stock" else etf_codes)
        full_name = (isinstance(name, str) and len(name) >= 4 and name in text
                     and not re.search(re.escape(name) + r"(?:业|行业|板块)", text))
        if qualified or full_name:
            found.append({"symbol": symbol, "name": name or symbol, "asset_type": asset,
                          "basis": f"正文明确出现{'代码 ' + symbol if qualified else '完整名称 ' + name}", "direct": True})
            seen.add(symbol)
    return found[:12]
