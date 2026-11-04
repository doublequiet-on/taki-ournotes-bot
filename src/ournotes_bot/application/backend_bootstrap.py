# L3: CLI-owned M4 backend, business locks, background tasks and notice ledger; no QQ SDK.
import logging
import os
from dataclasses import replace
from pathlib import Path
from aiohttp import web
from ..natural_query.parser import LocalQueryParser
from .query_runtime import QueryRuntime
from .backend_sources import CODE_FILES, QUERY_CODE_FILES
from ..data import SongRepository
from ..sources.cutoff_history import configure_sources
from .backend import Backend, create_app
from .data_owner import DataOwner
from .runtime_tasks import RuntimeTasks
from .operations_sources import SourceOperations
from .operations_runtime import RuntimeOperations
from .backend_protocol import ProtocolError, require


def build_backend(args, settings, *, assert_recovery_current=None):
    """Existing personal composition; unchanged CLI default."""
    return _build_backend(args, settings, query_only=False, assert_recovery_current=assert_recovery_current)


def build_query_backend(args, settings, *, assert_recovery_current=None):
    """Explicit development query composition, not a custom product approval.

    No environment flag selects this root. Final identity/scope activation belongs
    to the trusted 09/10 composition; this function never grants a management scope.
    """
    return _build_backend(args, settings, query_only=True, assert_recovery_current=assert_recovery_current)


def _build_backend(args, settings, *, query_only, assert_recovery_current=None):
    # This is a pre-construction barrier, not a running-writer isolation proof.
    # A trusted launcher must keep this root unconstructed until both roles have
    # been reviewed. No environment/Console boolean can authorize recovery here.
    if assert_recovery_current is not None:
        require(callable(assert_recovery_current), 'trusted_recovery_check_required')
        require(assert_recovery_current() is None, 'trusted_recovery_check_required')
    token = os.environ.get('OURNOTES_BACKEND_TOKEN', '')
    require(len(token) >= 32, 'backend_token_required')
    require(1 <= args.port <= 65535, 'backend_port')
    diagnostics = os.environ.get('TAKI_DIAGNOSTICS_ENABLED','1')
    require(diagnostics in ('0','1'), 'diagnostics_configuration')
    capacities = {}
    for name in ('request', 'context', 'epoch'):
        raw = os.environ.get('TAKI_LEDGER_'+name.upper()+'_CAPACITY', '4096')
        require(raw.isascii() and raw.isdecimal() and len(raw) <= 19,
                'ledger_'+name+'_capacity_configuration')
        value = int(raw)
        require(0 < value <= 2**63-1, 'ledger_'+name+'_capacity_configuration')
        capacities['ledger_'+name+'_capacity'] = value
    owner = DataOwner(settings)
    backend = None
    try:
        settings = replace(settings, app_id='', app_secret='')
        if query_only:
            settings = replace(settings, ai_api_key='', chat_engine='legacy', update_notices=False)
            parser = LocalQueryParser(settings)
        else:
            from ..ai_query import AIQueryParser
            parser = AIQueryParser(settings)
        repository = SongRepository(settings.data_base, settings.cache_file, settings.cache_ttl_hours,
                                    chart_source=settings.chart_source, data_source=settings.data_source)
        configure_sources(repository, settings)
        backend = Backend(repository, parser, settings, instance_id=args.instance_id,
                          app_id=args.app_id, ledger_path=Path(args.state_dir)/'deliveries-v1.sqlite3',
                          diagnostics_enabled=diagnostics=='1',
                          code_files=QUERY_CODE_FILES if query_only else CODE_FILES, **capacities)
        if query_only:
            backend.query_runtime = QueryRuntime(backend)
        else:
            from .personal_runtime import configure_personal
            configure_personal(backend, args, settings)
        backend.runtime_tasks = RuntimeTasks(repository, settings, backend.knowledge)
        SourceOperations(backend).register()
        RuntimeOperations(backend).register()
        backend.data_owner = owner
        if assert_recovery_current is None:
            return create_app(backend, token, repository.load)
        return create_app(backend, token, repository.load, assert_recovery_current=assert_recovery_current)
    except BaseException:
        if backend is not None:
            if backend.knowledge is not None: backend.knowledge.close()
            if backend.model_runtime is not None:
                if backend.model_runtime.agent: backend.model_runtime.agent.close()
                backend.model_runtime.usage.close()
            backend.ledger.close()
            backend.diagnostics.close()
            if backend.operations.db is not None: backend.operations.db.close()
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
