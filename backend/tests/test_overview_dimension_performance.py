"""Read-path regression tests for shared, projected market overview dimensions."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import polars as pl

from app.services import market_overview_builder as overview
from app.services.ext_data import ExtConfigStore


def _repo(data_dir: Path):
    return SimpleNamespace(store=SimpleNamespace(data_dir=data_dir))


def _write_config(data_dir: Path, *, mode: str = "timeseries", symbol_map: dict | None = None) -> Path:
    config_dir = data_dir / "ext_data" / "concept_industry"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "id": "concept_industry",
        "label": "概念行业表",
        "mode": mode,
        "fields": [
            {"name": "所属概念", "dtype": "string", "label": "所属概念"},
            {"name": "所属行业", "dtype": "string", "label": "所属行业"},
        ],
    }
    if symbol_map is not None:
        config["symbol_map"] = symbol_map
    (config_dir / "config.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return config_dir


def _write_partition(
    data_dir: Path,
    day: str,
    concepts: list[str],
    industries: list[str],
    *,
    physical_dates: list[date] | None = None,
) -> Path:
    part = data_dir / "ext_data" / "concept_industry" / "timeseries" / f"date={day}" / "part.parquet"
    part.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "symbol": [f"{index + 1:06d}.SZ" for index in range(len(concepts))],
        "所属概念": concepts,
        "所属行业": industries,
        "unused_payload": [f"ignored-{day}-{index}" for index in range(len(concepts))],
    }
    if physical_dates is not None:
        payload["date"] = physical_dates
    pl.DataFrame(payload).write_parquet(part)
    return part


def _quote_rows(count: int) -> list[dict]:
    return [
        {"symbol": f"{index + 1:06d}.SZ", "name": f"股票{index + 1}", "change_pct": index / 100, "amount": index + 1}
        for index in range(count)
    ]


def test_both_dimensions_share_one_config_read_and_latest_projected_partition(tmp_path, monkeypatch):
    _write_config(tmp_path)
    _write_partition(tmp_path, "2026-10-26", ["旧概念"], ["一级-旧行业"])
    latest = _write_partition(
        tmp_path,
        "2026-10-27",
        ["新概念", "新概念"],
        ["一级-汽车", "一级-银行"],
    )

    config_reads = 0
    original_load_all = ExtConfigStore.load_all

    def counted_load_all(store):
        nonlocal config_reads
        config_reads += 1
        return original_load_all(store)

    monkeypatch.setattr(ExtConfigStore, "load_all", counted_load_all)
    parquet_reads: list[tuple[list[Path], list[str] | None]] = []
    original_read_parquet = pl.read_parquet

    def counted_read_parquet(source, *args, **kwargs):
        paths = source if isinstance(source, (list, tuple)) else [source]
        parquet_reads.append(([Path(path) for path in paths], kwargs.get("columns")))
        return original_read_parquet(source, *args, **kwargs)

    monkeypatch.setattr(overview.pl, "read_parquet", counted_read_parquet)
    ranks = overview._dimension_ranks(
        _quote_rows(2),
        _repo(tmp_path),
        {"concept": None, "industry": 2},
    )

    assert config_reads == 1
    assert len(parquet_reads) == 1
    paths, columns = parquet_reads[0]
    assert paths == [latest]
    assert columns is not None
    assert {"symbol", "所属概念", "所属行业", "date"} <= set(columns)
    assert "unused_payload" not in columns
    assert [item["name"] for item in ranks["concept"]["leading"]] == ["新概念"]
    assert {item["name"] for item in ranks["industry"]["leading"]} == {"汽车", "银行"}


def test_latest_empty_partition_falls_back_to_latest_partition_with_rows(tmp_path, monkeypatch):
    _write_config(tmp_path)
    old = _write_partition(tmp_path, "2026-10-26", ["旧概念"], ["一级-旧行业"])
    empty = tmp_path / "ext_data" / "concept_industry" / "timeseries" / "date=2026-10-27" / "part.parquet"
    empty.parent.mkdir(parents=True)
    pl.DataFrame(schema={
        "symbol": pl.String,
        "所属概念": pl.String,
        "所属行业": pl.String,
        "unused_payload": pl.String,
    }).write_parquet(empty)

    reads: list[list[Path]] = []
    original_read_parquet = pl.read_parquet

    def counted_read_parquet(source, *args, **kwargs):
        paths = source if isinstance(source, (list, tuple)) else [source]
        reads.append([Path(path) for path in paths])
        return original_read_parquet(source, *args, **kwargs)

    monkeypatch.setattr(overview.pl, "read_parquet", counted_read_parquet)
    rows = overview._read_ext_rows(tmp_path, ExtConfigStore(tmp_path).load_all()[0], "所属概念")

    assert rows == [{"所属概念": "旧概念", "symbol": "000001.SZ"}]
    assert reads[0] == [empty]
    assert reads[-1] == [old, empty]


def test_canonical_partition_date_matches_legacy_hive_read_with_physical_date(tmp_path):
    _write_config(tmp_path)
    old = _write_partition(
        tmp_path,
        "2026-10-26",
        ["旧概念"],
        ["一级-旧行业"],
        physical_dates=[date(2026, 10, 30)],
    )
    latest = _write_partition(
        tmp_path,
        "2026-10-27",
        ["新概念"],
        ["一级-汽车"],
        physical_dates=[date(2026, 10, 1)],
    )

    legacy = pl.read_parquet([old, latest], hive_partitioning=True)
    assert legacy.get_column("date").to_list() == [date(2026, 10, 26), date(2026, 10, 27)]
    legacy_latest = legacy.get_column("date").max()
    expected = legacy.filter(pl.col("date") == legacy_latest).select(["所属概念", "symbol"]).to_dicts()

    config = ExtConfigStore(tmp_path).load_all()[0]
    actual = overview._read_ext_rows(tmp_path, config, "所属概念")

    assert actual == expected == [{"所属概念": "新概念", "symbol": "000001.SZ"}]


def test_canonical_schema_mismatch_falls_back_to_legacy_multi_file_read(tmp_path, monkeypatch):
    _write_config(tmp_path)
    old = _write_partition(tmp_path, "2026-10-26", ["旧概念"], ["一级-旧行业"])
    latest = _write_partition(tmp_path, "2026-10-27", ["新概念"], ["一级-汽车"])
    pl.read_parquet(old).with_columns(pl.lit("only-old").alias("legacy_only")).write_parquet(old)

    reads: list[list[Path]] = []
    original_read_parquet = pl.read_parquet

    def counted_read_parquet(source, *args, **kwargs):
        paths = source if isinstance(source, (list, tuple)) else [source]
        reads.append([Path(path) for path in paths])
        return original_read_parquet(source, *args, **kwargs)

    monkeypatch.setattr(overview.pl, "read_parquet", counted_read_parquet)
    rows = overview._read_ext_rows(tmp_path, ExtConfigStore(tmp_path).load_all()[0], "所属概念")

    assert rows == []
    assert any(set(paths) == {old, latest} for paths in reads)


def test_mixed_timeseries_layout_uses_recursive_legacy_read(tmp_path, monkeypatch):
    config_dir = _write_config(tmp_path)
    canonical = _write_partition(tmp_path, "2026-10-27", ["规范分区"], ["一级-汽车"])
    legacy = config_dir / "timeseries" / "legacy" / "part.parquet"
    legacy.parent.mkdir(parents=True)
    pl.DataFrame({
        "symbol": ["000002.SZ"],
        "所属概念": ["旧路径"],
        "所属行业": ["一级-银行"],
        "date": [date(2026, 10, 30)],
    }).write_parquet(legacy)

    reads: list[list[Path]] = []
    original_read_parquet = pl.read_parquet

    def counted_read_parquet(source, *args, **kwargs):
        paths = source if isinstance(source, (list, tuple)) else [source]
        reads.append([Path(path) for path in paths])
        return original_read_parquet(source, *args, **kwargs)

    monkeypatch.setattr(overview.pl, "read_parquet", counted_read_parquet)
    rows = overview._read_ext_rows(tmp_path, ExtConfigStore(tmp_path).load_all()[0], "所属概念")

    assert rows == []  # Polars rejects the mixed Hive/physical date shape, as before.
    assert any(set(paths) == {canonical, legacy} for paths in reads)


def test_legacy_timeseries_without_date_keeps_recursive_compatibility(tmp_path):
    config_dir = _write_config(tmp_path)
    legacy = config_dir / "timeseries" / "legacy" / "part.parquet"
    legacy.parent.mkdir(parents=True)
    pl.DataFrame({
        "symbol": ["000001.SZ"],
        "所属概念": ["无日期旧schema"],
        "所属行业": ["一级-银行"],
    }).write_parquet(legacy)

    rows = overview._read_ext_rows(tmp_path, ExtConfigStore(tmp_path).load_all()[0], "所属概念")

    assert rows == [{"所属概念": "无日期旧schema", "symbol": "000001.SZ"}]


def test_snapshot_with_legacy_mapped_symbol_schema_still_projects_required_columns(tmp_path, monkeypatch):
    config_dir = _write_config(
        tmp_path,
        mode="snapshot",
        symbol_map={"type": "mapped", "col": "证券代码"},
    )
    part = config_dir / "part.parquet"
    pl.DataFrame({
        "证券代码": ["000001"],
        "所属概念": ["快照概念"],
        "unused_payload": ["ignored"],
    }).write_parquet(part)

    calls = []
    original_read_parquet = pl.read_parquet

    def counted_read_parquet(source, *args, **kwargs):
        calls.append(kwargs.get("columns"))
        return original_read_parquet(source, *args, **kwargs)

    monkeypatch.setattr(overview.pl, "read_parquet", counted_read_parquet)
    rank = overview._dimension_rank(_quote_rows(1), _repo(tmp_path), "concept")

    assert rank["leading"][0]["name"] == "快照概念"
    assert calls == [["所属概念", "证券代码"]]
