from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.strategy import (
    StrategyCodeSaveRequest,
    StrategyCodeValidateRequest,
    _prepare_strategy_code,
    _research_draft_code_matches,
    _save_strategy_code,
    _set_meta_bool_field,
    publish_ai_strategy,
    validate_strategy_code,
)
from app.strategy.engine import StrategyEngine


def _code(strategy_id: str, name: str = "测试策略") -> str:
    return f'''"""测试策略"""
import polars as pl

META = {{
    "id": "{strategy_id}",
    "name": "{name}",
    "description": "测试描述",
    "tags": ["测试"],
    "params": [],
    "scoring": {{}},
}}

ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.05
MAX_HOLD_DAYS = 20

RULES = """
1. 测试规则一
2. 测试规则二
3. 测试规则三
"""

def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    return pl.lit(True)
'''


def _request(tmp_path):
    ai_dir = tmp_path / "strategies" / "ai"
    custom_dir = tmp_path / "strategies" / "custom"
    engine = StrategyEngine(strategy_dirs=[custom_dir, ai_dir])
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo, strategy_engine=engine)))


def test_prepare_strategy_code_rejects_forbidden_import():
    req = StrategyCodeValidateRequest(
        strategy_id="custom_bad",
        code='''import os\nMETA = {"id": "custom_bad"}\n''',
    )

    with pytest.raises(ValueError, match="禁止 import os"):
        _prepare_strategy_code(req)


def test_prepare_strategy_code_rejects_unknown_scoring_field():
    req = StrategyCodeValidateRequest(
        strategy_id="custom_bad_score",
        code=_code("custom_bad_score").replace(
            '"scoring": {},',
            '"scoring": {"volume_surge": 1.0},',
        ),
    )

    with pytest.raises(ValueError, match="volume_surge"):
        _prepare_strategy_code(req)


def test_save_strategy_code_creates_ai_strategy_in_ai_dir(tmp_path):
    request = _request(tmp_path)
    req = StrategyCodeSaveRequest(
        strategy_id="ai_saved",
        target_source="ai",
        mode="create",
        code=_code("wrong"),
        name="AI 策略",
    )

    result = _save_strategy_code(req, request)

    assert result["ok"] is True
    assert result["source"] == "ai"
    assert (tmp_path / "strategies" / "ai" / "ai_saved.py").exists()
    loaded = request.app.state.strategy_engine.get("ai_saved")
    assert loaded.source == "ai"
    assert loaded.file_path == tmp_path / "strategies" / "ai" / "ai_saved.py"


def test_save_strategy_code_creates_custom_strategy_in_custom_dir(tmp_path):
    request = _request(tmp_path)
    req = StrategyCodeSaveRequest(
        strategy_id="custom_saved",
        target_source="custom",
        mode="create",
        code=_code("wrong"),
        name="自定义策略",
    )

    result = _save_strategy_code(req, request)

    assert result["ok"] is True
    assert result["source"] == "custom"
    assert (tmp_path / "strategies" / "custom" / "custom_saved.py").exists()
    loaded = request.app.state.strategy_engine.get("custom_saved")
    assert loaded.source == "custom"
    assert loaded.file_path == tmp_path / "strategies" / "custom" / "custom_saved.py"


def test_save_strategy_code_updates_existing_source_file(tmp_path):
    request = _request(tmp_path)
    create = StrategyCodeSaveRequest(
        strategy_id="custom_update",
        target_source="custom",
        mode="create",
        code=_code("custom_update", "旧名称"),
    )
    _save_strategy_code(create, request)

    update = StrategyCodeSaveRequest(
        strategy_id="custom_update",
        target_source="ai",
        mode="update",
        code=_code("custom_update", "新名称"),
    )
    result = _save_strategy_code(update, request)

    assert result["source"] == "custom"
    custom_path = tmp_path / "strategies" / "custom" / "custom_update.py"
    assert custom_path.exists()
    assert not (tmp_path / "strategies" / "ai" / "custom_update.py").exists()
    assert '"name": "新名称"' in custom_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("submitted_flag", [None, False, True])
