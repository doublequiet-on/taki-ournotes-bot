"""Install identical benchmark-only harness edits in disposable Linux clones."""
import hashlib,json,shutil,sys
from pathlib import Path
root=Path(sys.argv[1]);source=Path(__file__).resolve().parent
for name in ('base','integrated'):
    repo=root/name
    shutil.copyfile(source/'native_fixture.py',repo/'scripts/native_fixture.py')
    p=repo/'scripts/benchmark_queries.py';s=p.read_text()
    assert 'native_fixture' not in s,'do not reapply experiment edits'
    s=s.replace('        acquire = backend.gate._workers.acquire',
        '        from native_fixture import install\n        install(backend,state,measured)\n        acquire = backend.gate._workers.acquire')
    s=s.replace("'pending_workers': backend.gate._pending}","'pending_workers': backend.gate._pending, 'model': backend.models.status(), 'usage': [dict(row) for row in backend.model_runtime.usage.db.execute('SELECT request_id,state,charged FROM attempts')], 'evidence_chunks': [len(lease.chunks) for work in backend.works.values() for reply in work.prepared for lease in reply.evidence]}")
    s=s.replace('no real QQ or model.', 'real ModelRuntime with 20ms synthetic provider; no real QQ/provider; 50 synthetic knowledge documents; per-request fixture users.')
    p.write_text(s)
    p=repo/'koishi/scripts/benchmark-queries.cjs';s=p.read_text()
    s=s.replace("  light: () => '/帮助',", "  knowledge: () => '/问 工程规则是什么意思',\n  'knowledge-mixed': i => i % 2 ? '/问 工程规则是什么意思' : '/查曲 100091',\n  'card-typed': i => i % 2 ? '/问 紫色R成员卡有哪些' : '/查卡 颜色=紫色 稀有度=R',\n  light: () => '/帮助',")
    s=s.replace("author: { id: 'benchmark-user' }", "author: { id: 'benchmark-user-'+id }")
    s=s.replace("'benchmark-group', 'benchmark-user', sample.id", "'benchmark-group', 'benchmark-user-'+sample.id, sample.id")
    s=s.replace("s.command === '/帮助' ||", "s.command === '/帮助' || s.command === '/问 工程规则是什么意思' && s.business?.length && s.business.every(p => p.business_status === 'success') ||")
    s=s.replace("s.command !== '/帮助' &&", "s.command !== '/帮助' && s.command !== '/问 工程规则是什么意思' &&")
    p.write_text(s)
manifest={name:{path:hashlib.sha256((root/name/path).read_bytes()).hexdigest() for path in
    ('scripts/benchmark_queries.py','scripts/native_fixture.py','koishi/scripts/benchmark-queries.cjs')} for name in ('base','integrated')}
assert manifest['base']==manifest['integrated']
(root/'evidence/chain-harness.json').write_text(json.dumps(manifest,indent=2))
