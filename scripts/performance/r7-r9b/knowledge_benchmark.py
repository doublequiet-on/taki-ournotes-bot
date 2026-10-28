"""Local synthetic authority benchmark; no production content or model access."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace


def worker(args):
    sys.path.insert(0, str(args.repo / 'src'))
    from ournotes_bot.application import knowledge_service as ks
    from ournotes_bot.application.backend import Backend
    from ournotes_bot.domain.knowledge import EvidenceLease, KnowledgeScope
    from ournotes_bot.sources.knowledge_files import parse_document
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        service = ks.KnowledgeService(root/'content', root/'state.sqlite3',
                                     SimpleNamespace(identity='fixture-only', dimensions=2))
        approvals = []
        digest = hashlib.sha256()
        for index in range(args.documents):
            meta = dict(schema='taki-knowledge/1', id=f'fixture-{index}', title='工程夹具',
                        keywords=['工程'], aliases=[], game='bang-dream-our-notes', servers=['jp'],
                        applicability=dict(mode='current', game_version=None, valid_from=None,
                                           valid_to=None, scope_note='not game facts'),
                        sources=[dict(source_id='fixture', kind='fixture', uri='fixture://local',
                                      locator='isolated', accessed_at='2026-10-27')],
                        evidence_type='reviewed_analysis', review_status='approved',
                        reviewed_by='FIXTURE', reviewed_at='2026-10-27', limitations=['not game facts'])
            body = '\n\n'.join(f'## Section {section}\n' + ('隔离工程素材。'*48) for section in range(8))
            text = '---\n'+json.dumps(meta, ensure_ascii=False)+'\n---\n'+body+'\n'
            doc = parse_document(text)
            raw = text.encode(); digest.update(raw)
            (service.root/f'{doc.id}.md').write_bytes(raw)
            approvals.append(dict(id=doc.id, content_sha256=doc.content_sha256, approval_revision=1,
                                  approved_by=doc.reviewed_by, approved_at=doc.reviewed_at,
                                  confirmation_ref='fixture://approved/'+doc.id))
        manifest = json.dumps(dict(schema='taki-knowledge-approvals/1', revision=1,
                                   approved=approvals, revoked=[])).encode()
        (service.root/'approvals.json').write_bytes(manifest); digest.update(manifest)
        service.observe(); assert service.available
        chunks = tuple(ks.split_document(*service.allowed['fixture-0']))[:args.chunks]
        lease = EvidenceLease(service.approved_set_revision, KnowledgeScope(), chunks)
        backend = object.__new__(Backend)
        backend.knowledge=service; backend.runtime='r'; backend.clock=lambda:10
        backend.ledger=SimpleNamespace(active=lambda epoch:True)
        backend.works={'d':SimpleNamespace(active=True, hint=False, prepared=[
            SimpleNamespace(evidence=(lease,), evidence_deadline=None) for _ in range(args.parts)])}
        row=dict(delivery='d', status='prepared', epoch='e', runtime='r', expires=20)
        counts=dict(observe=0, split=0); timing=dict(observe_ms=0., split_ms=0., lock_wait_ms=0.)
        original_observe=service.observe; original_split=ks.split_document; original_lock=service.lock
        class TimedLock:
            def __enter__(self):
                started=time.perf_counter(); original_lock.acquire()
                timing['lock_wait_ms']+=(time.perf_counter()-started)*1000
            def __exit__(self,*_): original_lock.release()
        service.lock=TimedLock()
        def observe():
            counts['observe']+=1; started=time.perf_counter()
            try:return original_observe()
            finally:timing['observe_ms']+=(time.perf_counter()-started)*1000
        def split(*params):
            counts['split']+=1; started=time.perf_counter()
            try:yield from original_split(*params)
            finally:timing['split_ms']+=(time.perf_counter()-started)*1000
        service.observe=observe; ks.split_document=split
        times=[]; cpu=time.process_time()
        for _ in range(5):
            started=time.perf_counter(); assert backend.live(row,'e')
            times.append((time.perf_counter()-started)*1000)
        cpu=time.process_time()-cpu
        service.close()
        result=dict(documents=args.documents,chunks=args.chunks,parts=args.parts,iterations=5,
                    elapsed_ms=statistics.median(times),samples_ms=times,cpu_seconds=cpu,
                    counters=counts,timings=timing,input_sha256=digest.hexdigest(),
                    python=platform.python_version(),sqlite=sqlite3.sqlite_version,
                    source_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=args.repo,text=True).strip(),
                    source_sha256=hashlib.sha256(b''.join((args.repo/path).read_bytes() for path in
                      ['src/ournotes_bot/application/knowledge_service.py','src/ournotes_bot/application/backend.py',
                       'src/ournotes_bot/application/model_runtime.py'])).hexdigest())
        args.output.write_text(json.dumps(result),encoding='utf-8')


def sample(pid):
    try:
        fields=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
        return int(fields[21])*os.sysconf('SC_PAGE_SIZE')
    except (FileNotFoundError,ProcessLookupError):return 0


def run(args):
    args.output.mkdir(parents=True,exist_ok=True)
    cgroup=Path('/sys/fs/cgroup')/next(line.split(':',2)[2].lstrip('/') for line in
              Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0:'))
    limits={name:(cgroup/name).read_text().strip() for name in ('cpu.max','memory.max','memory.swap.max')}
    q,p=limits['cpu.max'].split();assert int(q)/int(p)==4
    assert limits['memory.max']=='4294967296' and limits['memory.swap.max']=='0'
    (args.output/'limits.json').write_text(json.dumps(limits,indent=2))
    rows=[]
    for docs in (5,50,200):
        for chunks in (1,6):
            for parts in (1,5):
                for rnd in range(5):
                    for turn,label in enumerate('ABBA'):
                        name=f'd{docs}-c{chunks}-p{parts}-r{rnd}-{turn}-{label}'
                        output=args.output/(name+'.json')
                        command=[sys.executable,'-B',__file__,'--worker','--repo',str(args.base if label=='A' else args.new),
                                 '--documents',str(docs),'--chunks',str(chunks),'--parts',str(parts),'--output',str(output)]
                        with (args.output/(name+'.log')).open('w') as log:
                            process=subprocess.Popen(command,stdout=log,stderr=log);rss=0
                            while process.poll() is None:
                                rss=max(rss,sample(process.pid));time.sleep(.005)
                            assert process.returncode==0,name
                        row=json.loads(output.read_text());row.update(round=rnd,turn=turn,label=label,peak_rss_bytes=rss)
                        rows.append(row)
                        (args.output/'runs.json').write_text(json.dumps(rows,indent=2))
                print('DONE',docs,chunks,parts,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--worker',action='store_true')
    for name in ('repo','base','new','output'):parser.add_argument('--'+name,type=Path)
    for name in ('documents','chunks','parts'):parser.add_argument('--'+name,type=int)
    args=parser.parse_args()
    worker(args) if args.worker else run(args)