@pytest.mark.parametrize("published", [False, True])
def test_update_ai_strategy_preserves_existing_publication_state(tmp_path, submitted_flag, published):
    """保存代码不能发布研究草稿, 也不能把已发布策略悄悄变回草稿。"""
    request = _request(tmp_path)
    sid = "ai_update_publication"
    _save_strategy_code(StrategyCodeSaveRequest(
        strategy_id=sid, target_source="ai", mode="create", code=_code(sid),
    ), request)
    if published:
        publish_ai_strategy(sid, request)

    submitted_code = _code(sid, "修改后的草稿")
    if submitted_flag is not None:
        submitted_code = _set_meta_bool_field(submitted_code, "research_only", submitted_flag)
    result = _save_strategy_code(StrategyCodeSaveRequest(
        strategy_id=sid, target_source="ai", mode="update", code=submitted_code,
    ), request)

    expected_research_only = not published
    assert result["research_only"] is expected_research_only
    loaded = request.app.state.strategy_engine.get(sid)
    assert loaded.meta.get("research_only", False) is expected_research_only
    assert loaded.meta["name"] == "修改后的草稿"
    public_ids = {meta["id"] for meta in request.app.state.strategy_engine.list_strategies()}
    assert (sid in public_ids) is published


def test_create_ai_strategy_still_rejects_existing_id(tmp_path):
    request = _request(tmp_path)
    req = StrategyCodeSaveRequest(
        strategy_id="ai_duplicate", target_source="ai", mode="create", code=_code("ai_duplicate"),
    )
    _save_strategy_code(req, request)
    path = tmp_path / "strategies" / "ai" / "ai_duplicate.py"
    saved_code = path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="已存在"):
        _save_strategy_code(req, request)

    assert path.read_text(encoding="utf-8") == saved_code


def test_validate_recognizes_iterator_rewritten_draft_identity_without_writing(tmp_path):
    from app.strategy.ai_generator import AIStrategyGenerator
    from app.strategy.ai_iterator import AIStrategyIterator

    request = _request(tmp_path)
    raw_code = _code("ai_model_suggestion")
    actual_id = "ai_iterator_assigned"
    AIStrategyIterator._save_draft(
        request.app.state.strategy_engine, str(tmp_path), actual_id,
        raw_code, AIStrategyGenerator._extract_meta(raw_code),
    )
    path = tmp_path / "strategies/ai" / f"{actual_id}.py"
    before = path.read_bytes()

    result = validate_strategy_code(StrategyCodeValidateRequest(
        code=raw_code, strategy_id=actual_id,
    ), request)

    assert result["valid"] is True
    assert result.get("matches_existing_research_draft") is True
    assert result["meta"]["id"] == actual_id
    assert result["meta"]["research_only"] is True
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", ["logic", "signal", "metadata", "bool_as_int", "key_order", "published", "custom", "missing"])
def test_validate_does_not_bind_unrelated_existing_strategy(tmp_path, change):
    request = _request(tmp_path)
    actual_id = "custom_existing" if change == "custom" else "ai_existing_draft"
    original = _code(actual_id)
    if change == "bool_as_int":
        original = original.replace('"tags": ["测试"],', '"tags": ["测试"],\n    "enabled": True,')
    if change != "missing":
        _save_strategy_code(StrategyCodeSaveRequest(
            code=original, strategy_id=actual_id, mode="create",
            target_source="custom" if change == "custom" else "ai",
        ), request)
    if change == "published":
        publish_ai_strategy(actual_id, request)
    submitted = _code("ai_model_suggestion")
    if change == "logic":
        submitted = submitted.replace("pl.lit(True)", "pl.lit(False)")
    elif change == "signal":
        submitted = submitted.replace("STOP_LOSS = -0.05", "STOP_LOSS = -0.10")
    elif change == "metadata":
        submitted = submitted.replace('"params": []', '"params": [{"id": "period", "default": 5}]')
    elif change == "bool_as_int":
        submitted = submitted.replace('"tags": ["测试"],', '"tags": ["测试"],\n    "enabled": 1,')
    elif change == "key_order":
        submitted = submitted.replace('"params": [],\n    "scoring": {},', '"scoring": {},\n    "params": [],')
    result = validate_strategy_code(StrategyCodeValidateRequest(
        code=submitted, strategy_id=actual_id,
    ), request)
    assert result.get("matches_existing_research_draft") is False


