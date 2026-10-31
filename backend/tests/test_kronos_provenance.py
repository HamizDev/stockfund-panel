"""Bounded, read-only provenance checks; these tests never load a model runtime."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.custom.kronos import provenance

MODEL_HASHES = {
    "small": "a" * 64,
    "base": "b" * 64,
    "tokenizer": "c" * 64,
}


def _runner_source(model_hashes: dict[str, str] | None = None) -> str:
    return f"MODEL_HASHES = {(model_hashes or MODEL_HASHES)!r}\n"


def _write_model_config(runtime: Path, name: str, value: dict) -> Path:
    path = runtime / "models" / name / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    return path


@pytest.fixture
def panel_code(tmp_path, monkeypatch):
    module = tmp_path / "panel" / "backend" / "app" / "custom" / "kronos" / "provenance.py"
    module.parent.mkdir(parents=True)
    module.write_text("# test module anchor\n", encoding="utf-8")
    (module.parent / "service.py").write_text("SERVICE_VERSION = 1\n", encoding="utf-8")
    (module.parent / "data.py").write_text("DATA_VERSION = 1\n", encoding="utf-8")
    monkeypatch.setattr(provenance, "__file__", str(module))
    return module.parent


@pytest.fixture
def runtime(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "runner.py").write_text(_runner_source(), encoding="utf-8")
    return root


def test_runtime_profile_parses_hashes_without_executing_runner(runtime, panel_code):
    sentinel = runtime / "must-not-execute"
    runner = runtime / "runner.py"
    runner_bytes = (
        _runner_source() + "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).touch()\n"
    ).encode("utf-8")
    runner.write_bytes(runner_bytes)

    profile = provenance.runtime_profile(runtime)

    assert profile["model_hashes"] == MODEL_HASHES
    assert profile["runner_sha256"] == hashlib.sha256(runner_bytes).hexdigest()
    assert len(profile["implementation_sha256"]) == 64
    assert not sentinel.exists()


def test_runtime_profile_allows_missing_optional_model_source(runtime, panel_code):
    profile = provenance.runtime_profile(runtime)

    assert profile["model_hashes"] == MODEL_HASHES
    assert not (runtime / "source" / "model").exists()


def test_base_only_runtime_does_not_require_or_fingerprint_retired_small(runtime, panel_code):
    hashes = {name: MODEL_HASHES[name] for name in ('base', 'tokenizer')}
    (runtime / 'runner.py').write_text(_runner_source(hashes), encoding='utf-8')
    for name in hashes:
        _write_model_config(runtime, name, {'model': name, 'version': 1})
    initial = provenance.runtime_profile(runtime)
    assert initial['model_hashes'] == hashes
    assert not (runtime / 'models' / 'small').exists()
    _write_model_config(runtime, 'small', {'model': 'small', 'version': 99})
    assert provenance.runtime_profile(runtime) == initial
    _write_model_config(runtime, 'base', {'model': 'base', 'version': 2})
    assert provenance.runtime_profile(runtime)['implementation_sha256'] != initial['implementation_sha256']


def test_missing_and_new_model_configs_change_fingerprint(runtime, panel_code):
    missing = provenance.runtime_profile(runtime)
    for name in ("small", "base", "tokenizer"):
        _write_model_config(runtime, name, {"model": name, "version": 1})
    present = provenance.runtime_profile(runtime)
    _write_model_config(runtime, "small", {"model": "small", "version": 2})
    changed = provenance.runtime_profile(runtime)
    (runtime / "models" / "small" / "config.json").unlink()
    missing_again = provenance.runtime_profile(runtime)

    assert present["implementation_sha256"] != missing["implementation_sha256"]
    assert changed["implementation_sha256"] != present["implementation_sha256"]
    assert missing_again["implementation_sha256"] != changed["implementation_sha256"]


@pytest.mark.parametrize("name", ("small", "base", "tokenizer"))
def test_each_model_config_is_fingerprinted(runtime, panel_code, name):
    path = _write_model_config(runtime, name, {"model": name, "version": 1})
    initial = provenance.runtime_profile(runtime)
    path.write_text(json.dumps({"model": name, "version": 2}), encoding="utf-8")
    modified = provenance.runtime_profile(runtime)

    assert modified["implementation_sha256"] != initial["implementation_sha256"]


def test_runtime_source_content_and_relative_path_change_fingerprint(runtime, panel_code):
    model_dir = runtime / "source" / "model"
    model_dir.mkdir(parents=True)
    source = model_dir / "encoder.py"
    source.write_text("ENCODER_VERSION = 1\n", encoding="utf-8")

    initial = provenance.runtime_profile(runtime)
    unchanged = provenance.runtime_profile(runtime)
    source.write_text("ENCODER_VERSION = 2\n", encoding="utf-8")
    changed = provenance.runtime_profile(runtime)
    source.rename(model_dir / "renamed.py")
    renamed = provenance.runtime_profile(runtime)

    assert initial == unchanged
    assert changed["runner_sha256"] == initial["runner_sha256"]
    assert changed["implementation_sha256"] != initial["implementation_sha256"]
    assert renamed["implementation_sha256"] != changed["implementation_sha256"]


def test_panel_service_and_data_content_change_fingerprint(runtime, panel_code):
    service_file = panel_code / "service.py"
    data_file = panel_code / "data.py"
    initial = provenance.runtime_profile(runtime)

    service_file.write_text("SERVICE_VERSION = 2\n", encoding="utf-8")
    changed_service = provenance.runtime_profile(runtime)
    data_file.write_text("DATA_VERSION = 2\n", encoding="utf-8")
    changed_data = provenance.runtime_profile(runtime)

    assert changed_service["implementation_sha256"] != initial["implementation_sha256"]
    assert changed_data["implementation_sha256"] != changed_service["implementation_sha256"]


@pytest.mark.parametrize(
    "runner_source",
    [
        "MODEL_HASHES = __import__('pathlib').Path('must-not-execute').touch()\n",
        "MODEL_HASHES = {**{'small': '" + "a" * 64 + "'}}\n",
        "MODEL_HASHES = {'small': '" + "a" * 64 + "', 'base': '" + "b" * 64 + "'}\n",
        "MODEL_HASHES = {'small': '" + "a" * 64 + "', 'base': '" + "b" * 64 + "', 'tokenizer': '" + "c" * 64 + "', 'extra': '" + "d" * 64 + "'}\n",
        "MODEL_HASHES = {'small': '" + "a" * 64 + "', 'base': '" + "b" * 64 + "', 'tokenizer': 'short'}\n",
        "MODEL_HASHES = {'small': '" + "a" * 64 + "', 'small': '" + "b" * 64 + "', 'base': '" + "b" * 64 + "', 'tokenizer': '" + "c" * 64 + "'}\n",
        "if True:\n    MODEL_HASHES = " + repr(MODEL_HASHES) + "\n",
        "MODEL_HASHES = {\n",
    ],
)
def test_runner_ast_is_fail_closed(runtime, panel_code, runner_source):
    marker = runtime / "must-not-execute"
    (runtime / "runner.py").write_text(
        runner_source.replace("must-not-execute", str(marker)),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"MODEL_HASHES|runner.py"):
        provenance.runtime_profile(runtime)
    assert not marker.exists()


def test_model_python_file_size_limit_includes_exact_boundary(runtime, panel_code):
    model_dir = runtime / "source" / "model"
    model_dir.mkdir(parents=True)
    source = model_dir / "boundary.py"
    source.write_bytes(b"#" + b"x" * (provenance._MAX_CODE_BYTES - 1))

    assert provenance.runtime_profile(runtime)["implementation_sha256"]

    source.write_bytes(b"#" + b"x" * provenance._MAX_CODE_BYTES)
    with pytest.raises(ValueError, match="1 MiB"):
        provenance.runtime_profile(runtime)


def test_model_python_file_count_limit(runtime, panel_code):
    model_dir = runtime / "source" / "model"
    model_dir.mkdir(parents=True)
    for index in range(provenance._MAX_MODEL_PY_FILES):
        (model_dir / f"module_{index:02}.py").write_text(f"VALUE = {index}\n", encoding="utf-8")

    assert provenance.runtime_profile(runtime)["implementation_sha256"]

    (model_dir / "module_32.py").write_text("VALUE = 32\n", encoding="utf-8")
    with pytest.raises(ValueError, match="32"):
        provenance.runtime_profile(runtime)


def test_model_config_size_limit_includes_exact_boundary(runtime, panel_code):
    config = runtime / "models" / "small" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_bytes(b"{}" + b" " * (provenance._MAX_CODE_BYTES - 2))

    assert provenance.runtime_profile(runtime)["implementation_sha256"]

    config.write_bytes(b"{}" + b" " * (provenance._MAX_CODE_BYTES - 1))
    with pytest.raises(ValueError, match="1 MiB"):
        provenance.runtime_profile(runtime)


def test_model_source_symlink_is_rejected(runtime, panel_code, monkeypatch):
    model_dir = runtime / "source" / "model"
    model_dir.mkdir(parents=True)
    link = model_dir / "linked.py"
    link.write_text("PRIVATE = True\n", encoding="utf-8")
    original_is_link = provenance._is_link

    def mark_link(path):
        return Path(path) == link or original_is_link(path)

    monkeypatch.setattr(provenance, "_is_link", mark_link)

    with pytest.raises(ValueError, match="符号链接"):
        provenance.runtime_profile(runtime)


def test_model_source_resolving_outside_runtime_is_rejected(runtime, panel_code, tmp_path, monkeypatch):
    model_dir = runtime / "source" / "model"
    model_dir.mkdir(parents=True)
    escaped = model_dir / "escaped.py"
    escaped.write_text("VALUE = 1\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("VALUE = 2\n", encoding="utf-8")
    original_resolve = Path.resolve

    def resolve_outside(path, *args, **kwargs):
        if path == escaped:
            return outside.resolve()
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_outside)

    with pytest.raises(ValueError, match="超出允许目录"):
        provenance.runtime_profile(runtime)


def test_model_config_symlink_is_rejected(runtime, panel_code, monkeypatch):
    config = _write_model_config(runtime, "small", {"model": "small"})
    original_is_link = provenance._is_link

    def mark_link(path):
        return Path(path) == config or original_is_link(path)

    monkeypatch.setattr(provenance, "_is_link", mark_link)

    with pytest.raises(ValueError, match="符号链接"):
        provenance.runtime_profile(runtime)


def test_model_directory_symlink_is_rejected(runtime, panel_code, monkeypatch):
    _write_model_config(runtime, "small", {"model": "small"})
    model_dir = runtime / "models" / "small"
    original_is_link = provenance._is_link

    def mark_link(path):
        return Path(path) == model_dir or original_is_link(path)

    monkeypatch.setattr(provenance, "_is_link", mark_link)

    with pytest.raises(ValueError, match="符号链接"):
        provenance.runtime_profile(runtime)


def test_malformed_model_config_is_rejected(runtime, panel_code):
    config = runtime / "models" / "small" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text("not json", encoding="utf-8")

    with pytest.raises(ValueError, match="有效 JSON"):
        provenance.runtime_profile(runtime)


def test_model_config_resolving_outside_runtime_is_rejected(runtime, panel_code, tmp_path, monkeypatch):
    config = _write_model_config(runtime, "small", {"model": "small"})
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    original_resolve = Path.resolve

    def resolve_outside(path, *args, **kwargs):
        if path == config:
            return original_resolve(outside, *args, **kwargs)
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_outside)

    with pytest.raises(ValueError, match="超出允许目录"):
        provenance.runtime_profile(runtime)
