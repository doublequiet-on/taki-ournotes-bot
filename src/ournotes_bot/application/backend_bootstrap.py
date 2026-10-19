# L3: CLI-owned assembly for the M2 local backend; no QQ SDK or notification sender.
import logging
import os
from dataclasses import replace
from pathlib import Path
from aiohttp import web
from ..ai_query import AIQueryParser
from ..data import SongRepository
from ..sources.cutoff_history import configure_sources
from .backend import Backend, create_app
from .backend_protocol import ProtocolError, require


def build_backend(args, settings):
    token = os.environ.get('OURNOTES_BACKEND_TOKEN', '')
    require(len(token) >= 32, 'backend_token_required')
    require(1 <= args.port <= 65535, 'backend_port')
    settings = replace(settings, app_id='', app_secret='')
    repository = SongRepository(settings.data_base, settings.cache_file, settings.cache_ttl_hours,
                                chart_source=settings.chart_source, data_source=settings.data_source)
    configure_sources(repository, settings)
    backend = Backend(repository, AIQueryParser(settings), settings, instance_id=args.instance_id,
                      app_id=args.app_id, ledger_path=Path(args.state_dir)/'deliveries-v1.sqlite3')
    return create_app(backend, token, repository.load)


def run_backend(args, settings):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    try:
        app = build_backend(args, settings)
    except ProtocolError as exc:
        raise SystemExit('后端启动失败：'+exc.code) from None
    # No public bind switch; no URL/access logs containing opaque identifiers.
    web.run_app(app, host='127.0.0.1', port=args.port, access_log=None, print=None)
