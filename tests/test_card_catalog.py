"""Offline synthetic catalog; short factual Yume samples are identified in mapping tests."""
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from ournotes_bot.sources import yatta
from ournotes_bot.ai_query import AIQueryParser
from ournotes_bot.query.card_catalog import query_cards, labels
from ournotes_bot.commands import resolve_command, handle_command
from ournotes_bot.config import Settings
from ournotes_bot.data import Card, SupportCard, SongRepository
from ournotes_bot.visuals import render_catalog, render_card, render_support_card


def raw_detail(support=False, live="スコアUP", gek="JUST数獲得量UP"):
    # Synthetic values, not game stats; structural shape follows public MasterParsed.
    return {"id": 1, **({"characters": [1]} if support else {"character": 1}),
            "statsMax": [10000, 20000, 30000],
            "levelGroup": [{"level": 1, "stats": [100, 200, 300]}],
            "rankGroup": [{"stats": None}], "awakeGroup": [],
            "skills": [{"id": 1, "type": "supportSkill" if support else "liveSkill",
                        "icon": "icon_skill_scoreup", "name": [live],
                        "description": ["条件を満たすと5秒間効果。"]},
                       {"id": 2, "type": "gekisouSupportSkill" if support else "gekisouSkill",
                        "name": [gek], "description": ["合成テスト説明。"]}]}


