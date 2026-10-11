# L3
# Input: Anonymous Haneoka HTTPS URL, caller-owned response cap and total timeout.
# Output: Bounded raw bytes and lowercase response headers, or ChartDataError.
# Pos: Data / Sources Haneoka public HTTP facility; see ../L2-2.md.
# Effects/Dependencies: HTTPS only, no redirects/proxy environment; no resource/cache imports.
"""Public transport; resource permissions and refresh budgets belong to callers."""
from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

from ..errors import ChartDataError

ORIGIN = "https://haneoka.org"


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
                                       headers={"User-Agent": "Taki-chart/1", "Accept-Encoding": "identity"}) as response:
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
