from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

from ournotes_bot.chart_data import ChartDataError, chart_url, load_chart_score
from ournotes_bot.data import Chart, Song
from ournotes_bot.visuals import _draw_score, render_chart


SCORE = {
    "meta": {"version": 100},
    "score": {
        "events": {"bpm": [{"t": 0, "bpm": 190}], "sig": [{"t": 0, "sig": [4, 4]}]},
        "notes": [
            {"t": 9600, "pos": 6, "size": 6},
            {"type": "flick", "t": 10080, "pos": 12, "size": 6, "dir": "right"},
            {"type": "long", "node": [
                {"t": 10560, "pos": 6, "size": 6},
                {"t": 11520, "pos": 12, "size": 6},
            ]},
        ],
    },
}


class ChartDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.chart = Chart("EXPERT", 25, 25.0, 768, "0001/0001_03")
        self.song = Song(100001, "Test", ("Test",), "MyGO!!!!!", "", "", "",
                         "", "", (self.chart,))

    def test_public_score_url_and_cache_fallback(self) -> None:
        self.assertEqual(chart_url(self.song, self.chart),
                         "https://storage.bdon.moe/moenotes/Live/MusicScore/0001/0001_03/0001_03.json")
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            with patch("ournotes_bot.chart_data.urlopen", return_value=io.BytesIO(json.dumps(SCORE).encode())) as fetch:
                self.assertEqual(load_chart_score(self.song, self.chart, cache)["notes"], SCORE["score"]["notes"])
                self.assertEqual(fetch.call_count, 1)
            with patch("ournotes_bot.chart_data.urlopen", side_effect=AssertionError("cache was not used")):
                self.assertEqual(load_chart_score(self.song, self.chart, cache)["notes"], SCORE["score"]["notes"])
            os.utime(cache / "0001_0001_03.json", (0, 0))
            with patch("ournotes_bot.chart_data.urlopen", side_effect=OSError("offline")):
                self.assertEqual(load_chart_score(self.song, self.chart, cache)["notes"], SCORE["score"]["notes"])

    def test_invalid_or_unrecognized_score_is_rejected(self) -> None:
        with self.assertRaises(ChartDataError):
            chart_url(Song(999999, "Other", (), "", "", "", "", "", "", (self.chart,)), self.chart)
        with tempfile.TemporaryDirectory() as directory:
            with patch("ournotes_bot.chart_data.urlopen", return_value=io.BytesIO(b'{"score":{"notes":[]}}')):
                with self.assertRaises(ChartDataError):
                    load_chart_score(self.song, self.chart, Path(directory))

    def test_rendered_score_is_an_image_with_note_timeline(self) -> None:
        song = Song(100001, "Test", ("Test",), "MyGO!!!!!", "Composer", "Lyricist", "Arranger",
                    "2026-01-01", "https://example.invalid/jacket.png", (self.chart,))
        with patch("ournotes_bot.visuals._asset", return_value=None):
            result = render_chart(song, (self.chart,), "zh", SCORE["score"], "EXPERT")
        with Image.open(io.BytesIO(result)) as image:
            self.assertEqual(image.width, 900)
            self.assertGreater(image.height, 2000)
            self.assertNotEqual(image.getpixel((100, 1000)), image.getpixel((100, 600)))

    def test_score_reads_upwards_with_columns_and_lanes_left_to_right(self) -> None:
        image = Image.new("RGB", (900, 1800))
        draw = Mock(wraps=ImageDraw.Draw(image))
        _draw_score(draw, {"notes": [
            {"t": 0, "pos": 0, "size": 6},
            {"t": 960, "pos": 12, "size": 6},
            {"t": 1920, "pos": 0, "size": 6},
        ]}, 100, "zh")
        notes = [call.args[0] for call in draw.rounded_rectangle.call_args_list
                 if call.kwargs.get("fill") == "#75C5E8"]
        early, later, next_column = notes
        self.assertGreater(early[1], later[1])
        self.assertLess(early[0], later[0])
        self.assertGreater(next_column[0], later[0])
        self.assertEqual(early[1], next_column[1])
        self.assertIn("起点 ↑", [call.args[1] for call in draw.text.call_args_list])

    def test_dense_notes_have_room_and_canvas_contains_the_timeline(self) -> None:
        score = {"notes": [
            {"t": 0, "pos": 0, "size": 6},
            {"t": 120, "pos": 0, "size": 6},
            {"t": 180000, "pos": 0, "size": 6},
        ]}
        draw = Mock()
        bottom = _draw_score(draw, score, 790, "zh")
        notes = [call.args[0] for call in draw.rounded_rectangle.call_args_list
                 if call.kwargs.get("fill") == "#75C5E8"]
        self.assertGreaterEqual(notes[0][1] - notes[1][1], 10)
        with patch("ournotes_bot.visuals._asset", return_value=None):
            result = render_chart(self.song, (self.chart,), "zh", score, "EXPERT")
        with Image.open(io.BytesIO(result)) as image:
            self.assertGreaterEqual(image.height, bottom + 60)

    def test_cross_column_holds_and_flick_directions_survive_vertical_mapping(self) -> None:
        image = Image.new("RGB", (900, 1800))
        draw = Mock(wraps=ImageDraw.Draw(image))
        bottom = _draw_score(draw, {"notes": [
            {"type": "long", "node": [{"t": 1440, "pos": 0, "size": 6},
                                       {"t": 2400, "pos": 12, "size": 6}]},
            {"type": "flick", "t": 0, "pos": 0, "size": 6, "dir": "left"},
            {"type": "flick", "t": 960, "pos": 12, "size": 6, "dir": "right"},
        ]}, 100, "zh")
        first, second = [call.args[0] for call in draw.polygon.call_args_list]
        self.assertGreater(first[0][1], first[2][1])
        self.assertEqual(first[2][1], 100)
        self.assertEqual(second[0][1], bottom - 24)
        self.assertGreater(second[0][1], second[2][1])
        self.assertGreater(second[0][0], first[2][0])
        arrows = [call.args[0] for call in draw.line.call_args_list
                  if call.kwargs.get("fill") == "#68D99C"]
        self.assertEqual(len(arrows), 2)
        self.assertLess(arrows[0][2], arrows[0][0])
        self.assertGreater(arrows[1][2], arrows[1][0])
        for arrow in arrows:
            self.assertLess(arrow[3], arrow[1])


if __name__ == "__main__":
    unittest.main()
