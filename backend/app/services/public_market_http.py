"""Bounded public research transport; shared source budgets across new adapters."""
from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict
from urllib.parse import urlsplit

import httpx

HOSTS = {'qt.gtimg.cn': 'tencent', 'ifzq.gtimg.cn': 'tencent',
         'stock.gtimg.cn': 'tencent', 'markets.newyorkfed.org': 'nyfed'}


class PublicDataError(ValueError):
    pass


class PublicMarketHTTP:
    def __init__(self, *, clock=time.monotonic, sleep=time.sleep, client=None):
        self.clock, self.sleep = clock, sleep
        self._client = client
        self._lock = threading.Lock()
        self._sources = {}
        self._cache = OrderedDict()

    def _reserve(self, source):
        deadline = self.clock() + 4
        while True:
            with self._lock:
                now = self.clock()
                row = self._sources.setdefault(source, dict(next=0., blocked=0., requests=0, errors=0, failures=0))
                if row['blocked'] > now:
                    raise PublicDataError('Public source cooling down; retry later')
                delay = max(0., row['next'] - now)
                if delay == 0:
                    row['next'] = now + 1.0
                    row['requests'] += 1
                    return
                if now + delay > deadline:
                    raise PublicDataError('Public source busy; retry later')
            self.sleep(delay)

    def _failure(self, source, status=0, retry_after=None):
        with self._lock:
            row = self._sources[source]
            row['errors'] += 1
            row['failures'] += 1
            delay = min(60, 2 ** min(row['failures'], 6))
            if status == 403:
                delay = 300
            elif status == 429:
                try:
                    delay = max(60, min(3600, float(retry_after)))
                except (TypeError, ValueError):
                    delay = 60
            row['blocked'] = self.clock() + delay

    def text(self, url, params=None, *, ttl=60, encoding='utf8'):
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.username or parsed.password:
            raise ValueError('Unsupported public research host')
        source = HOSTS[parsed.hostname]
        key = (url, tuple(sorted((params or {}).items())), encoding)
        with self._lock:
            cached = self._cache.get(key)
            if ttl > 0 and cached and self.clock() < cached[0]:
                return cached[1]
        self._reserve(source)
        try:
            with self._lock:
                if self._client is None:
                    self._client = httpx.Client(timeout=httpx.Timeout(8, connect=3), follow_redirects=False,
                                                headers={'User-Agent': 'stockfund-panel research/1.1'})
                client = self._client
            deadline = self.clock() + 15
            with client.stream('GET', url, params=params) as response:
                if response.status_code != 200:
                    self._failure(source, response.status_code, response.headers.get('retry-after'))
                    raise PublicDataError(f'Public source HTTP {response.status_code}; request stopped')
                body = bytearray()
                for block in response.iter_bytes():
                    body.extend(block)
                    if len(body) > 2_000_000 or self.clock() > deadline:
                        raise PublicDataError('Public response exceeds byte budget')
            value = body.decode(encoding)
        except PublicDataError:
            raise
        except (httpx.HTTPError, UnicodeError) as exc:
            self._failure(source)
            raise PublicDataError('Public source unavailable; request stopped') from exc
        with self._lock:
            self._sources[source]['failures'] = 0
            if ttl > 0:
                self._cache[key] = (self.clock() + ttl, value)
                self._cache.move_to_end(key)
            while len(self._cache) > 64:
                self._cache.popitem(last=False)
        return value

    def status(self):
        with self._lock:
            return {name: dict(requests=row['requests'], errors=row['errors'],
                               cooldown_seconds=max(0, round(row['blocked'] - self.clock())))
                    for name, row in copy.deepcopy(self._sources).items()}


PUBLIC_HTTP = PublicMarketHTTP()
