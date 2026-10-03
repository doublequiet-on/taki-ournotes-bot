"""Regression contracts for unique entities and trustworthy missing fields."""
import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

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
        self.repo.cache_file.write_text(json.dumps(payload), encoding="utf-8")
        self.repo._load_cache()
        self.assertEqual([c.notes for c in self.repo.songs[0].charts][:2], [None, None])

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
