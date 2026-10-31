"""Verify an explicitly selected optional runtime; never download or train a model."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path


def probe_environment() -> dict[str, str]:
    allowed = {'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP',
               'USERPROFILE', 'LOCALAPPDATA', 'APPDATA'}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(PYTHONNOUSERSITE='1', PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
               HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', DO_NOT_TRACK='1')
    return env


def read_runner(path: Path) -> bytes:
    if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
        raise ValueError('runner.py must be a regular local file')
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('runner.py must be a regular local file')
    with path.open('rb') as stream:
        value = stream.read(65_537)
    if len(value) > 65_536:
        raise ValueError('runner.py exceeds the verification limit')
    return value


def check(runtime: Path) -> dict:
    runtime = runtime.resolve(strict=True)
    runner = runtime / 'runner.py'
    template = Path(__file__).with_name('runner.py')
    template_bytes = read_runner(template)
    if read_runner(runner) != template_bytes:
        raise ValueError('runner.py does not match this release')
    tree = ast.parse(template_bytes.decode('utf-8'))
    hashes = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == 'MODEL_HASHES' for target in node.targets))
    for name, expected in hashes.items():
        with (runtime / 'models' / name / 'model.safetensors').open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                raise ValueError(f'{name} model checksum mismatch')
        if not (runtime / 'models' / name / 'config.json').is_file():
            raise ValueError(f'{name} config.json is missing')
    if not (runtime / 'source/model/__init__.py').is_file():
        raise ValueError('Official Kronos source/model is missing')
    executable = runtime / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')
    probe_code = (
        'import sys; sys.path.insert(0,"source"); import pandas, torch; '
        'from model import Kronos, KronosPredictor, KronosTokenizer; '
        'assert torch.cuda.is_available(), "CUDA is unavailable"'
    )
    probe = subprocess.run([str(executable), '-B', '-c', probe_code],
        cwd=runtime, capture_output=True, timeout=60, check=False,
        env=probe_environment())
    if probe.returncode:
        raise ValueError('Optional Python dependencies, model imports or CUDA check failed')
    return {'ready': True, 'model': 'base', 'device': 'cuda', 'runner_sha256': hashlib.sha256(template_bytes).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--write-ready', action='store_true', help='Write ready.json only after all checks pass')
    args = parser.parse_args()
    try:
        runtime = args.runtime.expanduser()
        if runtime.is_symlink() or getattr(runtime, 'is_junction', lambda: False)():
            raise ValueError('Runtime directory must not be a link')
        runtime = runtime.resolve(strict=True)
        result = check(runtime)
        if args.write_ready:
            descriptor, temporary = tempfile.mkstemp(dir=runtime, suffix='.tmp')
            try:
                with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                    json.dump(result, stream)
                os.replace(temporary, runtime / 'ready.json')
            finally:
                Path(temporary).unlink(missing_ok=True)
        print(json.dumps(result))
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f'Runtime not ready: {error}\n')


if __name__ == '__main__':
    main()