class CardCatalogTests(unittest.TestCase):
    def setUp(self):
        source = patch("ournotes_bot.rendering.member_list_visuals.get_snapshot", return_value=None)
        self.source = source.start()
        self.addCleanup(source.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = SongRepository(yatta.BASE, Path(self.tmp.name) / "cache.json")
        self.base = Card(1, 1, "合成卡", "高松灯", "MyGO!!!!!", 4, 1, 0, 0, 0, "", "", "", "",
                         catalog={"bands": ["MyGO!!!!!"], "tags": {"1": {"zh": "MyGO!!!!!"}},
                                  **yatta.detail_catalog(raw_detail())},
                         skills=yatta.build_skills(raw_detail()["skills"]))
        self.repo.cards = [self.base, replace(self.base, id=2, rarity=3, card_type=2),
                           replace(self.base, id=3, character="丰川祥子", band="Ave Mujica", card_type=2)]
        self.support = SupportCard(1, "合成SNAP", "高松灯 / 丰川祥子", ("高松灯", "丰川祥子"), 4, 1,
                                   0, 0, 0, "", "", "", catalog={"bands": ["MyGO!!!!!", "Ave Mujica"],
                                   **yatta.detail_catalog(raw_detail(True, "ライブスキル延長", "LUCKY RUSHスコアUP"))},
                                   skills=yatta.build_skills(raw_detail(True)["skills"]))
        self.repo.support_cards = [self.support]

    def ids(self, text, support=False):
        answer = query_cards(text, self.repo, support=support)
        self.assertFalse(answer.error, answer.error)
        return [c.id for c in answer.cards]

    def test_birthday_rarity_is_member_only_and_named_bd(self):
        from ournotes_bot.query.card_catalog import rarity_name
        card = replace(self.base, id=64, rarity=20)
        self.repo.cards.append(card)
        self.assertEqual(self.ids("BD"), [64])
        self.assertEqual(self.ids("稀有度=BD"), [64])
        self.assertEqual(rarity_name(card), "BD")
        self.assertTrue(query_cards("BD", self.repo, support=True).error)

    def test_and_or_dimensions_and_same_character_dedup(self):
        self.assertEqual(self.ids("Ave Mujica SSR"), [3])
        self.assertEqual(self.ids("SSR,SR 颜色=红色或蓝色 乐队=MyGO 角色=tmr LIVE=分数提升 击奏=JUST"), [1, 2])
        self.assertEqual(self.ids("角色=tmr,skk 乐队=MyGO LIVE=技能延长 击奏=LUCK", True), [1])
        self.repo.support_cards *= 2
        self.assertEqual(self.ids("角色=tmr,skk", True), [1])
        self.assertEqual(self.ids("乐队=MyGO 角色=skk"), [])

    def test_individual_dimensions_both_objects(self):
        for query in ("SSR", "颜色=红色", "乐队=MyGO", "角色=tmr", "LIVE=分数提升", "击奏=JUST"):
            self.assertIn(1, self.ids(query))
        for query in ("SSR", "颜色=红色", "乐队=MyGO", "角色=skk", "LIVE=技能延长", "击奏=LUCK"):
            self.assertEqual(self.ids(query, True), [1])

    def test_zero_single_and_multiple_are_lists(self):
        for q, count in (("颜色=红色", 1), ("SSR", 2), ("颜色=紫色", 0)):
            answer = query_cards(q, self.repo)
            self.assertEqual(answer.request.mode, "list")
            self.assertEqual(len(answer.cards), count)
            self.assertNotIn("详情", answer.text().splitlines()[0])
        self.assertIn("未放宽", query_cards("颜色=紫色", self.repo).text())

    def test_exact_id_objects_and_art(self):
        for cmd, support in (("/查卡", False), ("/查SNAP", True)):
            result = resolve_command(cmd + " ID=1", self.repo)
            self.assertEqual(result.catalog.request.mode, "detail")
            self.assertEqual(result.catalog.request.support, support)
            self.assertTrue(resolve_command(cmd + " 11", self.repo).catalog.error)
        for cmd in ("/查卡面", "/查支援卡面"):
            result = resolve_command(cmd + " 1", self.repo)
            self.assertEqual(result.catalog.request.mode, "art")
            self.assertTrue(resolve_command(cmd + " tmr", self.repo).catalog.error)
        from ournotes_bot.natural_query.local_query import parse_local_query
        self.assertIn("同时存在", parse_local_query("ID1", self.repo))

    def test_unknown_values_and_unverified_rarity_are_errors(self):
        for q in ("颜色=黑色", "LIVE=超强", "击奏=FEVER", "EX", "未知=1", "乐队=不存在", "角色=不存在", "得意=不存在", "页0"):
            self.assertTrue(query_cards(q, self.repo).error, q)
        self.assertTrue(query_cards("得意=MyGO", self.repo, support=True).error)
        self.repo.support_cards += [replace(self.support, id=61, rarity=10)]
        self.assertEqual(self.ids("EX", True), [61])

    def test_tags_require_all(self):
        self.repo.cards[0] = replace(self.base, catalog={**self.base.catalog, "tags": {
            "1": {"zh": "MyGO!!!!!"}, "2": {"zh": "Ave Mujica"}}})
        self.assertEqual(self.ids("得意=MyGO,Ave Mujica"), [1])

    def test_pages_complete_stable_and_executable(self):
        self.repo.cards = [replace(self.base, id=i) for i in reversed(range(1, 36))]
        seen = []
        for page in range(1, 4):
            result = resolve_command(f"/查卡 SSR 颜色=红色 页{page}", self.repo)
            answer = result.catalog
            seen += [c.id for c in answer.visible]
            self.assertEqual(len(answer.cards), 35)
            if page < 3:
                next_cmd = answer.footer.split("下一页：")[1]
                self.assertEqual(resolve_command(next_cmd, self.repo).catalog.request.page, page + 1)
        self.assertEqual(seen, list(range(1, 36)))
        out = query_cards("SSR 页4", self.repo)
        self.assertEqual(out.visible, ())
        self.assertIn("超出", out.text())

    def test_unknown_index_is_not_empty_or_no_skill(self):
        self.repo.cards[1] = replace(self.repo.cards[1], catalog={})
        answer = query_cards("LIVE=分数提升", self.repo)
        self.assertIn("索引不完整", answer.error)
        self.assertIn("分类未确认", labels(self.repo.cards[1]))
        self.assertEqual(len(query_cards("", self.repo).cards), 3)

    def test_source_mapping_samples_and_ex_multiple_live(self):
        # Project Yume /Resources/en/MasterParsed/supportcards/61.json, read 2026-09-28:
        # two supportSkill entries, names ライブスキル延長 / LIFE回復, no gekisou.
        raw = raw_detail(True, "ライブスキル延長")
        raw["skills"][1] = {"id": 73, "type": "supportSkill", "name": ["LIFE回復"]}
        catalog = yatta.detail_catalog(raw)
        self.assertEqual(catalog["categories"], {"live": ["duration", "life"], "gekisou": ["not_applicable"]})
        card = replace(self.support, catalog={**self.support.catalog, **catalog})
        self.repo.support_cards = [card]
        self.assertIn("LIFE回复", labels(card)[0])
        self.assertEqual(labels(card)[1], "不适用")
        self.assertEqual(self.ids("LIVE=LIFE回复", True), [1])
        self.assertEqual(self.ids("击奏=JUST", True), [])
        # member 51: スコアUP(LIFEボーナス) is score, not recovery.
        self.assertEqual(yatta.detail_catalog(raw_detail(live="スコアUP(LIFEボーナス)"))["categories"]["live"], ["score"])

    def test_unknown_skill_does_not_become_default_category(self):
        data = yatta.detail_catalog(raw_detail(live="未確認分類"))
        self.assertEqual(data["categories"]["live"], [None])
        raw = raw_detail()
        raw["skills"] = [{"type": "futureSkill"}]
        self.assertEqual(yatta.detail_catalog(raw)["categories"]["gekisou"], [None])
        raw["skills"] = [None]
        with self.assertRaises(ValueError):
            yatta.detail_catalog(raw)

    def test_effect_locale_formula_and_multiline_are_preserved(self):
        raw = {"description": ["条件\n効果{x}秒", "", "", ""],
               "effects": {"0": [{"param": "x", "data": {"5": 1500}, "formula": {"divide": 1000, "multiply": 2}}],
                           "3": [{"param": "x", "data": {"5": 999}}]}}
        self.assertEqual(yatta.skill_description(raw), "条件\n効果3秒")
        self.assertEqual(yatta.detail_catalog(raw_detail())["stats_level1"], [100, 400, 900])
        self.assertEqual(yatta.detail_catalog(raw_detail(True))["stats_level1"], [1, 4, 9])

    def test_cache_round_trip_legacy_fields_and_missing_index_upgrade(self):
        self.repo.metadata = {"source": yatta.BASE, "schema": 4, "card_catalog_version": 1,
                              "member_skill_index_complete": True}
        self.repo._save_cache()
        raw = json.loads(self.repo.cache_file.read_text(encoding="utf-8"))
        self.assertNotIn("catalog", raw["cards"][0])
        self.assertNotIn("catalog", raw["support_cards"][0])
        loaded = SongRepository(yatta.BASE, self.repo.cache_file)
        with patch.object(loaded, "refresh", side_effect=AssertionError("unexpected network")):
            loaded.load()
        self.assertEqual(loaded.cards, self.repo.cards)
        self.assertEqual(loaded.support_cards, self.repo.support_cards)
        raw.pop("card_catalog")
        raw["metadata"].pop("card_catalog_version")
        self.repo.cache_file.write_text(json.dumps(raw), encoding="utf-8")
        from ournotes_bot.data import DataError
        with patch.object(loaded, "refresh", side_effect=DataError("offline")) as refresh:
            loaded.load()
        refresh.assert_called_once()
        self.assertEqual(loaded.cache_state, "stale")
        self.assertIn("索引不完整", query_cards("LIVE=分数提升", loaded).error)

    def test_refresh_failure_preserves_support_and_new_index(self):
        from ournotes_bot.data import DataError
        # Reuse the normal refresh path with entirely synthetic input and no network.
        with patch.object(yatta, "fetch_json", return_value={}), \
             patch.object(yatta, "build_data", return_value=([], [replace(self.base, skills=(), catalog={})])), \
             patch.object(yatta, "build_support_cards", return_value=[replace(self.support, skills=(), catalog={})]), \
             patch.object(yatta, "card_detail", side_effect=OSError("offline")), \
             patch.object(yatta, "support_card_detail", side_effect=OSError("offline")):
            self.repo.refresh()
        self.assertEqual(self.repo.support_cards[0].skills, self.support.skills)
        self.assertTrue(self.repo.support_cards[0].catalog["detail_stale"])
        self.assertEqual(self.ids("LIVE=技能延长", True), [1])
        before = self.repo.cache_file.read_bytes()
        with patch.object(yatta, "fetch_json", side_effect=ValueError("HTML error")):
            with self.assertRaises(DataError):
                self.repo.refresh()
        self.assertEqual(self.repo.cache_file.read_bytes(), before)

    def test_detail_id_mismatch_and_nonfinite_stats_are_not_presented(self):
        raw = raw_detail()
        raw["id"] = 99
        with self.assertRaises(ValueError):
            self.repo._merge_card_detail(self.base, raw)
        raw = raw_detail(True)
        raw["statsMax"] = [float("inf"), 1, 1]
        self.assertIsNone(yatta.detail_catalog(raw)["stats_level1"])

    def test_direct_and_natural_share_selection_no_model_or_quota(self):
        parser = AIQueryParser(Settings("", "", yatta.BASE, self.repo.cache_file, 6))
        with patch.object(parser, "_request", side_effect=AssertionError("AI forbidden")) as model, \
             patch.object(parser._quota, "reserve", side_effect=AssertionError("quota forbidden")):
            for command, question in (("/查卡 SSR 颜色=红色 LIVE=分数提升 击奏=JUST", "SSR 颜色=红色 LIVE=分数提升 击奏=JUST的角色卡有哪些"),
                                      ("/查支援卡 角色=tmr LIVE=技能延长", "角色=tmr LIVE=技能延长的SNAP有哪些"),
                                      ("/查卡 MyGO SSR 红色", "MyGO的红色SSR角色卡有哪些"),
                                      ("/查卡 SSR SR", "SSR SR卡有哪些")):
                direct = resolve_command(command, self.repo)
                text, natural = parser.answer_with_plan("/问 " + question, self.repo)
                self.assertIsNotNone(natural, text)
                self.assertEqual(direct.catalog.request.filters, natural.catalog.request.filters)
                self.assertEqual(direct.catalog.cards, natural.catalog.cards)
            model.assert_not_called()

    def test_visual_single_list_detail_missing_and_long_text(self):
        from ournotes_bot.rendering.card_visuals import _lines
        from ournotes_bot.visuals import _font
        title = "很长的标题" * 12
        long = replace(self.base, title=title, skills=yatta.build_skills([{
            "type": "liveSkill", "name": title, "description": "持续时间、概率和条件。" * 30}]))
        self.assertEqual("".join(_lines(title, 300, 25)), title)
        self.assertTrue(all(_font(25).getlength(s) <= 300 for s in _lines(title, 300, 25)))
        with patch("ournotes_bot.visuals._asset", return_value=None):
            for blob in (render_catalog(query_cards("颜色=红色", self.repo)), render_card(long), render_support_card(self.support)):
                image = Image.open(io.BytesIO(blob))
                self.assertLessEqual(image.width, 2000)
                self.assertLessEqual(image.height, 8192)
                self.assertLessEqual(len(blob), 1_500_000)
                self.assertLess(image.height, 24000)
            self.assertIsNone(render_catalog(query_cards("1", self.repo, art=True)))

    def test_art_preserves_aspect_ratio(self):
        with patch("ournotes_bot.visuals._asset", return_value=Image.new("RGBA", (512, 288))):
            blob = render_catalog(query_cards("1", self.repo, support=True, art=True))
        self.assertEqual(Image.open(io.BytesIO(blob)).size, (512, 288))


if __name__ == "__main__":
    unittest.main()
