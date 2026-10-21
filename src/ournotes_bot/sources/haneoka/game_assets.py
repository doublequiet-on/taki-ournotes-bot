# L3
# Input: Built-in game-art name and a caller-owned runtime cache directory.
# Output: Verified runtime artwork, plus explicit preparation of fixed note assets.
# Pos: Sources / Haneoka; game artwork is runtime-only, never redistributed.
# Effects: Bounded anonymous HTTPS or explicit local import, atomic cache writes; no credentials/redirects.
import asyncio
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
import threading
import time

from .transport import public_get, public_proxy_options
from ..errors import ChartDataError

MANIFEST = json.loads(Path(__file__).with_suffix('.json').read_text('utf-8'))
_MEMORY = {}
_FAILED = {}
_LOCK = threading.Lock()
_ASSET_LOCKS = {name: threading.Lock() for name in MANIFEST}


def _fetch(url, timeout=8):
    if url.startswith('https://haneoka.org/'):
        return public_get(url, 4_000_000, timeout)[0]
    # Only called with a checked-in manifest URL; never accepts user URLs.
    async def read():
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout), trust_env=False) as session:
            headers = ({"Accept": "application/vnd.github.raw+json", "X-GitHub-Api-Version": "2022-11-28"}
                       if url.startswith("https://api.github.com/") else {})
            async with session.get(url, allow_redirects=False, headers=headers, **public_proxy_options()) as response:
                if response.status != 200:
                    raise ChartDataError('Game asset unavailable')
                chunks = []; size = 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > 4_000_000:
                        raise ChartDataError('Game asset too large')
                    chunks.append(chunk)
                return b''.join(chunks)
    import aiohttp
    try:
        return asyncio.run(read())
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        raise ChartDataError("Game asset request failed") from exc


NOTE_ASSETS = ('skin001.json', 'skin001.png')


def prepare_note_assets(cache_dir, source_dir=None, *, names=NOTE_ASSETS):
    """Verify and persist note artwork before queries; optional offline import."""
    prepared = []
    for name in names:
        spec = MANIFEST[name]
        filename = spec['sha256'] + Path(name).suffix
        path = Path(cache_dir) / filename
        raw = path.read_bytes() if path.is_file() and path.stat().st_size <= 4_000_000 else b''
        if hashlib.sha256(raw).hexdigest() != spec['sha256']:
            if source_dir is None:
                # Preparation tolerates slow upstream; the normal query budget stays 8s.
                raw = _fetch(spec.get('prepare_url', spec['url']), timeout=180)
            else:
                source = Path(source_dir) / filename
                if source.stat().st_size > 4_000_000:
                    raise ChartDataError(f'Note artwork is too large: {name}')
                raw = source.read_bytes()
            if hashlib.sha256(raw).hexdigest() != spec['sha256']:
                raise ChartDataError(f'Note artwork checksum mismatch: {name}')
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=filename + '.', suffix='.tmp', dir=path.parent)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(raw)
                Path(temporary).replace(path)
            finally:
                Path(temporary).unlink(missing_ok=True)
        prepared.append(path)
    return tuple(prepared)


def invalidate_assets(names):
    for name in names:
        if name not in MANIFEST:
            raise ValueError('Unknown built-in game asset')
        with _LOCK:
            lock = _ASSET_LOCKS.setdefault(name, threading.Lock())
        with lock:
            _MEMORY.pop(name, None)
            _FAILED.pop(name, None)


def asset_bytes(name, cache_dir):
    spec = MANIFEST.get(name)
    if spec is None:
        raise ValueError('Unknown built-in game asset')
    # A slow/cold icon must not block unrelated assets or warm reads.
    with _LOCK:
        asset_lock = _ASSET_LOCKS.setdefault(name, threading.Lock())
    with asset_lock:
        if name in _MEMORY:
            return _MEMORY[name]
        if time.monotonic() < _FAILED.get(name, 0):
            return None
        path = Path(cache_dir) / (spec['sha256'] + Path(name).suffix)
        try:
            raw = path.read_bytes() if path.is_file() and path.stat().st_size <= 4_000_000 else b''
            if hashlib.sha256(raw).hexdigest() != spec['sha256']:
                raw = _fetch(spec['url'])
                if hashlib.sha256(raw).hexdigest() != spec['sha256']:
                    raise ChartDataError('Game asset checksum mismatch')
                path.parent.mkdir(parents=True, exist_ok=True)
                temp = path.with_suffix(path.suffix + '.tmp')
                temp.write_bytes(raw)
                temp.replace(path)
            _MEMORY[name] = raw
            return raw
        except (OSError, ChartDataError, TimeoutError) as exc:
            logging.getLogger(__name__).warning('Game asset %s unavailable: %s', name, type(exc).__name__)
            _FAILED[name] = time.monotonic() + 60
            return None
