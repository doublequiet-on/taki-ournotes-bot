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
            from ournotes_bot.rendering.cutoff_trends import segments

            with tempfile.TemporaryDirectory() as folder:
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


if __name__ == "__main__":
    unittest.main()
