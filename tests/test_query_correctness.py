"""Regression contracts for unique entities and trustworthy missing fields."""
import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ournotes_bot.commands import resolve_command, handle_command
from ournotes_bot.data import SongRepository
from ournotes_bot.sources.yatta import BASE, build_data
from test_query import CHARACTERS, CARDS, SONGS, META


class CorrectnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = SongRepository(BASE, Path(self.tmp.name) / "cache.json")
        self.repo.songs, self.repo.cards = build_data(CHARACTERS, CARDS, SONGS, META)
        self.repo.metadata = {"source": BASE, "schema": self.repo.CACHE_SCHEMA}

    def test_same_title_different_ids_requires_selection(self):
        song = self.repo.songs[0]
        self.repo.songs.append(replace(song, id=100002))
        result = resolve_command("/查谱面 迷星叫 EX", self.repo)
        self.assertEqual(result.status, "ambiguous")
        self.assertFalse(result.songs)
        text = handle_command("/查谱面 迷星叫 EX", self.repo, result)
        self.assertIn("/查谱面 100001 EXPERT", text)
        self.assertIn("/查谱面 100002 EXPERT", text)
        self.assertEqual(len(resolve_command("/查曲 迷星叫", self.repo).songs), 2)

    def test_entity_name_before_difficulty_or_level(self):
        song = replace(self.repo.songs[0], title="A EX", titles=("A EX",), localized={})
        self.repo.songs = [song]
        result = resolve_command("/查谱面 A EX", self.repo)
        self.assertEqual(result.songs, (song,))
        self.assertIsNone(result.parsed[2])
        self.repo.songs = [replace(song, title="Song 25", titles=("Song 25",))]
        self.assertEqual(len(resolve_command("/查曲 Song 25", self.repo).songs), 1)

    def test_chart_title_takes_priority_over_option_shaped_suffixes(self):
        song = self.repo.songs[0]
        for title in ("Song EX", "Song HD", "Song ＥＸ", "Song 镜像", "Song 反转", "Song 5/6.5/8/5/5",
                      "Song 覆盖=5/6.5/8/5/5", "Song 24/7"):
            with self.subTest(title=title):
                full_title = replace(song, id=100002, title=title, titles=(title,), localized={})
                self.repo.songs = [replace(song, title="Song", titles=("Song",), localized={}), full_title]
                result = resolve_command(f"/查谱面 {title}", self.repo)
                self.assertEqual(result.songs, (full_title,))
                self.assertIsNone(result.parsed[2])
                self.assertFalse(result.chart_mirror)
                self.assertEqual(result.chart_coverage, ())
                result = resolve_command(f"/查谱面 {title} 镜像 5/6.5/8/5/5 ＨＤ", self.repo)
                self.assertEqual(result.songs, (full_title,))
                self.assertEqual(result.parsed[2], "HARD")
                self.assertTrue(result.chart_mirror)
                self.assertEqual(result.chart_coverage, (5, 6.5, 8, 5, 5))

    def test_chart_options_keep_ambiguity_and_retry_parameters(self):
        song = self.repo.songs[0]
        self.repo.songs.append(replace(song, id=100002))
        command = "/查谱面 迷星叫 反转 5/6.5/8/5/5 ex"
        result = resolve_command(command, self.repo)
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(result.candidates, tuple(self.repo.songs))
        for retry in handle_command(command, self.repo, result).splitlines()[1:]:
            resolved = resolve_command(retry.split("→ ", 1)[1], self.repo)
            self.assertEqual(resolved.status, "success")
            self.assertEqual(resolved.parsed[2], "EXPERT")
            self.assertTrue(resolved.chart_mirror)
            self.assertEqual(resolved.chart_coverage, (5, 6.5, 8, 5, 5))

    def test_chart_three_han_typo_yields_to_exact_titles_aliases_and_substrings(self):
        song = self.repo.songs[0]
        exact = resolve_command("/查谱面 迷星叫", self.repo)
        self.assertEqual(exact.songs, (song,))
        self.assertEqual(exact.status, "success")

        literal = replace(song, id=100002, title="再唱迷星咬", titles=(), localized={})
        self.repo.songs = [song, literal]
        self.assertEqual(resolve_command("/查谱面 迷星咬", self.repo).songs, (literal,))

        alias_target = replace(literal, title="Another Song", titles=("Another Song",))
        self.repo.songs = [song, alias_target]
        with patch("ournotes_bot.query.entity_lexicon._aliases",
                   return_value={"song": {"迷星咬": "100002"}}):
            self.assertEqual(resolve_command("/查谱面 迷星咬", self.repo).songs, (alias_target,))

    def test_chart_three_han_typo_limits_distinct_song_ids(self):
        song = self.repo.songs[0]
        variants = (
            song,
            replace(song, id=100002, title="春日之歌", titles=("迷星吼", "迷星唱"), localized={}),
            replace(song, id=100003, title="Star Song", titles=(), localized={"title": {"zh": "迷月咬"}}),
            replace(song, id=100004, title="梦星咬", titles=(), localized={}),
        )
        duplicate = replace(song, title="迷星笑", titles=("迷星笑",), localized={})
        for count in range(1, 5):
            with self.subTest(count=count):
                self.repo.songs = [*reversed(variants[:count]), duplicate]
                command = "/查谱面 迷 星·咬 EX"
                result = resolve_command(command, self.repo)
                self.assertEqual(result.status, "ambiguous")
                self.assertFalse(result.songs)
                self.assertEqual([candidate.id for candidate in result.candidates],
                                 [song.id for song in variants[:count]])
                reply = handle_command(command, self.repo, result)
                if count <= 3:
                    self.assertEqual(reply.count("→ /查谱面"), count)
                    self.assertIn(song.band, reply)
                else:
                    self.assertEqual(reply, "有 4 首歌曲可能匹配。请补充歌名以缩小范围。")

    def test_chart_three_han_typo_suppresses_looser_suggestions(self):
        song = self.repo.songs[0]
        for title in ("迷咬星", "迷星又咬", "我夜咬", "迷星A"):
            with self.subTest(title=title):
                self.repo.songs = [replace(song, title=title, titles=(title,), localized={})]
                command = "/查谱面 迷星咬"
                result = resolve_command(command, self.repo)
                self.assertEqual(result.status, "unknown_entity")
                self.assertFalse(result.candidates)
                reply = handle_command(command, self.repo, result)
                self.assertIn("没有找到谱面", reply)
                self.assertNotIn("你可能想查", reply)
                self.assertNotIn(str(song.id) + "）", reply)

    def test_chart_long_name_keeps_existing_fuzzy_candidates(self):
        song = replace(self.repo.songs[0], title="Stellar Odyssey", titles=("Stellar Odyssey",), localized={})
        self.repo.songs = [song]
        command = "/查谱面 Stelar Odyssey EX"
        result = resolve_command(command, self.repo)
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(result.candidates, (song,))
        self.assertIn("Stellar Odyssey（ID 100001）→ /查谱面 100001 EXPERT",
                      handle_command(command, self.repo, result))

    def test_chart_reports_repeated_or_invalid_options(self):
        for options, hint in (
            ("EX 镜像 ＨＤ", "难度只能指定一次"),
            ("镜像 EX 反转", "镜像只能指定一次"),
            ("5/6.5/8/5/5 EX 覆盖=5/5/5/5/5", "覆盖时间只能指定一组"),
            ("覆盖=5/5/5/5/5 反转 5/6.5/8/5/5", "覆盖时间只能指定一组"),
            ("5/5/5 EX 镜像", "五个正数"),
            ("EX 覆盖=foo 镜像", "五个正数"),
            ("0/5/5/5/5 镜像 EX", "五个正数"),
            ("5/5/xx/5/5 镜像 EX", "五个正数"),
            ("5/5/NaN/5/5 镜像 EX", "五个正数"),
        ):
            with self.subTest(options=options):
                command = "/查谱面 1 " + options
                result = resolve_command(command, self.repo)
                self.assertEqual(result.status, "invalid_arguments")
                self.assertIn(hint, handle_command(command, self.repo, result))

    def test_chart_help_explains_omitted_difficulty(self):
        for command, text_hint, image_hint in (
            ("/查谱面", "文字列出各档数据", "图片只绘制一档，优先 EXPERT"),
            ("/chart", "text lists all available difficulties", "image shows one chart, preferring EXPERT"),
            ("/譜面", "テキストには取得済みの全難易度", "画像はEXPERTを優先して1つ"),
        ):
            with self.subTest(command=command):
                reply = handle_command(command, self.repo)
                self.assertIn("EZ", reply)
                self.assertIn(text_hint, reply)
                self.assertIn(image_hint, reply)
                self.assertIn(command + " 1 镜像 5/6.5/8/5/5 EX", reply)

    def test_missing_and_zero_survive_cache_and_old_rows_remain_readable(self):
        meta = deepcopy(META)
        meta["100001"][0][4] = None
        meta["100001"][1][4] = 0
        self.repo.songs, self.repo.cards = build_data(CHARACTERS, CARDS, SONGS, meta)
        self.assertIsNone(self.repo.songs[0].charts[0].notes)
        self.assertEqual(self.repo.songs[0].charts[1].notes, 0)
        self.repo._save_cache()
        payload = json.loads(self.repo.cache_file.read_text(encoding="utf-8"))
        self.assertEqual([c["notes"] for c in payload["songs"][0]["charts"]][:2], [0, 0])
        self.repo._load_cache()
        self.assertEqual([c.notes for c in self.repo.songs[0].charts][:2], [None, 0])
        text = handle_command("/查谱面 100001", self.repo)
        self.assertIn("Note：暂无资料", text)
        self.assertIn("0 Notes", text)
        del payload["chart_notes_known"]
        del payload["metadata"]["chart_notes_known"]
        self.repo.cache_file.write_text(json.dumps(payload), encoding="utf-8")
        self.repo._load_cache()
        self.assertEqual([c.notes for c in self.repo.songs[0].charts][:2], [None, None])

    def test_metadata_keeps_provenance_after_legacy_top_level_rewrite(self):
        meta = deepcopy(META)
        meta["100001"][0][4] = None
        meta["100001"][1][4] = 0
        meta["100001"][2][4] = 123
        self.repo.songs, self.repo.cards = build_data(CHARACTERS, CARDS, SONGS, meta)
        self.repo._save_cache()
        payload = json.loads(self.repo.cache_file.read_text(encoding="utf-8"))
        # The complete 8ae reader/writer also runs in the offline rollback probe;
        # this fixture is its stable schema-2 shape with metadata passed through.
        del payload["chart_notes_known"]
        self.repo.cache_file.write_text(json.dumps(payload), encoding="utf-8")
        self.repo._load_cache()
        self.assertEqual([c.notes for c in self.repo.songs[0].charts][:3], [None, 0, 123])

    def test_save_replaces_obsolete_metadata_provenance(self):
        meta = deepcopy(META)
        meta["100001"][0][4] = 0
        self.repo.songs, self.repo.cards = build_data(CHARACTERS, CARDS, SONGS, meta)
        self.repo._save_cache()
        self.repo._load_cache()
        song = self.repo.songs[0]
        charts = (replace(song.charts[0], notes=None), *song.charts[1:])
        self.repo.songs[0] = replace(song, charts=charts)
        self.repo._save_cache()
        self.repo._load_cache()
        self.assertIsNone(self.repo.songs[0].charts[0].notes)

    def test_current_unknown_is_not_overridden_by_legacy_zero_marker(self):
        meta = deepcopy(META)
        meta["100001"][0][4] = None
        self.repo.songs, self.repo.cards = build_data(CHARACTERS, CARDS, SONGS, meta)
        self.repo._save_cache()
        payload = json.loads(self.repo.cache_file.read_text(encoding="utf-8"))
        payload["metadata"]["chart_notes_known"]["100001"]["EASY"] = True
        self.repo.cache_file.write_text(json.dumps(payload), encoding="utf-8")
        self.repo._load_cache()
        self.assertIsNone(self.repo.songs[0].charts[0].notes)

    def test_numeric_title_before_short_id_and_fuzzy_tail_not_ignored(self):
        original = self.repo.songs[0]
        song = replace(original, title="123", titles=("123",), localized={})
        self.repo.songs = [song, replace(original, id=100123)]
        self.assertEqual(resolve_command("/查谱面 123 EX", self.repo).songs, (song,))
        title = "A very long verified song title"
        self.repo.songs = [replace(original, title=title, titles=(title,), localized={})]
        query = f"/查谱面 {title} foo=1 EX"
        result = resolve_command(query, self.repo)
        self.assertEqual(result.status, "ambiguous")
        self.assertFalse(result.songs)
        self.assertIn("/查谱面 100001 EXPERT", handle_command(query, self.repo, result))


if __name__ == "__main__":
    unittest.main()
