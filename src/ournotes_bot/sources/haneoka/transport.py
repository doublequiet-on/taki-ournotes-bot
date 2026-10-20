# L3
# Input: Anonymous Haneoka HTTPS URL, caller-owned response cap and total timeout.
# Output: Bounded decoded bytes and lowercase response headers, or ChartDataError.
# Pos: Data / Sources Haneoka public HTTP facility; see ../L2-2.md.
# Effects/Dependencies: HTTPS only, no redirects/implicit proxy; optional explicit anonymous proxy.
"""Public transport; resource permissions and refresh budgets belong to callers."""
from __future__ import annotations

import asyncio
import os
from urllib.parse import urlsplit

from ..errors import ChartDataError

ORIGIN = "https://haneoka.org"


def public_proxy_options():
    """Opt-in route for public downloads; never consume ambient proxy credentials."""
    value = os.environ.get('OURNOTES_PUBLIC_HTTP_PROXY', '').strip()
    if not value:
        return {}
    try:
        parts = urlsplit(value)
        valid = (parts.scheme in ('http', 'https') and parts.hostname and parts.port != 0
                 and parts.username is None and parts.password is None
                 and parts.path in ('', '/') and not parts.query and not parts.fragment
                 and not any(ch.isspace() for ch in value))
    except ValueError:
        valid = False
    if not valid:
        raise ChartDataError('Invalid public HTTP proxy configuration')
    return {'proxy': value}


def public_get(url, limit, timeout):
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "haneoka.org" or parts.fragment:
        raise ChartDataError("Invalid Haneoka URL")

    async def read():
        import aiohttp
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout),
                                             trust_env=False) as session:
                async with session.get(url, allow_redirects=False,
                                       headers={"User-Agent": "Taki-chart/1", "Accept-Encoding": "gzip"},
                                       **public_proxy_options()) as response:
                    if response.status != 200:
                        raise ChartDataError(f"Haneoka HTTP {response.status}")
                    chunks, size = [], 0
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > limit:
                            raise ChartDataError("Haneoka response is too large")
                        chunks.append(chunk)
                    return b"".join(chunks), {key.lower(): value for key, value in response.headers.items()}
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise ChartDataError("Haneoka request failed") from exc

    return asyncio.run(read())
