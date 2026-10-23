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

    def write_fixture(self, number=1, *, server='jp', version=None, text='隔离测试内容，不代表任何游戏事实。'):
        import json
        from ournotes_bot.sources.knowledge_files import parse_document
        from ournotes_bot.application.knowledge_service import atomic_text
        meta = dict(schema='taki-knowledge/1', id='ci-fixture', title='工程测试', keywords=['工程测试'], aliases=[],
            game='bang-dream-our-notes', servers=[server], applicability=dict(mode='current', game_version=version,
            valid_from=None, valid_to=None, scope_note='仅隔离工程测试'), sources=[dict(source_id='fixture',
            kind='fixture', uri='fixture://local-only', locator='isolated-ci', accessed_at='2026-10-23')],
            evidence_type='reviewed_analysis', review_status='approved', reviewed_by='ISOLATED-FIXTURE',
            reviewed_at='2026-10-23', limitations=['合成测试，非正式百科'])
        content = '---\n'+json.dumps(meta, ensure_ascii=False)+'\n---\n'+text+'\n'
        doc = parse_document(content)
        approvals = dict(schema='taki-knowledge-approvals/1', revision=number, revoked=[], approved=[dict(
            id=doc.id, content_sha256=doc.content_sha256, approval_revision=number, approved_by=doc.reviewed_by,
            approved_at=doc.reviewed_at, confirmation_ref=f'fixture://isolated/{number}')])
        atomic_text(self.service.root/'ci-fixture.md', content)
        atomic_text(self.service.root/'approvals.json', json.dumps(approvals))
        return content, approvals

    def retrieve(self, **scope):
        from ournotes_bot.domain.knowledge import KnowledgeScope
        return self.service.retrieve('工程测试', KnowledgeScope(**scope), self.service.approved_set_revision)

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
