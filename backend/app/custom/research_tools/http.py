# ruff: noqa: RUF001 -- Localized failure messages.
"""Fixed-host public research transport, bounded independently of market feeds."""
import json
import re
from urllib.parse import urlparse

import httpx

HOSTS = {'api.finmindtrade.com', '500.farm', 'raw.githubusercontent.com', 'www1.hkexnews.hk',
         'datacenter-web.eastmoney.com', 'api.nasdaq.com', 'query1.finance.yahoo.com'}


def get_json(url: str, params: dict | None = None, *, headers: dict | None = None, jsonp: bool = False):
    if urlparse(url).scheme != 'https' or urlparse(url).hostname not in HOSTS:
        raise ValueError('研究数据地址不受支持')
    try:
        with (httpx.Client(timeout=httpx.Timeout(8,connect=4),follow_redirects=False,
                           headers=headers or {'User-Agent':'Mozilla/5.0'}) as client,
              client.stream('GET',url,params=params) as response):
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 2_000_000:
                    raise ValueError('研究数据响应超出限制')
                chunks.append(chunk)
        raw = b''.join(chunks).decode('utf-8-sig')
        if jsonp:
            match = re.fullmatch(r'\s*callback\(([\s\S]*)\)\s*;?\s*',raw)
            if match is None:
                raise ValueError('Invalid callback envelope')
            raw = match.group(1)
        return json.loads(raw)
    except (httpx.HTTPError,ValueError,UnicodeError):
        raise ValueError('免费研究源本次未返回有效资料，请稍后重试') from None
