"""One local CUDA prediction per process; process exit releases the CUDA context."""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
os.environ['DO_NOT_TRACK'] = '1'
os.environ['OMP_NUM_THREADS'] = '4'
os.environ['MKL_NUM_THREADS'] = '4'
sys.path.insert(0, str(ROOT / 'source'))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

MODEL_HASHES = {
    'base': 'abff193acab6db1a0368e9773e75799d11403b6d054ee6d5f0a11aeabc5f4b83',
    'tokenizer': '59d85f6af76a2c3b8240ea06cb21db4213b4eeca053f246b23e29cf832fc6bee',
}


def verify_weights(name: str) -> Path:
    folder = ROOT / 'models' / name
    with (folder / 'model.safetensors').open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != MODEL_HASHES[name]:
        raise ValueError('Local model checksum mismatch')
    return folder


def validate_input(payload: dict) -> tuple[str, list, list, int]:
    if not isinstance(payload, dict):
        raise TypeError('Prediction input must be an object')
    model = payload.get('model', 'base')
    if model != 'base':
        raise ValueError('Only Base is enabled')
    history, future = payload.get('history'), payload.get('future_times')
    samples = payload.get('samples', 3)
    if type(samples) is not int or samples not in (1, 3, 5):
        raise ValueError('Samples must be 1, 3 or 5')
    if not isinstance(history, list) or not 30 <= len(history) <= 512:
        raise ValueError('Expected 30 to 512 complete historical bars')
    if not isinstance(future, list) or not 1 <= len(future) <= 20:
        raise ValueError('Expected 1 to 20 future trading timestamps')
    previous = None
    for row in history:
        stamp = row.get('time') if isinstance(row, dict) else None
        if not isinstance(stamp, str) or len(stamp) > 32:
            raise ValueError('History must have unique increasing timestamps')
        actual = datetime.fromisoformat(stamp)
        if actual.tzinfo is not None or (previous is not None and actual <= previous):
            raise ValueError('History must have unique increasing wall-clock timestamps')
        previous = actual
        prices = [row.get(key) for key in ('open', 'high', 'low', 'close')]
        if not all(type(value) in (int, float) and math.isfinite(value) and value > 0 for value in prices):
            raise ValueError('Invalid observed OHLC')
        op, hi, lo, cl = prices
        if hi < max(op, cl) or lo > min(op, cl) or lo > hi:
            raise ValueError('Invalid observed OHLC ordering')
    for stamp in future:
        if not isinstance(stamp, str) or len(stamp) > 32:
            raise ValueError('Future timestamps must follow the input cutoff')
        actual = datetime.fromisoformat(stamp)
        if actual.tzinfo is not None or actual <= previous:
            raise ValueError('Future timestamps must follow the input cutoff')
        previous = actual
    return model, history, future, samples


def encode_forecast(rows: list[dict], future: list[str]) -> tuple[list[dict], str, list[str]]:
    """Never repair or expose invalid OHLC; a valid close path may stand alone."""
    if len(rows) != len(future) or not rows:
        raise ValueError('Model returned an incomplete forecast')
    complete = True
    for row in rows:
        prices = [row.get(key) for key in ('open', 'high', 'low', 'close')]
        if not all(type(value) in (int, float) and math.isfinite(value) and value > 0 for value in prices):
            raise ValueError('Model returned non-finite or non-positive forecast prices')
        op, hi, lo, cl = prices
        complete = complete and hi >= max(op, cl) and lo <= min(op, cl) and hi >= lo
    keys = ('open', 'high', 'low', 'close') if complete else ('close',)
    bars = [{'time': stamp, **{key: float(row[key]) for key in keys}}
            for stamp, row in zip(future, rows, strict=True)]
    warnings = [] if complete else [
        '模型预测的开高低关系不满足K线约束，本次仅展示收盘价预测路径；未修正或展示其开高低预测。']
    return bars, 'ohlc' if complete else 'close_only', warnings


