"""Core queries and rendering must work without importing the QQ integration."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


class PlatformBoundaryTests(unittest.TestCase):
    def test_core_works_without_qq_or_credentials(self):
        # A fresh interpreter prevents other tests' imports from hiding a dependency.
        probe = textwrap.dedent("""
            import importlib.abc
            import io
            from pathlib import Path
            import sys
            import tempfile
            from unittest.mock import patch

            class NoQQ(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname == 'botpy' or fullname.startswith('botpy.') or fullname in {
                        'ournotes_bot.qq', 'ournotes_bot.menu',
                        'ournotes_bot.platforms', 'ournotes_bot.platforms.qq',
                        'ournotes_bot.platforms.qq.qq', 'ournotes_bot.platforms.qq.menu', 'ournotes_bot.update_notice',
                    }:
                        raise AssertionError('Core imported QQ integration: ' + fullname)

            sys.meta_path.insert(0, NoQQ())

            def offline(event, args):
                if event in {'socket.connect', 'socket.getaddrinfo'}:
                    raise AssertionError('Boundary test must stay offline')

            sys.addaudithook(offline)
            from PIL import Image
            from ournotes_bot.ai_query import AIQueryParser
            from ournotes_bot.sources.chart_data import score_name
            from ournotes_bot.commands import CommandResult, handle_command, resolve_command
            from ournotes_bot.config import Settings
            from ournotes_bot.data import Chart, Song, SongRepository
            from ournotes_bot.structured_query import QueryResult, QuerySpec, resolve_query
            from ournotes_bot.visuals import render_song_list
            from ournotes_bot.query.continuation import capture_context, execute_followup, Operation
            from ournotes_bot.query.field_query import parse_field_question
            from ournotes_bot.sources.cutoff_history import CutoffHistory
            from ournotes_bot.sources.cutoff_sampler import HistorySampler
            from ournotes_bot.application.knowledge_service import KnowledgeService
            from ournotes_bot.sources.knowledge_embeddings import LocalBgeEncoder
            from ournotes_bot.domain.knowledge import KnowledgeScope
            from ournotes_bot.rendering.cutoff_trends import segments

            with tempfile.TemporaryDirectory() as folder:
                knowledge = KnowledgeService(Path(folder)/'knowledge', Path(folder)/'knowledge.sqlite3', LocalBgeEncoder(None))
                try:
                    assert knowledge.refresh()['code'] == 'empty'
                    assert knowledge.retrieve('资料', KnowledgeScope(), knowledge.approved_set_revision).message == '尚无已审核资料。'
                    assert 'onnxruntime' not in sys.modules and 'tokenizers' not in sys.modules
                finally:
                    knowledge.close()
                repo = SongRepository('https://bdon.yatta.moe', Path(folder) / 'cache.json')
                song = Song(100001, '迷星叫', ('迷星叫',), 'MyGO!!!!!', '', '', '', '', '',
                            (Chart('EXPERT', 25, 25.0, 768, '0001/0001_03'),))
                repo.songs = [song]
                direct = resolve_command('/查曲 迷星叫', repo)
                assert isinstance(direct, CommandResult)
                assert list(direct.songs) == [song]
                context = capture_context(direct, repo)
                detail, _ = execute_followup(context, Operation('select', 1), repo)
                assert detail.chart[0].id == song.id
                short = resolve_query(parse_field_question('迷星叫 EX Note', repo), repo)
                assert '768 Notes' in short.short_text
                assert HistorySampler(repo.event_cutoffs).task is None
                assert '迷星叫' in handle_command('/查曲 迷星叫', repo, resolved=direct)
                result = resolve_query(QuerySpec(intent='song', difficulty='EXPERT',
                                                 comparison='>=', level=25), repo)
                assert isinstance(result, QueryResult)
                assert list(result.songs) == [song]
                empty = resolve_query(QuerySpec(intent='song', comparison='>=', level=99), repo)
                assert not empty.songs
                parser = AIQueryParser(Settings('', '', repo.data_base, repo.cache_file, 6))
                with patch.object(parser, '_request', side_effect=AssertionError('Unexpected model call')) as request:
                    answer, plan = parser.answer_with_plan('/问 MyGO的歌有哪些', repo)
                    assert '迷星叫' in answer
                    assert isinstance(plan, QueryResult)
                    assert list(plan.songs) == [song]
                    request.assert_not_called()
                assert score_name(song, song.charts[0]) == '0001/0001_03'
                with patch('ournotes_bot.visuals._asset', return_value=None):
                    picture = render_song_list(list(result.songs), 'MyGO!!!!!')
                with Image.open(io.BytesIO(picture)) as rendered:
                    rendered.verify()
        """)
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ, PYTHONPATH=str(root / "src"),
                   QQ_APP_ID="", QQ_APP_SECRET="", AI_API_KEY="")
        result = subprocess.run([sys.executable, "-B", "-c", probe], cwd=root,
                                env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class KnowledgeAuthorityTests(unittest.TestCase):
    """Synthetic content stays in TemporaryDirectory, never knowledge/on."""

    def setUp(self):
        import tempfile
        from ournotes_bot.application.knowledge_service import KnowledgeService
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        class FixtureEncoder:
            identity, dimensions, fail = 'isolated-ci-vector/1', 2, False
            def encode(self, texts, *, query=False):
                if self.fail and not query:
                    raise RuntimeError('isolated build failure')
                return tuple((1., 0.) for _ in texts)
        self.encoder = FixtureEncoder()
        self.service = KnowledgeService(self.root/'content', self.root/'state.sqlite3', self.encoder)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def write_fixture(self, number=1, *, server='jp', version=None, text='隔离测试内容，不代表任何游戏事实。', doc_id='ci-fixture', valid_to=None):
        import json
        from ournotes_bot.sources.knowledge_files import parse_document
        from ournotes_bot.application.knowledge_service import atomic_text
        meta = dict(schema='taki-knowledge/1', id=doc_id, title='工程测试', keywords=['工程测试'], aliases=[],
            game='bang-dream-our-notes', servers=list(server) if isinstance(server,tuple) else [server], applicability=dict(mode='current', game_version=version,
            valid_from='2026-01-01' if valid_to else None, valid_to=valid_to, scope_note='仅隔离工程测试'), sources=[dict(source_id='fixture',
            kind='fixture', uri='fixture://local-only', locator='isolated-ci', accessed_at='2026-10-23')],
            evidence_type='reviewed_analysis', review_status='approved', reviewed_by='ISOLATED-FIXTURE',
            reviewed_at='2026-10-23', limitations=['合成测试，非正式百科'])
        content = '---\n'+json.dumps(meta, ensure_ascii=False)+'\n---\n'+text+'\n'
        doc = parse_document(content)
        approvals = dict(schema='taki-knowledge-approvals/1', revision=number, revoked=[], approved=[dict(
            id=doc.id, content_sha256=doc.content_sha256, approval_revision=number, approved_by=doc.reviewed_by,
            approved_at=doc.reviewed_at, confirmation_ref=f'fixture://isolated/{number}')])
        atomic_text(self.service.root/(doc_id+'.md'), content)
        atomic_text(self.service.root/'approvals.json', json.dumps(approvals))
        return content, approvals

    def retrieve(self, **scope):
        from ournotes_bot.domain.knowledge import KnowledgeScope
        return self.service.retrieve('工程测试', KnowledgeScope(**scope), self.service.approved_set_revision)

    def test_one_boundary_observes_once_and_splits_each_referenced_document_once(self):
        import time
        from types import SimpleNamespace
        from unittest.mock import patch
        from ournotes_bot.application import knowledge_service as ks
        from ournotes_bot.application.backend import Backend
        from ournotes_bot.domain.knowledge import EvidenceLease, KnowledgeScope
        self.write_fixture(text='## 工程测试\n'+'隔离测试内容。'*300)
        self.service.refresh()
        chunks=tuple(row.chunk for row in self.service.index[:6])
        self.assertEqual(len(chunks),6)
        lease=EvidenceLease(self.service.approved_set_revision,KnowledgeScope(),chunks)
        backend=object.__new__(Backend)
        backend.knowledge=self.service;backend.runtime='runtime';backend.clock=lambda:10
        backend.ledger=SimpleNamespace(active=lambda epoch:True)
        row=dict(delivery='delivery',status='prepared',epoch='epoch',runtime='runtime',expires=20)
        for count in (1,5):
            work=SimpleNamespace(active=True,hint=False,chat=False,prepared=[
                SimpleNamespace(evidence=(lease,lease),evidence_deadline=None) for _ in range(count)])
            backend.works={'delivery':work}
            with patch.object(self.service,'observe',wraps=self.service.observe) as observe, \
                    patch.object(ks,'split_document',wraps=ks.split_document) as split:
                self.assertTrue(backend.live(row,'epoch'))
                self.assertEqual(observe.call_count,1)
                self.assertEqual(split.call_count,1)
            work.prepared[-1].evidence_deadline=time.monotonic()-1
            self.assertFalse(backend.live(row,'epoch'))
        work.prepared=[SimpleNamespace(evidence=(lease,),evidence_deadline=20)]
        now=[10]
        original=self.service.observe
        def observed_after_deadline():
            result=original();now[0]=21;return result
        with patch.object(self.service,'observe',side_effect=observed_after_deadline), \
                patch('ournotes_bot.application.backend.time.monotonic',side_effect=lambda:now[0]):
            self.assertFalse(backend.live(row,'epoch'))
        work.prepared=[SimpleNamespace(evidence=(),evidence_deadline=None)]
        with patch.object(self.service,'observe',wraps=self.service.observe) as observe:
            self.assertTrue(backend.live(row,'epoch'));observe.assert_not_called()
        # A later checkpoint must reobserve even in the same HTTP event.
        (self.service.root/'ci-fixture.md').unlink()
        self.assertFalse(backend.valid_evidence((lease,)))

    def test_batch_compares_complete_members_and_all_scopes(self):
        from dataclasses import replace
        from ournotes_bot.domain.knowledge import KnowledgeScope
        self.write_fixture();self.service.refresh();lease=self.retrieve().lease
        chunk=lease.chunks[0]
        changes=dict(text=chunk.text+'changed',chunk_sha256='0'*64,chunk_id='not-a-member',
                     source_refs=(),applicability=replace(chunk.applicability,scope_note='changed'),
                     evidence_type='changed',limitations=(),servers=('intl',),
                     content_sha256='0'*64,approval_revision=999)
        for key,value in changes.items():
            with self.subTest(field=key):
                forged=replace(lease,chunks=(replace(chunk,**{key:value}),))
                self.assertFalse(self.service.validate_current((lease,forged)))
                self.assertFalse(self.service.validate_current((forged,)))
        self.assertFalse(self.service.validate_current((lease,replace(lease,scope=KnowledgeScope(server='common')))))
        self.assertTrue(self.service.validate_current((lease,replace(lease,approved_set_revision='older-unrelated-set'))))

    def test_empty_collection_and_empty_lease_have_different_authority_requirements(self):
        from unittest.mock import patch
        from ournotes_bot.domain.knowledge import EvidenceLease,KnowledgeScope
        self.service.close()
        with patch.object(self.service,'observe',wraps=self.service.observe) as observe:
            self.assertTrue(self.service.validate_current(()));observe.assert_not_called()
            self.assertFalse(self.service.validate_current((EvidenceLease('',KnowledgeScope(),()),)))
            self.assertEqual(observe.call_count,1)
        self.assertFalse(self.service.valid(None))

    def test_batch_rechecks_utc_date_but_keeps_explicit_historical_scope(self):
        from dataclasses import replace
        from datetime import datetime,timezone
        from ournotes_bot.domain.knowledge import KnowledgeScope
        self.service.clock=lambda:datetime(2026,10,27,23,59,tzinfo=timezone.utc).timestamp()
        self.write_fixture(valid_to='2026-10-27');self.service.refresh();lease=self.retrieve().lease
        self.assertTrue(self.service.validate_current((lease,)))
        self.service.clock=lambda:datetime(2026,10,28,tzinfo=timezone.utc).timestamp()
        self.assertFalse(self.service.validate_current((lease,)))
        self.assertTrue(self.service.validate_current((replace(lease,scope=KnowledgeScope(date='2026-10-27')),)))

    def test_unrelated_publication_and_failed_index_do_not_revoke_members(self):
        import json
        from unittest.mock import patch
        from ournotes_bot.application.knowledge_service import atomic_text
        from ournotes_bot.query import knowledge_retrieval
        _,first=self.write_fixture(text='隔离内容。'*200);self.service.refresh();lease=self.retrieve().lease
        _,second=self.write_fixture(2,doc_id='other-fixture')
        second['approved']+=first['approved']
        atomic_text(self.service.root/'approvals.json',json.dumps(second))
        self.encoder.fail=True;self.service.refresh()
        self.assertTrue(self.service.index_error)
        self.assertTrue(self.service.validate_current((lease,)))
        self.encoder.fail=False
        with patch.object(knowledge_retrieval,'MAX_CHUNKS',1):
            self.service.rebuild()
            self.assertEqual(self.service.index_error,'knowledge_chunk_limit')
            self.assertTrue(self.service.validate_current((lease,)))

    def test_failed_authority_save_remains_closed_through_pending_recovery(self):
        from ournotes_bot.application.knowledge_service import KnowledgeService
        self.write_fixture();self.service.refresh();lease=self.retrieve().lease
        self.service.db.execute('PRAGMA query_only=ON')
        (self.service.root/'ci-fixture.md').unlink()
        self.assertFalse(self.service.validate_current((lease,)))
        self.assertIsNotNone(self.service.pending_authority)
        self.write_fixture()  # Replacing an old file cannot bypass the pending floor.
        self.assertFalse(self.service.validate_current((lease,)))
        self.service.db.execute('PRAGMA query_only=OFF')
        self.assertFalse(self.service.validate_current((lease,)))
        self.assertIsNone(self.service.pending_authority)
        self.service.close()
        self.service=KnowledgeService(self.root/'content',self.root/'state.sqlite3',self.encoder)
        self.assertFalse(self.service.validate_current((lease,)))
        self.write_fixture(2);self.service.refresh()
        self.assertTrue(self.service.valid(self.retrieve().lease))

    def test_deleted_capture_stays_invalid_after_failed_index_and_restart(self):
        from ournotes_bot.application.knowledge_service import KnowledgeService
        from ournotes_bot.domain.knowledge import KnowledgeError
        self.write_fixture(); self.service.refresh()
        captured = self.retrieve().lease
        self.assertTrue(captured.chunks)
        (self.service.root/'ci-fixture.md').unlink()
        self.service.observe()
        self.encoder.fail = True
        self.service.rebuild()
        self.assertFalse(self.service.valid(captured))
        with self.assertRaises(KnowledgeError): self.service.prepare_evidence(captured)
        self.write_fixture()
        self.service.close()
        self.service = KnowledgeService(self.root/'content', self.root/'state.sqlite3', self.encoder)
        self.service.refresh()
        self.assertEqual(self.retrieve().code, 'empty')
        self.encoder.fail = False
        self.write_fixture(2); self.service.refresh()
        self.assertEqual(self.retrieve().code, 'ok')

    def test_current_scope_filters_before_retrieval_and_bounds_evidence(self):
        from ournotes_bot.query.knowledge_retrieval import evidence_json
        self.write_fixture(server='intl', version='fixture-v1', text='隔离正文。'*200)
        self.service.refresh()
        self.assertEqual(self.retrieve().code, 'missing')
        self.assertEqual(self.retrieve(server='intl').code, 'missing')
        result = self.retrieve(server='intl', version='fixture-v1')
        self.assertEqual(result.code, 'ok')
        self.assertLessEqual(len(result.lease.chunks), 6)
        self.assertLessEqual(len(evidence_json(result.lease.chunks)), 6000)

    def test_common_scope_requires_both_servers_and_rechecks_lease(self):
        from dataclasses import replace
        from ournotes_bot.domain.knowledge import KnowledgeScope
        self.write_fixture();self.service.refresh()
        jp=self.retrieve().lease
        self.assertEqual(self.retrieve(server='common').code,'missing')
        self.assertFalse(self.service.valid(replace(jp,scope=KnowledgeScope(server='common'))))
        self.write_fixture(2,server=('jp','intl'));self.service.refresh()
        common=self.retrieve(server='common')
        self.assertEqual(common.code,'ok');self.assertTrue(self.service.valid(common.lease))
        self.write_fixture(3);self.service.refresh()
        self.assertFalse(self.service.valid(common.lease))

    def test_sections_preserve_heading_budget_and_deduplicate_only_metadata(self):
        import json
        from dataclasses import asdict
        from ournotes_bot.query.knowledge_retrieval import evidence_json,complete_sections,bounded
        self.write_fixture(text='## 入门\n\n'+'参与流程。'*75+'\n\n## 公式边界\n\n未知项。')
        self.service.refresh();rows=self.service.index
        chunks=[r.chunk for r in rows]
        self.assertTrue(all(len(c.text)<=360 for c in chunks))
        first=[c for c in chunks if '参与流程' in c.text]
        self.assertGreater(len(first),1)
        self.assertTrue(all(c.text.startswith('## 入门') for c in first))
        result=bounded(complete_sections((first[0],),rows))
        self.assertFalse(any('未知项' in c.text for c in result))
        data=json.loads(evidence_json(result))
        self.assertEqual(len(data['documents']),1)
        self.assertEqual(len(data['passages']),len(result))
        for passage,chunk in zip(data['passages'],result):
            restored={**data['documents'][passage['document']],**{k:v for k,v in passage.items() if k!='document'}}
            self.assertEqual(restored,json.loads(json.dumps(asdict(chunk))))
        self.assertLess(len(evidence_json(result)),len(json.dumps([asdict(c) for c in result],ensure_ascii=False)))

    def test_table_rows_carry_their_own_source_column_labels(self):
        from ournotes_bot.query.knowledge_retrieval import labelled_tables
        text='|区间|首要比较|\n|---|---|\n|JUST|JUST数量|\n|COMBO|最大连击|\n'
        result=labelled_tables(text)
        self.assertIn('区间：COMBO；首要比较：最大连击',result)
        self.assertIn('区间：JUST；首要比较：JUST数量',result)
        ambiguous=text.replace('最大连击','有\\|转义')
        self.assertEqual(labelled_tables(ambiguous),ambiguous)

    def test_focus_search_keeps_scope_and_shared_evidence_budget(self):
        from ournotes_bot.domain.knowledge import KnowledgeScope,KnowledgeError
        self.write_fixture(server='intl',text='## 入门\n工程测试参与方法。\n## 区间\n工程测试区间。')
        self.service.refresh()
        r=self.service.retrieve('区间',KnowledgeScope(server='intl'),self.service.approved_set_revision,focus_query='参与方法')
        self.assertEqual(r.code,'ok');self.assertTrue(self.service.valid(r.lease))
        self.assertLessEqual(len(r.lease.chunks),6);self.assertLessEqual(len(self.service.prepare_evidence(r.lease)),6000)
        r=self.service.retrieve('区间',KnowledgeScope(),self.service.approved_set_revision,focus_query='参与方法')
        self.assertEqual(r.code,'missing')
        with self.assertRaises(KnowledgeError):
            self.service.retrieve('区间',KnowledgeScope(),self.service.approved_set_revision,focus_query='长'*501)

    def test_rollback_or_missing_manifest_closes_existing_evidence(self):
        self.write_fixture(2); self.service.refresh()
        lease = self.retrieve().lease
        self.write_fixture(1)
        self.assertFalse(self.service.valid(lease))
        self.assertEqual(self.service.error, 'approval_revision_rollback')
        (self.service.root/'approvals.json').unlink()
        self.assertFalse(self.service.valid(lease))

    def test_unconfirmed_file_never_enters_index(self):
        import json
        from ournotes_bot.application.knowledge_service import atomic_text
        self.write_fixture()
        atomic_text(self.service.root/'approvals.json', json.dumps(dict(schema='taki-knowledge-approvals/1',
            revision=1, approved=[], revoked=[])))
        self.service.refresh()
        self.assertEqual(self.retrieve().code, 'empty')
        self.assertEqual(self.service.index, ())


if __name__ == "__main__":
    unittest.main()
