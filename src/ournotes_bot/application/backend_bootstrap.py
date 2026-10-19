# L3: CLI-owned M4 backend, business locks, background tasks and notice ledger; no QQ SDK.
import logging
import os
from dataclasses import replace
from pathlib import Path
from aiohttp import web
from ..ai_query import AIQueryParser
from ..data import SongRepository
from ..sources.cutoff_history import configure_sources
from .backend import Backend, create_app
from .data_owner import DataOwner
from .runtime_tasks import RuntimeTasks
from .notice_reports import NoticeReports, RELEASE_SOURCE
from .notice_gateway import NoticeGateway
from ..ai_client import AIClient
from ..config import CONFIG_ROOT
from .backend_protocol import ProtocolError, require


def build_backend(args, settings):
    token = os.environ.get('OURNOTES_BACKEND_TOKEN', '')
    require(len(token) >= 32, 'backend_token_required')
    require(1 <= args.port <= 65535, 'backend_port')
    owner = DataOwner(settings)
    backend = None
    try:
        settings = replace(settings, app_id='', app_secret='')
        repository = SongRepository(settings.data_base, settings.cache_file, settings.cache_ttl_hours,
                                    chart_source=settings.chart_source, data_source=settings.data_source)
        configure_sources(repository, settings)
        backend = Backend(repository, AIQueryParser(settings), settings, instance_id=args.instance_id,
                          app_id=args.app_id, ledger_path=Path(args.state_dir)/'deliveries-v1.sqlite3')
        backend.runtime_tasks = RuntimeTasks(repository, settings)
        if settings.update_notices:
            notice_ai = AIClient(settings.ai_base_url, settings.ai_api_key, settings.ai_model) if settings.ai_api_key else None
            reports = NoticeReports(CONFIG_ROOT, settings.cache_file.with_name('update-notices.sqlite3'), args.app_id,
                                    source=Path(os.getenv('OURNOTES_RELEASE_SOURCE', str(RELEASE_SOURCE))), ai_client=notice_ai)
            backend.notices = NoticeGateway(backend, reports)
        backend.data_owner = owner
        return create_app(backend, token, repository.load)
    except BaseException:
        if backend is not None:
            backend.ledger.close()
        owner.close()
        raise



def run_backend(args, settings):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    try:
        app = build_backend(args, settings)
    except ProtocolError as exc:
        raise SystemExit('后端启动失败：'+exc.code) from None
    # No public bind switch; no URL/access logs containing opaque identifiers.
    web.run_app(app, host='127.0.0.1', port=args.port, access_log=None, print=None)