def predict(payload: dict) -> dict:
    model_name, history, future, samples = validate_input(payload)
    started = time.monotonic()
    import pandas as pd
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError('CUDA GPU is unavailable; prediction was not started')
    torch.set_num_threads(4)
    torch.manual_seed(42)
    torch.cuda.reset_peak_memory_stats()
    model_path = verify_weights(model_name)
    tokenizer_path = verify_weights('tokenizer')
    from model import Kronos, KronosPredictor, KronosTokenizer

    frame = pd.DataFrame(history)
    warnings = []
    # Missing channels use the upstream OHLC-only inference mode. Never estimate
    # turnover as volume*price or present zero padding as observed market data.
    complete_va = all(type(row.get(key)) in (int, float)
                      and math.isfinite(row[key]) and row[key] >= 0
                      for row in history for key in ('volume', 'amount'))
    columns = ['open', 'high', 'low', 'close']
    if complete_va:
        columns += ['volume', 'amount']
    else:
        warnings.append('成交量或成交额不完整，使用官方OHLC-only模式；内部缺失通道零填充，不作为真实量额展示。')
    stamps = pd.to_datetime(frame['time'], errors='raise')
    targets = pd.Series(pd.to_datetime(future, errors='raise'))
    if (stamps.dt.tz is not None or targets.dt.tz is not None
            or not stamps.is_monotonic_increasing or not targets.is_monotonic_increasing):
        raise ValueError('Expected increasing Beijing wall-clock timestamps')
    with contextlib.redirect_stdout(sys.stderr), torch.inference_mode():
        tokenizer = KronosTokenizer.from_pretrained(str(tokenizer_path), local_files_only=True).eval()
        model = Kronos.from_pretrained(str(model_path), local_files_only=True).eval()
        predictor = KronosPredictor(model, tokenizer, device='cuda:0', max_context=512)
        result = predictor.predict(frame[columns], stamps, targets, pred_len=len(future),
                                   T=0.6, top_p=0.9, sample_count=samples, verbose=False)
        torch.cuda.synchronize()
    rows = [{key: float(result.iloc[index][key]) for key in ('open', 'high', 'low', 'close')}
            for index in range(len(result))]
    bars, forecast_mode, forecast_warnings = encode_forecast(rows, future)
    warnings.extend(forecast_warnings)
    return {
        'forecast': bars, 'forecast_mode': forecast_mode, 'warnings': warnings,
        'model_sha256': MODEL_HASHES[model_name],
        'tokenizer_sha256': MODEL_HASHES['tokenizer'],
        'seed': 42, 'temperature': 0.6, 'top_p': 0.9, 'samples': samples,
        'path_method': 'mean_of_sampled_paths',
        'metrics': {'elapsed_seconds': round(time.monotonic() - started, 3),
                    'cuda_peak_allocated_mb': round(torch.cuda.max_memory_allocated() / 1024**2, 2),
                    'cuda_peak_reserved_mb': round(torch.cuda.max_memory_reserved() / 1024**2, 2)},
    }


def main() -> None:
    if '--health' in sys.argv:
        import torch
        value = {'ready': torch.cuda.is_available(), 'torch': torch.__version__,
                 'cuda': torch.version.cuda,
                 'device': torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
                 'models': ['base'], 'model_hashes_verified': False}
        for name in MODEL_HASHES:
            verify_weights(name)
        value['model_hashes_verified'] = True
        print(json.dumps(value, ensure_ascii=False))
        return
    raw = sys.stdin.buffer.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError('Prediction input exceeds size limit')
    value = predict(json.loads(raw))
    print(json.dumps(value, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:  # noqa: BLE001 -- CLI returns a bounded diagnostic without a traceback.
        # No credentials are ever passed to this process. Avoid raw torch or
        # environment tracebacks in the panel response; details stay local.
        print(json.dumps({'error': str(exc)[:300]}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
