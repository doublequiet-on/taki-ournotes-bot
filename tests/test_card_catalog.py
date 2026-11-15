"""Offline synthetic catalog; short factual Yume samples are identified in mapping tests."""
import io
import json
import os
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

    def test_direct_member_names_keep_symbols_exact_and_offer_copyable_details(self):
        symbolic = "流星或旅途/心声：A"
        self.repo.cards = [replace(self.base, id=51, title=symbolic, card_type=4),
                           replace(self.base, id=52, title=symbolic + " 后篇", card_type=2),
                           replace(self.base, id=53, title="JUST", card_type=1),
                           replace(self.base, id=54, title="MyGO", card_type=2),
                           replace(self.base, id=55, title="Winter JUST Pages 页2", card_type=4),
                           replace(self.base, id=56, title="白昼序曲", card_type=4),
                           replace(self.base, id=57, title="夜幕尾声", card_type=4)]
        for query, expected in ((symbolic, [51]), (symbolic + " 黄", [51]),
                                ("卡名=" + symbolic, [51]), ("卡名：JUST", [53]),
                                ("卡名=MyGO", [54]), ("流星或旅途", [51, 52]),
                                ('卡名="Winter JUST" 黄', [55]), ("Winter JUST Pages 页2", [55]),
                                ("流星或旅途 黄", [51]), ("颜色=蓝 流星或旅途", [52]),
                                ("白昼序曲 / 夜幕尾声 黄", [56, 57])):
            with self.subTest(query=query):
                answer = resolve_command("/查卡 " + query, self.repo).catalog
                self.assertEqual(answer.request.mode, "list")
                self.assertEqual([card.id for card in answer.cards], expected)
                for card_id in expected:
                    self.assertIn(f"/查卡 {card_id}", answer.text())

    def test_member_title_page_suffix_stays_with_title_in_any_filter_order(self):
        title = "Winter JUST Pages 页2"
        self.repo.cards = [replace(self.base, id=50, title=title, card_type=4)]
        self.repo.cards.extend(replace(self.base, id=card_id, title=f"测试备用 {card_id}", card_type=4)
                               for card_id in range(1, 18))
        for query, page in ((f"{title} 黄", 1), (f"黄 {title}", 1), (f"颜色=黄 卡名={title}", 1),
                            (f'黄 卡名:"{title}"', 1), (f"黄 {title} 页3", 3)):
            with self.subTest(query=query):
                answer = resolve_command("/查卡 " + query, self.repo).catalog
                self.assertEqual(answer.request.page, page)
                self.assertEqual([card.id for card in answer.cards], [50])
        answer = resolve_command("/查卡 黄 页2", self.repo).catalog
        self.assertEqual(answer.request.page, 2)
        self.assertEqual([c.id for c in answer.visible], [17, 50])
        answer = resolve_command('/查卡 黄 卡名:"Winter JUST" 页2', self.repo).catalog
        self.assertEqual(answer.request.page, 2)
        self.assertEqual([c.id for c in answer.cards], [50])

    def test_direct_member_reorders_mixed_filters_without_relaxing_them(self):
        self.repo.cards = [replace(self.base, card_type=4),
                           replace(self.base, id=2, rarity=3, card_type=4),
                           replace(self.base, id=3, card_type=2),
                           replace(self.base, id=4, character="丰川祥子", band="Ave Mujica", card_type=4)]
        for query in ("颜色=黄 tmr SSR, SR just", "tmr SSR/SR 黄 JUST",
                      "ＴＭＲ　稀有度：ＳＳＲ ／ ＳＲ　黄　激奏＝ｊｕｓｔ",
                      "LIVE=分数提升 颜色=黄 角色=tmr 稀有度=SSR 或 SR JUST",
                      "SSR | SR 颜色=黄 / 黄 角色=tmr live=分数UP JUST"):
            with self.subTest(query=query):
                self.assertEqual([c.id for c in resolve_command("/查卡 " + query, self.repo).cards], [1, 2])
        self.assertEqual([c.id for c in resolve_command("/card 黄 BAND=Ave Mujica SSR", self.repo).cards], [4])
        for query in ("角色=不存在", "未知=黄", "颜色= SSR", "稀有度=SSR,",
                      "前段 SSR 后段", "卡名=前段 SSR 卡名=后段"):
            with self.subTest(query=query):
                self.assertEqual(resolve_command("/查卡 " + query, self.repo).status, "invalid_arguments")
        for query in ("不存在的标题 黄", "合成卡 紫", "卡名:合成卡 紫"):
            with self.subTest(query=query):
                answer = resolve_command("/查卡 " + query, self.repo).catalog
                self.assertEqual(answer.status, "empty")
                self.assertIn("未放宽", answer.text())
        art = resolve_command("/查卡面 1", self.repo).catalog
        self.assertEqual((art.request.mode, art.cards[0].id), ("art", 1))

    def test_title_match_keeps_tag_intersection_and_exact_id(self):
        two_tags = {"1": {"zh": "MyGO!!!!!"}, "2": {"zh": "Ave Mujica"}}
        self.repo.cards[0] = replace(self.base, catalog={**self.base.catalog, "tags": two_tags})
        answer = resolve_command("/查卡 得意=MyGO、 Ave Mujica 合成", self.repo).catalog
        self.assertEqual([card.id for card in answer.cards], [1])
        command = resolve_command("/查卡 ID：1", self.repo)
        self.assertEqual((command.catalog.request.mode, command.cards[0].id), ("detail", 1))
        with patch.object(self.repo, "card_with_detail", side_effect=AssertionError):
            self.assertEqual(resolve_command("/查卡 2 卡名=不存在", self.repo).status, "empty")

    def test_individual_dimensions_both_objects(self):
        for query in ("SSR", "颜色=红色", "乐队=MyGO", "角色=tmr", "LIVE=分数提升", "击奏=JUST"):
            self.assertIn(1, self.ids(query))
        for query in ("SSR", "颜色=红色", "乐队=MyGO", "角色=skk", "LIVE=技能延长", "击奏=LUCK"):
            self.assertEqual(self.ids(query, True), [1])

    def test_direct_snap_title_filters_keep_punctuation_and_object(self):
        ssr = replace(self.support, id=51, title="在迷失中，相伴一生")
        ex = replace(self.support, id=70, title="Abracadabra", rarity=10)
        self.repo.support_cards = [ssr, ex, replace(ssr, id=80, title="Another Abracadabra")]
        for query, ids in (
            ("在迷失中，相伴一生", [51]),
            ("颜色=红 在迷失中，相伴一生 SSR", [51]),
            ("Abracadabra", [70]),
            ("EX Abra", [70]),
            ("卡名:Abra EX", [70]),
            ('LIVE=技能延长 EX 卡名="Abracadabra"', [70]),
            ("LIVE=技能延长 SSR / EX", [70, 51, 80]),
        ):
            with self.subTest(query=query):
                result = resolve_command("/查留影卡 " + query, self.repo)
                self.assertEqual(result.catalog.request.mode, "list")
                self.assertEqual([card.id for card in result.support_cards], ids)
                if result.catalog.request.matched_title_ids is not None:
                    self.assertIn(f"/查留影卡 {ids[0]}", result.catalog.candidate_choices(result.support_cards))
        command = resolve_command("/查留影卡 LIVE=技能延长 70 EX", self.repo)
        self.assertEqual(command.catalog.request.mode, "detail")
        self.assertEqual([card.id for card in command.support_cards], [70])
        self.assertEqual(resolve_command("/查留影卡 LIVE=155", self.repo).status, "invalid_arguments")
        self.assertEqual(resolve_command("/查留影卡 队长=异色", self.repo).status, "invalid_arguments")

    def test_direct_snap_live_shortcuts_and_space_fields(self):
        self.repo.support_cards = [replace(self.support, rarity=10)]
        for query in ("EX 延长", "EX 技能延长", "LIVE 延长 EX", "EX LiVe=延长",
                      "激奏 luck EX 延长", "EX ＬＩＶＥ　延长 击奏 LUCK", "EX gekisou luck 延长",
                      "LIVE 延长 / 奶卡 EX luck", "EX 延长/奶卡 LUCK"):
            with self.subTest(query=query):
                result = resolve_command("/查留影卡 " + query, self.repo)
                self.assertEqual([card.id for card in result.support_cards], [1])
        for short, full, category in (("分卡", "分数提升", "score"), ("奶卡", "LIFE回复", "life"),
                                      ("判卡", "判定强化", "judgement")):
            self.repo.support_cards = [replace(self.support, catalog={
                **self.support.catalog, "categories": {"live": [category], "gekisou": ["LUCK"]}})]
            for query in (short, full, "LIVE=" + short, "LIVE " + full):
                with self.subTest(query=query):
                    result = resolve_command("/查留影卡 " + query, self.repo)
                    self.assertEqual([card.id for card in result.support_cards], [1])
        for query in ("EX LIVE", "激奏 FEVER", "LIVE 150"):
            with self.subTest(query=query):
                self.assertEqual(resolve_command("/查留影卡 " + query, self.repo).status, "invalid_arguments")

    def test_direct_snap_expanded_just_window_filters_the_effect(self):
        def snap(card_id, mission, expands, color=1):
            return replace(self.support, id=card_id, card_type=color, catalog={
                **self.support.catalog, "categories": {"live": ["duration"], "gekisou": [mission]},
                "just_window_expand": expands,
            })

        self.repo.support_cards = [snap(1, "JUST", True), snap(2, "JUST", False),
                                   snap(3, "COMBO", False), snap(4, "not_applicable", False)]
        for query, expected in (("扩判", [1]), ("红赤 激奏 扩判", [1]), ("激奏=扩判", [1]),
                                ("扩判/COMBO", [1, 3]), ("just", [1, 2]), ("EX 扩判", [])):
            with self.subTest(query=query):
                result = resolve_command("/查留影卡 " + query, self.repo)
                self.assertEqual([card.id for card in result.support_cards], expected)
        self.repo.support_cards.append(snap(5, "JUST", None, color=2))
        self.assertEqual(resolve_command("/查留影卡 扩判", self.repo).status, "data_unavailable")
        self.assertEqual([c.id for c in resolve_command("/查留影卡 扩判 红", self.repo).support_cards], [1])
        self.assertEqual(resolve_command("/查留影卡 LIVE=扩判", self.repo).status, "invalid_arguments")
        self.assertEqual(resolve_command("/查卡 激奏=扩判", self.repo).status, "invalid_arguments")

    def test_direct_snap_attributes_and_shortcuts_preserve_complete_titles(self):
        self.repo.support_cards = [replace(self.support, id=i, card_type=i) for i in range(1, 6)]
        for i, attribute in enumerate(("红赤", "绀碧", "翡翠", "琉金", "紫苑"), 1):
            for query in (attribute, f"属性={attribute}属性", f"颜色={attribute}"):
                with self.subTest(query=query):
                    result = resolve_command("/查留影卡 " + query, self.repo)
                    self.assertEqual([card.id for card in result.support_cards], [i])
        result = resolve_command("/查留影卡 属性=翡翠 / 黄 激奏=luck", self.repo)
        self.assertEqual([card.id for card in result.support_cards], [3, 4])

        self.repo.support_cards += [
            replace(self.support, id=51, card_type=4, title="翡翠"),
            replace(self.support, id=52, title="延长"),
            replace(self.support, id=53, title="激奏"),
        ]
        for query, expected in (("翡翠", [51]), ("属性=翡翠", [3]), ("延长 红", [52]),
                                ("卡名=延长", [52]), ("激奏", [53]), ("激奏=LUCK 黄", [4, 51])):
            with self.subTest(query=query):
                result = resolve_command("/查留影卡 " + query, self.repo)
                self.assertEqual([card.id for card in result.support_cards], expected)

    def test_snap_help_points_to_continue_for_long_lists(self):
        from ournotes_bot.commands import COMMAND_HELPS

        general = handle_command("/帮助", self.repo)
        snap = next(line for line in general.splitlines() if line.startswith("/查留影卡 "))
        self.assertIn("/继续", snap)
        self.assertNotIn("页N", snap)
        for command in ("/查留影卡76", "/查支援卡76"):
            with self.subTest(command=command):
                help_text = handle_command(command, self.repo)
                self.assertIn("/继续", help_text)
                self.assertIn("/查留影卡 EX 延长", help_text)
                self.assertNotIn("页N", help_text)
        for locale, old_page in (("en", "[page N]"), ("ja", "[ページN]")):
            with self.subTest(locale=locale):
                help_text = COMMAND_HELPS[locale]["support_cards"]
                self.assertIn("/继续", help_text)
                self.assertNotIn(old_page, help_text)

    def test_member_leader_types_compose_with_card_and_live_filters(self):
        self.repo.cards = [
            replace(self.base, id=card_id, card_type=4 if leader == "异色" else 1,
                    catalog={**self.base.catalog, "member_leader_type": leader,
                             "live_score_bands": [{"value": 15500, "condition": "perfect"}]})
            for card_id, leader in enumerate(("同色", "同技", "同激", "异色", ""), 1)
        ]
        for command, expected in (
            ("/查卡 队长 同色", [1]),
            ("/查卡 SSR 队长=同技/同激", [2, 3]),
            ("/查卡 mygo 队长 异色 黄 155", [4]),
            ("/查角色卡 155 黄 队长:异色 mygo", [4]),
            ("/查卡 红 队长 异色", []),
        ):
            with self.subTest(command=command):
                answer = resolve_command(command, self.repo).catalog
                self.assertFalse(answer.error, answer.error)
                self.assertEqual(answer.request.mode, "list")
                self.assertEqual([card.id for card in answer.cards], expected)
        self.assertEqual(resolve_command("/查卡 队长 技巧", self.repo).status, "invalid_arguments")
        self.assertEqual(resolve_command("/查留影卡 队长=同色", self.repo).status, "invalid_arguments")

    def test_member_leader_unknowns_only_block_when_other_filters_match(self):
        known = replace(self.base, catalog={**self.base.catalog, "member_leader_type": "同色"})
        self.repo.cards = [known, replace(self.base, id=2, card_type=4)]
        for query in ("红 队长 同色", "队长 同色 红"):
            answer = resolve_command("/查卡 " + query, self.repo).catalog
            self.assertEqual([card.id for card in answer.cards], [1])
        result = resolve_command("/查卡 队长 同色", self.repo)
        self.assertEqual(result.status, "data_unavailable")
        self.assertIn("队长技能分类", result.catalog.error)

    def test_direct_member_live_score_scope_ranges_and_explicit_id(self):
        def card(card_id, color, score, condition, *, band="MyGO!!!!!"):
            bands = [{"value": value * 100, "condition": condition} for value in score]
            return replace(self.base, id=card_id, card_type=color, band=band,
                           title=f"Sample {card_id}", catalog={**self.base.catalog,
                               "live_score_bands": bands, "live_score_summary": "演出条件样本"})

        self.repo.cards = [card(51, 4, (120, 140), "life"), card(53, 3, (150,), "perfect"),
                           card(72, 4, (145, 155), "perfect"),
                           card(150, 3, (155,), "", band="Ave Mujica")]
        for query, expected in (
            ("mygo 155", [72]), ("155 MyGO", [72]), ("１５５％ MyGO", [72]),
            ("颜色=黄 SSR MyGO 155", [72]), ("mygo 血量加分 140", [51]),
            ("mygo 演出=血量加分 140", [51]), ("mygo perfect加分 155", [72]),
            ("mygo 血量加分 155", []), ("mygo 150", [53]), ("mygo 145", [72]),
            ("演出155", [72, 150]), ("155%", [72, 150]), ("演出 155%", [72, 150]),
            ("mygo 120/150/155", [51, 53, 72]), ("mygo 150以上", [53, 72]),
            ("mygo 120以下", [51]), ("mygo 演出120到155", [51, 53, 72]),
            ("mygo JUST 155", [72]), ("卡名:Sample 72 黄 155", [72]),
        ):
            with self.subTest(query=query):
                answer = resolve_command("/查卡 " + query, self.repo).catalog
                self.assertFalse(answer.error, answer.error)
                self.assertEqual((answer.request.mode, [c.id for c in answer.cards]), ("list", expected))
        for query in ("150", "id 150 Ave Mujica", "Ave Mujica ID=150"):
            with self.subTest(query=query):
                answer = resolve_command("/查卡 " + query, self.repo).catalog
                self.assertEqual((answer.request.mode, [c.id for c in answer.cards]), ("detail", [150]))
        self.assertEqual(resolve_command("/查卡 mygo 演出155到120", self.repo).status, "invalid_arguments")

    def test_live_shortcuts_allow_multiple_effects_and_missing_scores_stay_unknown(self):
        composite = {**self.base.catalog, "categories": {"live": ["score", "life", "judgement"]},
                     "live_score_bands": [{"value": 15000, "condition": "perfect"}]}
        self.repo.cards = [replace(self.base, catalog=composite), replace(self.base, id=2, card_type=2)]
        for name in ("分卡", "奶卡", "判卡"):
            self.assertEqual([c.id for c in resolve_command("/查卡 红 " + name, self.repo).cards], [1])
        self.assertEqual(resolve_command("/查卡 红 奶卡 150", self.repo).status, "empty")
        self.assertEqual(resolve_command("/查卡 mygo 150", self.repo).status, "data_unavailable")
        self.assertEqual([c.id for c in resolve_command("/查卡 红 PERFECT加分 150", self.repo).cards], [1])

    def test_known_title_keeps_digits_in_skill_shaped_text(self):
        self.repo.cards[0] = replace(self.base, title="演出155")
        result = resolve_command("/查卡 红 演出155", self.repo).catalog
        self.assertEqual([c.id for c in result.cards], [1])
        self.assertFalse(result.request.has_live_details)

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

    def test_excluded_unknown_card_does_not_block_either_filter_order(self):
        self.repo.cards[2] = replace(self.repo.cards[2], skills=(), catalog={})
        for text in ("角色=高松灯 LIVE=分数提升", "LIVE=分数提升 角色=高松灯"):
            self.assertEqual(self.ids(text), [1, 2])
        self.repo.cards[1] = replace(self.repo.cards[1], skills=(), catalog={})
        self.assertEqual(query_cards("角色=高松灯 LIVE=分数提升", self.repo).status, "data_unavailable")

    def test_known_mismatch_excludes_unknown_tags_and_snap_links(self):
        self.repo.cards[2] = replace(self.repo.cards[2], catalog={})
        for text in ("角色=高松灯 得意=MyGO", "得意=MyGO 角色=高松灯"):
            self.assertEqual(self.ids(text), [1, 2])
        unknown = replace(self.support, id=2, card_type=2, catalog={"character_links_complete": False})
        self.repo.support_cards.append(unknown)
        for text in ("角色=高松灯 颜色=红色", "颜色=红色 角色=高松灯"):
            self.assertEqual(self.ids(text, True), [1])
        self.assertEqual(query_cards("角色=高松灯", self.repo, support=True).status, "data_unavailable")

    def test_cold_refresh_preserves_valid_expired_details_and_legacy_rows(self):
        self.repo.metadata = dict(source=yatta.BASE, schema=self.repo.CACHE_SCHEMA)
        self.repo._save_cache()
        os.utime(self.repo.cache_file, (0, 0))
        cold = SongRepository(yatta.BASE, self.repo.cache_file)
        with patch.object(yatta, "fetch_json", return_value={}), \
             patch.object(yatta, "build_data", return_value=([], [replace(self.base, skills=(), catalog={})])), \
             patch.object(yatta, "build_support_cards", return_value=[replace(self.support, skills=(), catalog={})]), \
             patch.object(yatta, "card_detail", side_effect=OSError("offline")), \
             patch.object(yatta, "support_card_detail", side_effect=OSError("offline")):
            cold.load()
        for actual, previous in ((cold.cards[0], self.base), (cold.support_cards[0], self.support)):
            self.assertEqual(actual.skills, previous.skills)
            self.assertTrue(actual.catalog["detail_stale"])
        saved = json.loads(cold.cache_file.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["member_skills"]["1"]), 2)
        self.assertEqual(len(saved["support_cards"][0]["skills"]), 2)
        self.assertEqual(Card(**saved["cards"][0]).id, 1)

    def test_bad_or_foreign_cache_cannot_supply_details_to_refresh(self):
        for foreign in (False, True):
            self.repo.metadata = dict(source=yatta.BASE, schema=self.repo.CACHE_SCHEMA)
            self.repo._save_cache()
            raw = json.loads(self.repo.cache_file.read_text(encoding="utf-8"))
            if foreign:
                raw["metadata"]["source"] = "https://other.invalid"
            else:
                raw["support_cards"] = [{"id": 1}]
            self.repo.cache_file.write_text(json.dumps(raw), encoding="utf-8")
            cold = SongRepository(yatta.BASE, self.repo.cache_file)
            with patch.object(yatta, "fetch_json", return_value={}), \
                 patch.object(yatta, "build_data", return_value=([], [replace(self.base, skills=(), catalog={})])), \
                 patch.object(yatta, "build_support_cards", return_value=[]), \
                 patch.object(yatta, "card_detail", side_effect=OSError("offline")):
                cold.refresh()
            self.assertEqual(cold.cards[0].skills, ())
            self.assertFalse(cold.cards[0].catalog.get("detail_stale"))

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
                self.assertLessEqual(len(blob), 3_000_000)
                self.assertLess(image.height, 24000)
            self.assertIsNone(render_catalog(query_cards("1", self.repo, art=True)))

    def test_art_preserves_aspect_ratio(self):
        with patch("ournotes_bot.visuals._asset", return_value=Image.new("RGBA", (512, 288))):
            blob = render_catalog(query_cards("1", self.repo, support=True, art=True))
        self.assertEqual(Image.open(io.BytesIO(blob)).size, (512, 288))


if __name__ == "__main__":
    unittest.main()
