# L3
# Input: Anonymous Haneoka HTTPS URL, caller-owned response cap and total timeout.
# Output: Bounded decoded bytes and lowercase response headers, or ChartDataError.
# Pos: Data / Sources Haneoka public HTTP facility; see ../L2-2.md.
# Effects/Dependencies: Scoped thread-local connections; HTTPS only, no redirects/implicit proxy.
"""Public transport; resource permissions and refresh budgets belong to callers."""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, contextmanager
import os
from threading import local
from urllib.parse import urlsplit

from ..errors import ChartDataError

ORIGIN = "https://haneoka.org"
_connections = local()


@contextmanager
def public_http_scope():
    """Reuse connections within one synchronous operation, isolated per thread.

    Nested callers share the outer scope. Sessions/loops are created lazily and
    always closed here, including failed reads; no process-lifetime HTTP worker.
    """
    if getattr(_connections, 'scope', None) is not None:
        yield
        return
    state = _connections.scope = {'loop': None, 'stack': None, 'session': None}
    try:
        yield
    finally:
        try:
            if state['loop'] is not None:
                try:
                    state['loop'].run_until_complete(state['stack'].aclose())
                    state['loop'].run_until_complete(state['loop'].shutdown_asyncgens())
                finally:
                    state['loop'].close()
        finally:
            del _connections.scope


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
        async def request():
            state = _connections.scope
            if state['session'] is None:
                state['session'] = await state['stack'].enter_async_context(aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=timeout), trust_env=False,
                    cookie_jar=aiohttp.DummyCookieJar()))
            session = state['session']
            for attempt in range(2):
                received_headers = False
                try:
                    async with AsyncExitStack() as stack:
                        # Reserve half the total budget for one new connection.
                        response = await asyncio.wait_for(stack.enter_async_context(session.get(
                            url, allow_redirects=False,
                            timeout=aiohttp.ClientTimeout(total=timeout),
                            headers={"User-Agent": "Taki-chart/1", "Accept-Encoding": "gzip"},
                            **public_proxy_options(),
                        )), timeout / 2 if attempt == 0 else None)
                        received_headers = True
                        if response.status != 200:
                            raise ChartDataError(f"Haneoka HTTP {response.status}")
                        chunks, size = [], 0
                        async for chunk in response.content.iter_chunked(65536):
                            size += len(chunk)
                            if size > limit:
                                raise ChartDataError("Haneoka response is too large")
                            chunks.append(chunk)
                        return b"".join(chunks), {
                            key.lower(): value for key, value in response.headers.items()}
                except (aiohttp.ClientConnectionError, asyncio.TimeoutError, ConnectionResetError) as exc:
                    if attempt or received_headers or isinstance(exc, aiohttp.ClientSSLError):
                        raise
        try:
            # Both attempts, response bodies and cleanup share the caller's deadline.
            return await asyncio.wait_for(request(), timeout)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise ChartDataError("Haneoka request failed") from exc

    with public_http_scope():
        state = _connections.scope
        if state['loop'] is None:
            state['loop'], state['stack'] = asyncio.new_event_loop(), AsyncExitStack()
        return state['loop'].run_until_complete(read())
