"""Isolated Receiver -> HTTP Backend -> QueryGate benchmark (no real accounts).

The checked-in catalog and deterministic synthetic artwork/chart are fixtures,
not current game data. The normal query, render, encode and delivery paths run.
Prepare pinned note assets separately with prepare_note_assets.py before running.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'benchmark-only-token-never-a-real-credential'
INSTANCE = 'benchmark-instance'
APP = 'benchmark-app'


def process_sample(pid):
    """External current RSS and cumulative CPU; never inspect process content."""
    if os.name == 'nt':
        from ctypes import wintypes as w
        class Memory(ctypes.Structure):
            _fields_ = [('cb', w.DWORD), ('faults', w.DWORD)] + [(k, ctypes.c_size_t) for k in
                ('peak', 'rss', 'paged_peak', 'paged', 'nonpaged_peak', 'nonpaged', 'pagefile', 'pagefile_peak')]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = w.HANDLE
        kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel.CloseHandle.argtypes = [w.HANDLE]
        kernel.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [w.HANDLE, ctypes.POINTER(Memory), w.DWORD]
        handle = kernel.OpenProcess(0x410, False, pid)
        if not handle:
            return None
        try:
            memory = Memory(); memory.cb = ctypes.sizeof(memory)
            times = [w.FILETIME() for _ in range(4)]
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb):
                return None
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                return None
            cpu = sum((t.dwHighDateTime << 32) + t.dwLowDateTime for t in times[2:]) / 1e7
            return {'rss_bytes': memory.rss, 'peak_rss_bytes': memory.peak, 'cpu_seconds': cpu}
        finally:
            kernel.CloseHandle(handle)
    if sys.platform.startswith('linux'):
        try:
            stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
            return {'rss_bytes': int(stat[21]) * os.sysconf('SC_PAGE_SIZE'),
                    'cpu_seconds': (int(stat[11]) + int(stat[12])) / os.sysconf('SC_CLK_TCK')}
        except (OSError, ValueError, IndexError):
            return None
    return None


def serve(args):
    sys.path.insert(0, str(ROOT / 'src'))
    from unittest.mock import patch
    from contextlib import ExitStack
    from urllib.parse import urlsplit
    from aiohttp import web
    from PIL import Image
    from ournotes_bot.application.backend import Backend, create_app
    from ournotes_bot.application import replies
    from ournotes_bot.sources.haneoka import catalog, game_assets
    from ournotes_bot.query import continuation
    from ournotes_bot.rendering import image_output
    from ournotes_bot.data import SongRepository
    from ournotes_bot.ai_query import AIQueryParser
    from ournotes_bot.config import Settings

    def network_guard(event, values):
        if event == 'socket.connect':
            address = values[1]
            if not isinstance(address, tuple) or address[0] != '127.0.0.1':
                raise RuntimeError('benchmark forbids external connections')
        if event in ('socket.getaddrinfo', 'socket.sendto'):
            raise RuntimeError('benchmark forbids DNS and datagrams')
    sys.addaudithook(network_guard)
    state = args.output
    state.mkdir(parents=True, exist_ok=True)
    snapshot = json.loads((ROOT / 'tests/fixtures/haneoka_catalog.json').read_bytes())['snapshot']
    snapshot['fetched_at'] = 1000
    for server in ('jp', 'intl'):
        snapshot['documents'][server].setdefault('ui-marks', {})
    snapshot['documents']['intl'].setdefault('progression', {k: [] for k in
        ('memberCardLevels', 'memberCardRanks', 'supportCardLevels', 'supportCardRanks')})
    counters, elapsed, lock = {}, {}, threading.Lock()
    def measured(name, fn):
        def call(*a, **kw):
            started = time.perf_counter()
            try:
                return fn(*a, **kw)
            finally:
                with lock:
                    counters[name] = counters.get(name, 0) + 1
                    elapsed[name] = elapsed.get(name, 0) + (time.perf_counter() - started) * 1000
        return call
    def fetch(url, limit, timeout):
        ident = snapshot['identities'][urlsplit(url).path.split('/')[-2]]
        time.sleep(.003)
        return catalog.encoded(ident), {'x-haneoka-release-id': ident['releaseId'], 'x-haneoka-source-id': ident['sourceId']}
    source = catalog.CatalogRepository(state / 'catalog.json', fetch=measured('identity_io', fetch), clock=lambda: 1100)
    source.saved = snapshot
    repo = SongRepository('', state / 'legacy.json', data_source='haneoka', chart_source='moenotes')
    repo._catalog_repository = source
    settings = Settings('', '', '', state / 'legacy.json', 6, data_source='haneoka',
                        ai_metrics_file=state/'metrics.json', ai_quota_file=state/'quota.json')
    chart = {'events': {'bpm': [{'t': 0, 'bpm': 160}], 'sig': [{'t': 0, 'sig': [4, 4]}]},
             'notes': [{'t': i*240, 'pos': (i % 4)*6, 'size': 6} for i in range(128)]}
    artwork = {}
    def asset(url, size, **kwargs):
        # Synthetic opaque artwork, deterministic per URL; source delays are declared.
        key = (url, size)
        with lock:
            miss = key not in artwork
            artwork[key] = True
        if miss:
            time.sleep(.015)
        color = tuple(hashlib.sha256(url.encode()).digest()[:3])
        return Image.new('RGB', size, color)
    def game_asset(name, directory):
        if name not in game_assets.NOTE_ASSETS:
            return None  # declared fixture omits decorative game icons
        spec = game_assets.MANIFEST[name]
        raw = (args.assets / (spec['sha256'] + Path(name).suffix)).read_bytes()
        if hashlib.sha256(raw).hexdigest() != spec['sha256']:
            raise ValueError('benchmark note asset checksum')
        return raw
    with ExitStack() as stack:
        for name in ('convert', 'convert_info'):
            stack.enter_context(patch.object(catalog, name, measured(name, getattr(catalog, name))))
        version = measured('catalog_version', continuation.catalog_version)
        stack.enter_context(patch.object(continuation, 'catalog_version', version))
        stack.enter_context(patch.object(replies, 'catalog_version', version))
        stack.enter_context(patch.object(replies, 'visual_asset', measured('artwork_io', asset)))
        stack.enter_context(patch.object(replies, 'load_chart_score', measured('chart_io', lambda *a, **kw: chart)))
        stack.enter_context(patch.object(game_assets, 'asset_bytes', game_asset))
        stack.enter_context(patch.object(replies, 'render_prepared', measured('render_encode', replies.render_prepared)))
        stack.enter_context(patch.object(image_output, 'encode_image_result', measured('encode', image_output.encode_image_result)))
        repo.load()
        backend = Backend(repo, AIQueryParser(settings), settings, instance_id=INSTANCE, app_id=APP,
                          ledger_path=state/'delivery.sqlite3')
        acquire = backend.gate._workers.acquire
        async def acquire_worker():
            started = time.perf_counter()
            result = await acquire()
            with lock:
                counters['worker_wait'] = counters.get('worker_wait', 0)+1
                elapsed['worker_wait'] = elapsed.get('worker_wait', 0)+(time.perf_counter()-started)*1000
            return result
        backend.gate._workers.acquire = acquire_worker
        app = create_app(backend, TOKEN)
        async def report(request):
            return web.json_response({'counters': counters, 'inclusive_stage_ms': elapsed,
                'requests': [dict(row, diagnostics=backend.diagnostics.read(row['id'])) for row in backend.ledger.admin_rows()],
                'cache': source.cache_metrics() if hasattr(source, 'cache_metrics') else None,
                'pending_workers': backend.gate._pending})
        app.router.add_get('/v1/benchmark', report)
        async def started(app):
            (state/'ready.json').write_text(json.dumps({'pid': os.getpid(), 'port': args.port}), encoding='utf-8')
        app.on_startup.append(started)
        web.run_app(app, host='127.0.0.1', port=args.port, print=None)


def run(args):
    import socket
    from importlib.metadata import version
    target = args.output.resolve()
    target.mkdir(parents=True, exist_ok=True)
    def fingerprints():
        files = [ROOT/'tests/fixtures/haneoka_catalog.json', ROOT/'koishi/package-lock.json',
                 ROOT/'src/ournotes_bot/sources/haneoka/game_assets.json', Path(__file__), ROOT/'koishi/scripts/benchmark-queries.cjs',
                 *sorted((ROOT/'src/ournotes_bot/rendering/assets').glob('*.ttf'))]
        code = [*sorted((ROOT/'src').rglob('*.py')), *sorted((ROOT/'koishi/src').rglob('*.ts')),
                *sorted((ROOT/'koishi/lib').rglob('*.js'))]
        digest = lambda paths: {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        return digest(files), digest(code)
    inputs, source_and_build = fingerprints()
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    environment = dict(os.environ, QQ_APP_ID='', QQ_APP_SECRET='', AI_API_KEY='',
                       MOENOTES_OPEN_SECRET='', BDON_OPENPLATFORM='', PYTHONPATH=str(ROOT/'src'), PYTHONUTF8='1')
    rows = []
    for run_id in range(args.runs):
        for scenario in args.scenarios.split(','):
            for concurrency in map(int, args.concurrency.split(',')):
                name = f'{scenario}-c{concurrency}-r{run_id}'
                folder = target/name
                if folder.exists():
                    raise ValueError(f'refuse to overwrite previous evidence: {folder}')
                folder.mkdir()
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
                with (folder/'backend.log').open('w', encoding='utf-8') as log:
                    server = subprocess.Popen([sys.executable, '-B', __file__, '--serve', '--port', str(port),
                        '--output', str(folder), '--assets', str(args.assets.resolve())], env=environment, stdout=log, stderr=log)
                    client = None
                    try:
                        deadline = time.monotonic()+30
                        while not (folder/'ready.json').exists():
                            if server.poll() is not None or time.monotonic() >= deadline:
                                raise RuntimeError(f'backend did not start; inspect {folder}/backend.log')
                            time.sleep(.05)
                        # Windows venv python.exe can be a launcher; sample the backend itself.
                        backend_pid = json.loads((folder/'ready.json').read_text('utf-8'))['pid']
                        client = subprocess.Popen([args.node, str(ROOT/'koishi/scripts/benchmark-queries.cjs'),
                            str(port), str(folder), scenario, str(concurrency), str(args.requests)], env=environment,
                            stdout=log, stderr=log)
                        samples = {'python': [], 'node': []}
                        deadline = time.monotonic()+300
                        while client.poll() is None:
                            if time.monotonic() >= deadline:
                                raise TimeoutError(f'benchmark {name} exceeded 300s')
                            for label, pid in (('python', backend_pid), ('node', client.pid)):
                                sample = process_sample(pid)
                                if sample:
                                    samples[label].append(sample)
                            time.sleep(.025)
                        if client.returncode:
                            raise RuntimeError(f'gateway failed; inspect {folder}/backend.log')
                        result = json.loads((folder/'result.json').read_text('utf-8'))
                        result['process_samples'] = {label: {'count': len(values),
                            'peak_rss_bytes': max((v.get('peak_rss_bytes', v['rss_bytes']) for v in values), default=None),
                            'cpu_seconds': max((v['cpu_seconds'] for v in values), default=None)} for label, values in samples.items()}
                        result['run'] = run_id
                        result['sampled_pids'] = {'python': backend_pid, 'python_launcher': server.pid, 'node': client.pid}
                        rows.append(result)
                        print(name, 'success=', result['success'], 'p95=', round(result['p95_ms']), flush=True)
                    finally:
                        if client is not None and client.poll() is None:
                            client.terminate(); client.wait(timeout=15)
                        server.terminate(); server.wait(timeout=15)
    if fingerprints() != (inputs, source_and_build):
        raise RuntimeError('source, compiled gateway or inputs changed during benchmark; discard comparison')
    manifest = {'schema': 2, 'head': head, 'source_and_build': source_and_build,
        'host': platform.platform(), 'logical_cpus': os.cpu_count(), 'python': sys.version,
        'python_dependencies': {name: version(name) for name in ('Pillow', 'aiohttp', 'qq-botpy')},
        'limits': {'gateway_pending': 8, 'query_workers': 2, 'query_queue': 4, 'retained_reply_bytes': 128000000,
                   'host_resource_limit': 'No 4-core/4-GB cap applied; local fixture measurements only'},
        'node': subprocess.check_output([args.node, '--version'], text=True).strip(),
        'arguments': {k: str(v) for k, v in vars(args).items()},
        'inputs': inputs,
        'evidence_scope': 'Local fixture end-to-end; synthetic artwork/chart and 3ms identity/15ms artwork/120ms upload delays; no real QQ or model.',
        'rows': rows}
    (target/'results.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--assets', type=Path, required=True, help='Prepared pinned note asset cache (read-only)')
    parser.add_argument('--node', default=shutil.which('node') or 'node')
    parser.add_argument('--runs', type=int, choices=range(1, 6), default=3)
    parser.add_argument('--requests', type=int, choices=range(4, 49), default=8)
    parser.add_argument('--concurrency', choices=('1', '2', '4', '1,2,4'), default='1,2,4')
    parser.add_argument('--scenarios', default='light,hot,cold,chart,mixed,ask-mixed')
    parser.add_argument('--serve', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--port', type=int, default=0, help=argparse.SUPPRESS)
    options = parser.parse_args()
    if options.serve:
        serve(options)
    else:
        run(options)