@pytest.mark.parametrize("mode", ["create", "update"])
def test_save_rejects_duplicate_research_only_keys_without_writing(tmp_path, mode):
    request = _request(tmp_path)
    sid = "ai_duplicate_flag"
    path = tmp_path / "strategies/ai" / f"{sid}.py"
    if mode == "update":
        _save_strategy_code(StrategyCodeSaveRequest(
            code=_code(sid), strategy_id=sid, mode="create", target_source="ai",
        ), request)
    before = path.read_bytes() if path.exists() else None
    submitted = _code(sid).replace('"params": [],', '"params": [],\n    "research_only": True,\n    "research_only": False,')
    with pytest.raises(ValueError, match="唯一字符串键"):
        _save_strategy_code(StrategyCodeSaveRequest(
            code=submitted, strategy_id=sid, mode=mode, target_source="ai",
        ), request)
    assert (path.read_bytes() if path.exists() else None) == before


@pytest.mark.parametrize("flag", ["True", "False"])
def test_draft_comparison_checks_other_assignments_on_meta_line(tmp_path, flag):
    from app.strategy.ai_generator import AIStrategyGenerator

    sid = "ai_same_line"
    code = _code(sid)
    start = code.index("META =")
    end = code.index("\n\nENTRY_SIGNALS")
    meta = {**AIStrategyGenerator._extract_meta(code), "research_only": True}
    original = code[:start] + f"META = {meta!r}; FLAG = True" + code[end:]
    path = tmp_path / "same_line.py"
    path.write_text(original, encoding="utf-8")
    existing = SimpleNamespace(source="ai", meta=meta, file_path=path)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        strategy_engine=SimpleNamespace(get=lambda _: existing),
    )))
    submitted = original.replace("; FLAG = True", f"; FLAG = {flag}")
    assert _research_draft_code_matches(request, sid, submitted) is (flag == "True")


@pytest.mark.parametrize("literal", ["0", "None", "False"])
def test_meta_bool_setter_replaces_inline_value_without_duplicate_keys(literal):
    import ast

    code = f"META = {{'name': '中文名称', 'research_only': {literal}}}; FLAG = True\n"
    changed = _set_meta_bool_field(code, "research_only", True)
    meta_node = ast.parse(changed).body[0].value
    assert len(meta_node.keys) == 2
    assert ast.literal_eval(meta_node)["research_only"] is True
    assert "; FLAG = True" in changed


def test_save_strategy_code_rejects_undefined_custom_signal(tmp_path):
    """REQUIRED_FEATURES 引用未定义的自定义信号 → 拒绝保存并恢复文件。

    回归: 之前保存不校验, 运行期才抛 polars 缺列错 (500)。
    """
    request = _request(tmp_path)
    code = _code("custom_missing_sig") + (
        '\nREQUIRED_FEATURES = {"csg_oversold_macd_about_to_golden"}\n'
    )
    req = StrategyCodeSaveRequest(
        strategy_id="custom_missing_sig",
        target_source="custom",
        mode="create",
        code=code,
        name="引用不存在信号的策略",
    )

    with pytest.raises(ValueError, match="csg_oversold_macd_about_to_golden"):
        _save_strategy_code(req, request)

    # 校验失败不落盘
    assert not (tmp_path / "strategies" / "custom" / "custom_missing_sig.py").exists()


def test_save_strategy_code_ok_when_custom_signal_defined(tmp_path):
    """信号已定义时, 引用它的策略可以正常保存。"""
    from app.strategy import custom_signals

    custom_signals.save_one(tmp_path, {
        "id": "oversold_macd_about_to_golden",
        "name": "超跌接近金叉",
        "kind": "entry",
        "conditions": [
            {"left": "momentum_60d", "op": "<=", "right": "-0.30",
             "leftDays": 0, "rightDays": 0},
        ],
        "enabled": True,
    })
    request = _request(tmp_path)
    code = _code("custom_with_sig") + (
        '\nREQUIRED_FEATURES = {"csg_oversold_macd_about_to_golden"}\n'
    )
    req = StrategyCodeSaveRequest(
        strategy_id="custom_with_sig",
        target_source="custom",
        mode="create",
        code=code,
        name="引用已定义信号的策略",
    )

    result = _save_strategy_code(req, request)
    assert result["ok"] is True
    loaded = request.app.state.strategy_engine.get("custom_with_sig")
    assert "csg_oversold_macd_about_to_golden" in loaded.required_features
