# L3: Personal composition of existing optional knowledge/model/notice owners.
# Input: Constructed backend, CLI args and Settings; no product identity decisions.
# Effects: Existing personal stores and model configuration; shared query root never imports this module.
import os
from pathlib import Path

from ..ai_client import AIClient
from ..config import CONFIG_ROOT
from ..sources.knowledge_embeddings import LocalBgeEncoder
from .knowledge_service import KnowledgeService
from .model_runtime import ModelRuntime
from .notice_gateway import NoticeGateway
from .notice_reports import NoticeReports, RELEASE_SOURCE


def configure_personal(backend, args, settings):
    backend.knowledge = KnowledgeService(settings.knowledge_dir or CONFIG_ROOT/'knowledge'/'on',
        Path(args.state_dir)/'knowledge-v1.sqlite3', LocalBgeEncoder(settings.knowledge_model_dir))
    backend.knowledge.observe()
    backend.model_runtime = ModelRuntime(backend, Path(args.state_dir)/'usage-v1.sqlite3')
    if settings.update_notices:
        notice_ai = AIClient(settings.ai_base_url, settings.ai_api_key, settings.ai_model,
                            settings.ai_reasoning_effort, settings.ai_openai_proxy) if settings.ai_api_key else None
        reports = NoticeReports(CONFIG_ROOT, settings.cache_file.with_name('update-notices.sqlite3'), args.app_id,
                                source=Path(os.getenv('OURNOTES_RELEASE_SOURCE', str(RELEASE_SOURCE))), ai_client=notice_ai)
        backend.notices = NoticeGateway(backend, reports)
