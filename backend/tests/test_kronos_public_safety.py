"""Portable research boundaries; no model, credentials or market calls."""
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.custom.kronos import evaluation, paths, routes, service
from app.custom.kronos.comparison_store import ResearchStore
from app.services import fs_utils


def test_research_store_stops_directory_iteration_at_limit_plus_one(tmp_path, monkeypatch):
    store = ResearchStore(tmp_path, 'comparisons', limit=2)
    used = []
    original = Path.glob

    def entries(folder, pattern):
        if folder != store.folder:
            return original(folder, pattern)
        def generate():
            for number in range(1000):
                used.append(number)
                yield folder / f'{number:032x}.json'
        return generate()

    monkeypatch.setattr(Path, 'glob', entries)
    with pytest.raises(ValueError):
        store.identities()
    assert used == [0, 1, 2]


def test_reader_bounds_a_file_that_grows_after_stat(tmp_path, monkeypatch):
    path = tmp_path / 'record.json'
    path.write_bytes(b'{}')
    original = Path.open
    sizes = []

    class Reader:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def fileno(self):
            return self.stream.fileno()
        def read(self, size=-1):
            sizes.append(size)
            return self.stream.read(size)

    def opening(target, *args, **kwargs):
        if target == path and args == ('rb',):
            with original(target, 'wb') as stream:
                stream.write(b'x' * 100)
        stream = original(target, *args, **kwargs)
        return Reader(stream) if target == path and args == ('rb',) else stream

    monkeypatch.setattr(Path, 'open', opening)
    with pytest.raises(ValueError):
        paths.read_record_bytes(path, 4)
    assert sizes == [5]


def test_exclusive_atomic_write_preserves_a_preexisting_temp_file(tmp_path, monkeypatch):
    destination = tmp_path / 'record.json'
    temporary = tmp_path / ('.record.json.' + '1' * 32 + '.tmp')
    temporary.write_text('keep', encoding='utf-8')
    monkeypatch.setattr(fs_utils.uuid, 'uuid4', lambda: SimpleNamespace(hex='1' * 32))
    with pytest.raises(FileExistsError):
        fs_utils.atomic_write_text_exclusive(destination, 'new')
    assert temporary.read_text(encoding='utf-8') == 'keep'
    assert not destination.exists()


def test_job_finish_never_writes_the_predictable_legacy_temp_name(tmp_path):
    folder = tmp_path / 'results'
    folder.mkdir()
    identity = 'a' * 32
    old_temp = folder / (identity + '.tmp')
    old_temp.write_text('keep', encoding='utf-8')
    manager = service.JobManager(tmp_path)
    job = {'id': identity, 'state': 'complete', 'result': {'record': 'original'}}
    manager._finish(job)
    assert old_temp.read_text(encoding='utf-8') == 'keep'
    assert json.loads((folder / (identity + '.json')).read_text(encoding='utf-8')) == job


def test_late_minute_prediction_is_not_a_before_the_fact_score():
    job = {'id': 'a' * 32, 'state': 'complete',
           'request': {'symbol': '510300.SH', 'period': '1m'},
           'result': {'symbol': '510300.SH', 'asset_type': 'etf', 'period': '1m',
                      'price_basis': '原始价格（未复权）', 'input_as_of': '2026-10-26T14:15',  # noqa: RUF001
                      'requested_at': '2026-10-26T14:16:20+08:00',
                      'completed_at': '2026-10-26T14:19:00+08:00',
                      'history': [{'time': '2026-10-26T14:15', 'close': 100.}],
                      'forecast': [{'time': '2026-10-26T14:16', 'close': 101.}]}}
    original = json.dumps(job, ensure_ascii=False)
    with pytest.raises(ValueError, match='晚于'):
        evaluation.payload(job)
    assert json.dumps(job, ensure_ascii=False) == original


def test_public_routes_have_no_personal_collection_or_trading_endpoints():
    names = [route.path for route in routes.build_router().routes]
    assert '/api/custom/kronos/comparisons' in names
    assert all(not any(word in name for word in ('training', 'historical', 'rolling', 'replay')) for name in names)


def test_missing_runtime_does_not_create_or_start_an_account(tmp_path):
    manager = service.JobManager(tmp_path / 'not-installed')
    assert manager.status()['ready'] is False
    with pytest.raises(ValueError, match='未就绪'):
        manager.start({'model': 'base'}, None, None)
    assert not (tmp_path / 'not-installed').exists()


def runtime_checker():
    path = Path(__file__).resolve().parents[2] / 'scripts/kronos/check_runtime.py'
    spec = spec_from_file_location('public_runtime_checker', path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_probe_excludes_secrets_and_disables_import_writes(monkeypatch):
    checker = runtime_checker()
    monkeypatch.setenv('EXAMPLE_API_SECRET', 'test-value')
    monkeypatch.setenv('PYTHONPATH', 'untrusted-source')
    env = checker.probe_environment()
    assert 'EXAMPLE_API_SECRET' not in env and 'PYTHONPATH' not in env
    assert env['PYTHONDONTWRITEBYTECODE'] == env['PYTHONNOUSERSITE'] == '1'
    assert env['HF_HUB_OFFLINE'] == env['HF_HUB_DISABLE_TELEMETRY'] == '1'


def test_runtime_runner_verification_is_bounded(tmp_path):
    checker = runtime_checker()
    path = tmp_path / 'runner.py'
    path.write_bytes(b'x' * 65_537)
    with pytest.raises(ValueError, match='exceeds'):
        checker.read_runner(path)
