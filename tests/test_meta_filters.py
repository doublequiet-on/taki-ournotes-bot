"""Shared filters applied to chart rows before ranking and pagination."""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from ournotes_bot.commands import resolve_command
from ournotes_bot.ai_query import AIQueryParser
from ournotes_bot.config import Settings
from ournotes_bot.data import Chart, Song, SongRepository
from ournotes_bot.query.efficiency_query import parse_efficiency, execute_efficiency
from ournotes_bot.sources.haneoka.song_traits import SongTraits
from ournotes_bot.sources.haneoka.song_meta import parse_payload
from test_song_meta import payload, STAMP


class MetaFilterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = SongRepository("https://bdon.yatta.moe", Path(self.tmp.name) / "cache.json")
        ident, songs, meta = payload(35)
        self.repo.songs = [Song(int(k), s["musicTitle"][0], tuple(s["musicTitle"]), "MyGO!!!!!", "", "", "", "", "",
                               (Chart("EXPERT", 25, 25, 100, ""),), traits=SongTraits(2, ("JUST", "JUST", "COMBO")))
                           for k, s in songs.items()]
        self.repo.song_meta = Mock()
        self.repo.song_meta.get.return_value = parse_payload(ident, songs, meta, STAMP)

    def answer(self, query):
        return resolve_command("/查分数表 " + query, self.repo).meta

    def test_shared_modes_or_and_order(self):
        self.repo.songs[1] = replace(self.repo.songs[1], traits=SongTraits(3, ("COMBO", "JUST", "LUCK")))
        self.repo.songs[2] = replace(self.repo.songs[2], traits=SongTraits(2, ("JUST",)))
        self.repo.songs[3] = replace(self.repo.songs[3], traits=None)
        for condition in ("颜色=蓝/绿 激奏=JUST", "属性=蓝 激奏=纯JUST", "激奏=混合",
                          "激奏=JUST/JUST/COMBO", "激奏=JUST/COMBO/JUST", "激奏=包含全部JUST/COMBO"):
            with self.subTest(condition=condition):
                songs = resolve_command("/查曲 MyGO " + condition, self.repo).songs
                spec = parse_efficiency("/查分数表 MyGO " + condition, self.repo, direct=True)
                first = execute_efficiency(spec, self.repo)
                second = execute_efficiency(replace(spec, page=2), self.repo)
                ids = {r.song_id for r in (*first.rows, *second.rows)}
                self.assertEqual(ids, {s.id for s in songs})

    def test_single_song_cannot_bypass_filters(self):
        self.assertEqual(self.answer("100001 颜色=红").status, "empty")
        self.assertEqual(self.answer("100001 激奏=纯COMBO").status, "empty")
        self.assertEqual(self.answer("100001 EX lv<25").status, "empty")
        self.assertEqual(self.answer("100001 乐队=MyGO 属性=蓝色").status, "success")
        self.repo.songs[0] = replace(self.repo.songs[0], traits=None)
        self.assertEqual(self.answer("100001 颜色=蓝").status, "data_unavailable")
        self.assertEqual(self.answer("100001").status, "success")

    def test_each_chart_level_not_catalog_max(self):
        snapshot = self.repo.song_meta.get.return_value
        ex = snapshot.rows[0]
        hard = replace(ex, difficulty="HARD", level=20, eff=ex.eff + 1)
        self.repo.song_meta.get.return_value = replace(snapshot, rows=(ex, hard))
        result = self.answer("颜色=蓝 全难度 lv<25")
        self.assertEqual([r.difficulty for r in result.rows], ["HARD"])
        self.assertEqual([r.difficulty for r in self.answer("100001 ALL lv<25").rows], ["HARD"])
        self.assertEqual([r.difficulty for r in self.answer("100001").rows], ["EXPERT"])

    def test_roundtrip_and_full_filter_before_page(self):
        query = "/查分数表 乐队=MyGO 属性=蓝 EX 激奏=JUST lv<=25 指标=score 排序=asc 前10 页2"
        spec = parse_efficiency(query, self.repo, direct=True)
        again = parse_efficiency("/" + spec.command_label(), self.repo, direct=True)
        self.assertEqual(spec, again)
        result = execute_efficiency(spec, self.repo)
        self.assertEqual(result.total, 35)
        self.assertEqual(len(result.rows), 10)
        first = execute_efficiency(replace(spec, page=1), self.repo)
        self.assertFalse({r.song_id for r in first.rows} & {r.song_id for r in result.rows})

    def test_coverage_songs_and_rows(self):
        self.repo.songs = [replace(s, traits=None) for s in self.repo.songs]
        answer = self.answer("颜色=蓝")
        self.assertEqual(answer.status, "data_unavailable")
        self.assertEqual((answer.unknown_songs, answer.unknown_rows), (35, 35))
        self.assertEqual(len(self.answer("").rows), 30)

    def test_names_containing_conditions_and_unknown_arguments(self):
        song = replace(self.repo.songs[0], title="EX 25", titles=("EX 25",))
        self.repo.songs[0] = song
        spec = parse_efficiency("/查分数表 EX 25 颜色=蓝", self.repo, direct=True)
        self.assertEqual(spec.subject.value, song.id)
        for condition in ("乐队=不存在", "颜色=黑", "lv=25", "lv>99", "EX HD", "ALL EX", "颜色=蓝 未知=1"):
            with self.subTest(condition=condition):
                answer = self.answer(condition)
                self.assertEqual(answer.status, "invalid_arguments")

    def test_supported_natural_filter_is_local(self):
        parser = AIQueryParser(Settings("", "", self.repo.data_base, self.repo.cache_file, 6, ""))
        with patch.object(parser, "_request", side_effect=AssertionError("model called")):
            text, result = parser.answer_with_plan("/问 MyGO 颜色=蓝 激奏=JUST EX 分数表", self.repo)
        self.assertIsNotNone(result, text)
        self.assertEqual(result.meta.rows, self.answer("MyGO 颜色=蓝 激奏=JUST EX").rows)


if __name__ == "__main__":
    unittest.main()
