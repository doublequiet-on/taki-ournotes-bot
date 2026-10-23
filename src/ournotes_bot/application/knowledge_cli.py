# L3: Local knowledge validation/import/retrieval CLI; no game-data or chat initialization.
# Pos: Application operator tooling. Imports need supplied delivery/approval records and exclusive content ownership.
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

from ..domain.knowledge import KnowledgeError, KnowledgeScope
from ..sources.knowledge_files import decode_json, eligible, read_text, scan_directory, validate_delivery
from ..sources.knowledge_embeddings import LocalBgeEncoder
from .knowledge_service import KnowledgeService


def main():
    parser = argparse.ArgumentParser(description='Taki 已审核资料工程工具（格式校验不代表事实审核）')
    parser.add_argument('action', choices=('validate', 'sync', 'retrieve', 'import'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--state', type=Path)
    parser.add_argument('--model-dir', type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--delivery', type=Path)
    parser.add_argument('--query')
    parser.add_argument('--server', default='jp', choices=('jp', 'intl'))
    parser.add_argument('--version')
    parser.add_argument('--date')
    args = parser.parse_args()
    service = None
    try:
        if args.action == 'validate':
            scan = scan_directory(args.root)
            if args.delivery:
                validate_delivery(scan, decode_json(read_text(args.delivery)))
            result = {'code': 'valid' if not scan.issues else 'invalid', 'approval_revision': scan.manifest.revision,
                      'documents': [{'id': d.id, 'file': d.file, 'content_sha256': d.content_sha256,
                                     'status': d.review_status} for d in scan.documents],
                      'eligible_documents': len(eligible(scan)), 'issues': scan.issues, 'fact_review': False}
        else:
            if args.state is None:
                parser.error('--state is required for sync/retrieve/import')
            service = KnowledgeService(args.root, args.state, LocalBgeEncoder(args.model_dir))
            if args.action == 'import':
                if not args.source or not args.delivery:
                    parser.error('import requires --source and --delivery from the content owner')
                result = service.import_confirmed(args.source, decode_json(read_text(args.delivery)))
            else:
                service.refresh()
                result = service.status()
                if args.action == 'retrieve':
                    retrieval = service.retrieve(args.query, KnowledgeScope(server=args.server, version=args.version, date=args.date),
                                                 service.approved_set_revision)
                    result = {'code': retrieval.code, 'message': retrieval.message, 'evidence': asdict(retrieval.lease)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result['code'] in ('invalid', 'unavailable', 'index_pending'):
            raise SystemExit(1)
    except (KnowledgeError, OSError, sqlite3.Error) as exc:
        code = str(exc) if isinstance(exc, KnowledgeError) else 'knowledge_io_failed'
        result = {'code': 'invalid', 'error': code}
        if service is not None:
            result['knowledge'] = service.status()
        print(json.dumps(result, ensure_ascii=False))
        raise SystemExit(1) from None
    finally:
        if service is not None:
            service.close()


if __name__ == '__main__':
    main()
