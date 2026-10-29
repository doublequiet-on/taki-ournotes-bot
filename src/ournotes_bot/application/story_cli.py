# L3: Local story operator CLI; no online model, game initialization or QQ access.
import argparse
import json
from pathlib import Path

from ..domain.story import StoryError
from ..sources.story_files import parse_bundle,read_json
from .story_service import StoryService


def main():
    parser=argparse.ArgumentParser(description='Taki 场景原文导入、检索、撤回与人工人格发布')
    parser.add_argument('action',choices=('validate','import','status','search','read','withdraw','withdraw-persona','rollback','approve-persona'))
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--bundle',type=Path)
    parser.add_argument('--allow-fixture',action='store_true',help='仅限隔离验收目录；合成资料不能批准人格')
    parser.add_argument('--query');parser.add_argument('--handle');parser.add_argument('--source-id');parser.add_argument('--reason')
    parser.add_argument('--revision');parser.add_argument('--persona-id');parser.add_argument('--reviewer');parser.add_argument('--approval-ref')
    parser.add_argument('--confirm-human-review',action='store_true')
    args=parser.parse_args();service=StoryService(args.root)
    try:
        if args.action in ('validate','import'):
            if args.bundle is None:parser.error('--bundle is required')
            if args.action=='validate':
                bundle=parse_bundle(read_json(args.bundle),allow_fixture=args.allow_fixture)
                result={'status':'valid','source_revision':bundle.revision,'sources':len(bundle.sources),'scenes':len(bundle.scenes),'fact_review':False}
            else:result=service.import_bundle(args.bundle,allow_fixture=args.allow_fixture)
        elif args.action=='status':result=service.status()
        elif args.action=='search':result=service.search(args.query)
        elif args.action=='read':result=service.read(args.handle)
        elif args.action=='withdraw':result=service.withdraw(args.source_id,args.reason)
        elif args.action=='withdraw-persona':result=service.withdraw_persona(args.persona_id,args.reason)
        elif args.action=='rollback':result=service.rollback(args.revision)
        else:
            if not args.confirm_human_review:parser.error('approve-persona requires --confirm-human-review from the human content owner')
            result=service.approve_persona(args.persona_id,args.reviewer,args.approval_ref,revision=args.revision)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (StoryError,OSError) as exc:
        print(json.dumps({'status':'error','error_code':str(exc) if isinstance(exc,StoryError) else 'story_io_failed'}))
        raise SystemExit(1) from None


if __name__=='__main__':main()
