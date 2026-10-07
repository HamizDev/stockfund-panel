import json

import pytest

from app.services.news_direction import direction_key, parse_directions

ARTICLE = {"id": "a" * 24, "title": "公司公告未来三年不减持", "summary": "股东承诺未来三年不减持股份。",
           "published_at": "2026-10-07T10:00:00+08:00"}
ROW = {"article_id": ARTICLE["id"], "direction": "positive", "scope": "company", "reason": "承诺不减持",
       "evidence": ["未来三年不减持"]}


def encode(rows):
    return json.dumps({"items": rows}, ensure_ascii=False)


def test_exact_evidence_and_json_fence_accepted():
    assert parse_directions(encode([ROW]), [ARTICLE]) == [ROW]
    assert parse_directions("```json\n" + encode([ROW]) + "\n```", [ARTICLE]) == [ROW]


@pytest.mark.parametrize("changes", [
    {"evidence": ["股价将上涨"]}, {"evidence": []}, {"evidence": [" "]}, {"scope": "unclear"},
    {"reason": " "}, {"article_id": "b" * 24}, {"direction": "buy"}, {"extra": "ignored"},
])
def test_invalid_or_invented_evidence_is_rejected(changes):
    with pytest.raises(ValueError):
        parse_directions(encode([{**ROW, **changes}]), [ARTICLE])


@pytest.mark.parametrize("text", [encode([]), encode([ROW, ROW]), encode([ROW]) + " trailing", "[]",
                                  '{"items":[],"other":true}'])
def test_all_articles_exactly_once_and_no_trailing_text(text):
    with pytest.raises(ValueError):
        parse_directions(text, [ARTICLE])


def test_uncertain_may_have_no_evidence_and_content_controls_identity():
    row = {**ROW, "direction": "uncertain", "scope": "unclear", "evidence": []}
    assert parse_directions(encode([row]), [ARTICLE]) == [row]
    assert direction_key(ARTICLE, "high") == direction_key({**ARTICLE, "associations": ["changed"]}, "high")
    assert direction_key(ARTICLE, "high") != direction_key({**ARTICLE, "summary": "改为减持"}, "high")
    assert direction_key(ARTICLE, "high") != direction_key(ARTICLE, "max")
