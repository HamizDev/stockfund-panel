"""Provider boundary for the concept/industry presets and stock preview."""
# ruff: noqa: RUF001
from pathlib import Path


def get_snapshot(kind: str, data_dir: Path, *, refresh: bool = False) -> dict | None:
    from app.services.board_membership_store import read_snapshot
    if not refresh:
        return read_snapshot(data_dir, kind)
    from app.data_providers.custom.loader import get_provider, provider_has_dataset

    if not provider_has_dataset('fuyao', 'board_membership'):
        if refresh:
            raise RuntimeError('Fuyao板块归属能力未就绪，请核对API Key和数据源配置')
        return None
    provider = get_provider('fuyao')
    method = getattr(provider, 'get_board_membership', None)
    if method is None:
        if refresh:
            raise RuntimeError('当前数据源不支持板块归属')
        return None
    try:
        return method(kind, data_dir, refresh=refresh)
    except Exception as exc:
        # API errors may include upstream URLs or account information.
        raise RuntimeError('Fuyao板块刷新未完成，请核对网络、权限或接口数据；原快照保留') from exc


def cached_rows(kind: str) -> list[dict]:
    from app.config import settings

    snapshot = get_snapshot(kind, settings.data_dir)
    return snapshot['rows'] if snapshot else []


def cached_status() -> dict:
    import time

    from app.config import settings

    result = {}
    for kind in ('concept', 'industry'):
        snapshot = get_snapshot(kind, settings.data_dir)
        result[kind] = {'source': 'fuyao', 'available': bool(snapshot),
                        'fetched_at_ms': snapshot.get('fetched_at_ms') if snapshot else None,
                        'source_updated_at_ms': snapshot.get('source_updated_at_ms') if snapshot else None,
                        'board_count': snapshot.get('board_count') if snapshot else 0,
                        'stock_count': len(snapshot['rows']) if snapshot else 0,
                        'stale': bool(snapshot and time.time() * 1000 - snapshot['fetched_at_ms'] >= 86_400_000)}
    return result
