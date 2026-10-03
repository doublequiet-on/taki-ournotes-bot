"""Multiple rank parsing, captured history and truthful trend geometry."""
import io
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from ournotes_bot.query.event_cutoff_query import CutoffRequest, CutoffAnswer, parse_cutoff, execute_cutoff
from ournotes_bot.structured_query import QuerySpec
from ournotes_bot.sources.moenotes_events import EventCutoffRepository
from ournotes_bot.sources.cutoff_history import CutoffHistory, HistoryPoint, HistoryView
from ournotes_bot.rendering.cutoff_trends import coordinates, segments
from ournotes_bot.rendering.event_cutoff_visuals import render_cutoff
from ournotes_bot.platforms.qq.qq import PreparedReply, _expand_replies
from test_event_cutoffs import PublicFixture


class TrendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = PublicFixture()
        self.source = EventCutoffRepository(Path(self.tmp.name) / "current", transport=self.fx,
                                           clock=lambda: self.fx.now, monotonic=lambda: self.fx.now)
        self.source.history = CutoffHistory(Path(self.tmp.name) / "history.sqlite3", min_free_mb=0)
        self.repo = SimpleNamespace(event_cutoffs=self.source)

    def query(self, query=""):
        return execute_cutoff(parse_cutoff("/查榜线 " + query), self.repo)

    def test_matrix_and_canonical_public_server(self):
        for query, count, ranks in (("", 3, (1, 2, 3, 10, 100)), ("100", 3, (100,)),
                                    ("t37", 3, (37,)), ("夢我夢中", 1, (1, 2, 3, 10, 100)),
                                    ("夢我夢中 t37,T10，T1 T37", 1, (1, 10, 37)),
                                    ("hk 夢我夢中 T100 T50 T3 T2 T1", 1, (1, 2, 3, 50, 100))):
            with self.subTest(query=query):
                answer = self.query(query)
                self.assertEqual(answer.status, "success", answer.text)
                self.assertEqual(len(answer.boards), count)
                self.assertEqual(answer.ranks, ranks)
                spec = QuerySpec("event_cutoff", cutoff_request=answer.request)
                again = execute_cutoff(parse_cutoff(spec.command_label()), self.repo)
                self.assertEqual(again.request, answer.request)
                self.assertNotIn(" tw ", spec.command_label())

    def test_invalid_ranks_are_rejected_before_dedup(self):
        for query in ("T0", "T-1", "T1.0", "T101", "T1 T2 T3 T4 T5 T6", "T1 2", "T1 T1 T0"):
            with self.subTest(query=query):
                self.assertEqual(self.query(query).status, "invalid_arguments")
        self.assertEqual(execute_cutoff(CutoffRequest(rank=True), self.repo).status, "invalid_arguments")
        self.assertEqual(execute_cutoff(CutoffRequest(ranks=(37, 1, 37)), self.repo).ranks, (1, 37))

    def test_entity_precedence_with_numeric_name(self):
        answer = self.query("123 T1 T37")
        self.assertEqual(len(answer.boards), 1)
        self.assertEqual(answer.boards[0].song.title, "123")
        self.assertEqual(self.query("长歌名 37 T100").boards[0].song.title, "长歌名 37")
        self.assertEqual(self.query("123").ranks, (1, 2, 3, 10, 100))

    def test_numeric_only_skips_history_read_and_all_drawing(self):
        with patch.object(self.source.history, "read", side_effect=AssertionError("history read")):
            answer = self.query("T100 仅数值")
        with patch("ournotes_bot.rendering.event_cutoff_visuals.v._background", side_effect=AssertionError("draw")):
            self.assertEqual(render_cutoff(answer), ())
        self.assertEqual(len(answer.boards), 3)
        self.assertIn("T100", answer.text)

    def test_geometry_keeps_gaps_flat_down_and_big_integers(self):
        view = HistoryView((HistoryPoint(1000, (100,), 1000), HistoryPoint(3000, (100,), 3000),
                            HistoryPoint(4000, (90,), 4000), HistoryPoint(5000, (None,), 5000),
                            HistoryPoint(6000, (80,), 6000), HistoryPoint(20000, (70,), 20000, True)))
        self.assertEqual(segments(view, 0), (((1000, 100), (3000, 100), (4000, 90)), ((6000, 80),), ((20000, 70),)))
        base = 10**40
        self.assertEqual(coordinates(20, base + 1, (0, 100, base, base + 2), (0, 0, 100, 100)), (20, 50))

    def test_default_per_song_cards_and_single_point_are_complete(self):
        answer = self.query()
        pages = render_cutoff(answer, preview_label="合成历史测试")
        self.assertEqual(len(pages), 3)
        for page in pages:
            self.assertIn("不足以形成曲线", page.text)
            with Image.open(io.BytesIO(page.image)) as image:
                self.assertLessEqual(image.width * image.height, 12000000)
            self.assertLessEqual(len(page.image), 1500000)
            for rank in answer.ranks:
                self.assertIn(f"T{rank}：", page.text)

    def test_reply_budget_keeps_every_song_and_rank(self):
        answer = self.query()
        pages = render_cutoff(answer)
        prepared = PreparedReply(answer.text, pages=tuple(PreparedReply(p.text, p.image) for p in pages))
        replies = _expand_replies([prepared, PreparedReply("other"), PreparedReply("other2")], reserve=1)
        self.assertLessEqual(len(replies), 4)
        text = "\n".join(p.text for p in replies)
        self.assertIn("容量不足", text)
        for b in answer.boards:
            self.assertIn(b.song.title, text)
            self.assertIn(str(b.score(100)), text)


if __name__ == "__main__":
    unittest.main()
