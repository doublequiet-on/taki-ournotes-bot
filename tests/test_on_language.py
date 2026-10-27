"""Fixed synthetic ON cases; no game-stat claims, network, QQ or paid AI."""
import copy
import json
import re
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ournotes_bot.commands import resolve_command
from ournotes_bot.data import Chart
from ournotes_bot.natural_query.query_capabilities import CAPABILITIES
from ournotes_bot.natural_query.query_validation import validate_capability_action, OutcomeCode
from ournotes_bot.sources.haneoka.song_traits import SongTraits
from ournotes_bot.sources.moenotes_events import EventSnapshot
from ournotes_bot.structured_query import resolve_query
import test_query_spec
from test_info_queries import catalog, person, gacha, activity, CurrentSource, NOW

CASES = json.loads((Path(__file__).parent / "fixtures/on_language.json").read_text("utf-8"))


class OnLanguageTests(unittest.TestCase):
    def test_short_entity_names_do_not_split_color_filter_words(self):
        self.repo.songs.append(replace(self.repo.songs[0], id=999999,
                                      title="颜", titles=("颜",), localized={}))
        self.repo.songs.append(replace(self.repo.songs[0], id=999998,
                                      title="蓝", titles=("蓝",), localized={}))
        cases = (
            ("颜色为蓝色的歌曲有哪些", "/查曲 颜色=蓝"),
            ("顏色為藍色的歌曲有哪些", "/查曲 颜色=蓝"),
            ("蓝色角色有哪些", "/查卡 颜色=蓝"),
            ("颜色是蓝色的留影卡有哪些", "/查留影卡 颜色=蓝"),
            ("颜的蓝色歌曲", "/查曲 颜 颜色=蓝"),
        )
        with patch.object(self.parser, "_request", side_effect=AssertionError("model called")):
            for question, command in cases:
                with self.subTest(question=question):
                    outcome = self.parser.answer_with_outcome("/问 " + question, self.repo)
                    self.assertEqual(outcome.code, OutcomeCode.SUCCESS, outcome.text)
                    direct = resolve_command(command, self.repo)
                    self.assertEqual(outcome.result.songs or outcome.result.cards or outcome.result.support_cards,
                                     direct.songs or direct.cards or direct.support_cards)

    def test_color_phrases_are_filters_not_entity_names(self):
        cases = [
            ("蓝色角色有哪些", "/查卡 蓝色", [1, 2, 3]),
            ("蓝色的成员有哪些", "/查卡 蓝色", [1, 2, 3]),
            ("哪些角色卡是蓝色", "/查卡 蓝色", [1, 2, 3]),
            ("有哪些红卡", "/查卡 红色", [6]),
            ("蓝属性的成员卡有哪些", "/查卡 蓝色", [1, 2, 3]),
            ("属性为蓝色的成员卡有哪些", "/查卡 属性=蓝色", [1, 2, 3]),
            ("颜色是蓝色的留影卡有哪些", "/查留影卡 蓝色", [7]),
            ("蓝属性的歌有哪些", "/查曲 蓝色", [100001, 100002]),
            ("有哪些蓝歌", "/查曲 蓝", [100001, 100002]),
            ("颜色为蓝色的歌曲有哪些", "/查曲 蓝色", [100001, 100002]),
            ("颜色：蓝色的歌曲有哪些", "/查曲 颜色： 蓝色", [100001, 100002]),
            ("颜色是蓝色或绿色的歌曲有哪些", "/查曲 颜色=蓝/绿", [100001, 100002, 100003]),
            ("角色=tmr 属性=蓝色的成员卡有哪些", "/查卡 角色=tmr 颜色=蓝色", [1, 2, 3]),
        ]
        with patch.object(self.parser, "_request", side_effect=AssertionError("model called")), \
             patch.object(self.parser._quota, "reserve", side_effect=AssertionError("quota used")):
            for question, command, ids in cases:
                with self.subTest(question=question):
                    outcome = self.parser.answer_with_outcome("/问 " + question, self.repo)
                    self.assertEqual(outcome.code, OutcomeCode.SUCCESS, outcome.text)
                    actual = outcome.result
                    direct = resolve_command(command, self.repo)
                    for result in (actual, direct):
                        records = result.songs or result.cards or result.support_cards
                        self.assertEqual(sorted(c.id for c in records), ids)
                    self.assertEqual(outcome.state.model_calls, 0)

    def test_filter_dimensions_and_order_through_commands_and_questions(self):
        cases = [
            ("查卡", "成员卡", ["颜色=蓝", "稀有度=SSR/SR", "角色=tmr", "乐队=MyGO", "LIVE=分数提升", "击奏=JUST", "得意=MyGO"], [1, 2]),
            ("查留影卡", "留影卡", ["颜色=蓝", "稀有度=EX", "角色=tmr", "LIVE=分数提升", "击奏=JUST"], [7]),
            ("查曲", "歌曲", ["颜色=蓝", "乐队=MyGO", "EX", "lv<=25", "激奏=JUST"], [100002]),
        ]
        with patch.object(self.parser, "_request", side_effect=AssertionError("model called")):
            for command, noun, fields, expected in cases:
                for index in range(len(fields)):
                    ordered = fields[index:] + fields[:index]
                    for query in (" ".join(ordered), " ".join(reversed(ordered))):
                        with self.subTest(command=command, query=query):
                            direct = resolve_command("/" + command + " " + query, self.repo)
                            outcome = self.parser.answer_with_outcome("/问 " + query + "的" + noun + "有哪些", self.repo)
                            self.assertEqual(outcome.code, OutcomeCode.SUCCESS, outcome.text)
                            for result in (direct, outcome.result):
                                records = result.songs or result.cards or result.support_cards
                                self.assertEqual(sorted(c.id for c in records), expected)

    def test_color_names_remain_entities_and_unknown_filters_do_not_broaden(self):
        song = replace(self.repo.songs[0], title="蓝", titles=("蓝",), localized={})
        self.repo.songs[0] = song
        self.assertEqual(resolve_command("/查曲 蓝", self.repo).songs, (song,))
        with patch.object(self.parser, "_request", side_effect=AssertionError("model called")):
            for question in ("颜色为黑色的歌曲有哪些", "黑属性的成员卡有哪些",
                             "颜色为蓝色 不存在的歌曲有哪些", "蓝色角色有哪些但只要未拥有的"):
                outcome = self.parser.answer_with_outcome("/问 " + question, self.repo)
                self.assertNotIn(outcome.code, (OutcomeCode.SUCCESS, OutcomeCode.EMPTY), question)

    def setUp(self):
        test_query_spec.QuerySpecificationTests.setUp(self)
        base = self.repo.songs[0]
        self.repo.songs += [replace(base, id=100003, title="合成丙", titles=("合成丙",), localized={},
            traits=SongTraits(3, ("COMBO", "JUST", "LUCK")), charts=(Chart("EXPERT",26,26.2,500,""),)),
            replace(base, id=100004, title="合成丁", titles=("合成丁",), localized={},
            traits=SongTraits(1, ("COMBO",)), charts=(Chart("EXPERT",27,27.0,400,""),)),
            replace(base, id=100005, title="缺资料曲", titles=("缺资料曲",), localized={},
            traits=None, charts=(Chart("EXPERT",28,28.0,600,""),))]
        card = self.repo.cards[0]
        self.repo.cards = [replace(c, catalog={"categories":{"live":["score"],"gekisou":["JUST"]},
            "tags":{"1":{"zh":"MyGO!!!!!"}}, "character_ids":[1], "character_links_complete":True}) for c in self.repo.cards]
        for ident, rarity, color, live, gek in [(4,4,3,"life","COMBO"),(5,2,3,"judgement","LUCK"),(6,4,1,"score","JUST")]:
            self.repo.cards.append(replace(card, id=ident, title=f"合成卡{ident}", character="丰川祥子", band="Ave Mujica", localized={},
                rarity=rarity, card_type=color, catalog={"categories":{"live":[live],"gekisou":[gek]},
                "tags":{"2":{"zh":"Ave Mujica"}}, "character_ids":[6],"character_links_complete":True}))
        support = self.repo.support_cards[0]
        self.repo.support_cards = [replace(support,catalog={"categories":{"live":["score"],"gekisou":["JUST"]}}),
            replace(support,id=8,rarity=4,card_type=3,catalog={"categories":{"live":["life"],"gekisou":["COMBO"]}}),
            replace(support,id=9,rarity=3,card_type=1,catalog={"categories":{"live":["duration"],"gekisou":["LUCK"]}})]
        snapshot = catalog(cards=tuple(self.repo.cards),support_cards=tuple(self.repo.support_cards), songs=tuple(self.repo.songs),
            characters=(person(1,"高松灯",birthday=(11,22)), person(6,"丰川祥子","Ave Mujica",birthday=(2,14))),
            activities=(activity(11,"合成活动"), activity(12,"旧活动",end=NOW-1)),
            gachas=(gacha(21,"合成卡池"),gacha(22,"旧卡池",end=NOW-1)))
        for target, value in (("capture_catalog", lambda:snapshot),("card_with_detail",lambda c:c),("support_card_with_detail",lambda c:c)):
            mock = patch.object(self.repo,target,side_effect=value); mock.start();self.addCleanup(mock.stop)
        self.repo.event_cutoffs = CurrentSource(EventSnapshot("jp","11","当前活动","nowOn",(NOW-100)*1000,(NOW+100)*1000,(),source="haneoka"))
        timer = patch("time.time",return_value=NOW);timer.start();self.addCleanup(timer.stop)

    def test_fixed_questions_conditions_ids_outcomes_and_zero_model(self):
        rows = CASES["cases"]
        self.assertGreaterEqual(len(rows),120)
        self.assertGreaterEqual(sum(r["split"]=="heldout" for r in rows), len(rows)/4)
        self.assertEqual(len(rows),len({r["question"] for r in rows}))
        with patch.object(self.parser,"_request",side_effect=AssertionError("model called")), patch.object(self.parser._quota,"reserve",side_effect=AssertionError("quota used")):
            for row in rows:
                with self.subTest(question=row["question"],split=row["split"]):
                    actual = self.parser.answer_with_outcome("/问 "+row["question"],self.repo)
                    self.assertEqual(actual.code.value,row["status"],actual.text)
                    self.assertEqual(actual.state.model_calls,0)
                    if not row.get("command"):
                        continue
                    direct = resolve_command(row["command"],self.repo)
                    result = actual.result
                    self.assertIsNotNone(result,actual.text)
                    if row["kind"]=="song":
                        self.assertEqual([s.id for s in result.songs],row["ids"])
                        self.assertEqual([s.id for s in direct.songs],row["ids"])
                        if result.song_selection is not None:
                            a,b=result.song_selection.request,direct.song_selection.request
                            self.assertEqual((a.colors,a.mode,a.missions,a.difficulty,a.comparison,a.level,a.integer_level),
                                             (b.colors,b.mode,b.missions,b.difficulty,b.comparison,b.level,b.integer_level))
                    elif row["kind"]=="chart":
                        self.assertEqual([result.chart[0].id],row["ids"])
                        self.assertEqual([c.difficulty for c in result.chart[1]],row["difficulties"])
                        self.assertEqual(result.chart[0],direct.songs[0])
                        self.assertEqual(result.chart[1],tuple(c for c in direct.songs[0].charts if not direct.parsed[2] or c.difficulty==direct.parsed[2]))
                    elif row["kind"] in {"card","support_card"}:
                        self.assertEqual(sorted(c.id for c in result.catalog.cards),row["ids"])
                        self.assertEqual(sorted(c.id for c in direct.catalog.cards),row["ids"])
                        self.assertEqual(result.catalog.request.filters,direct.catalog.request.filters)
                        self.assertEqual([c.id for c in result.catalog.cards],[c.id for c in direct.catalog.cards])
                    else:
                        a = result.activity if result.activity is not None else result.info
                        b = direct.activity if direct.activity is not None else direct.info
                        self.assertEqual(a,b)
                        ids = sorted({int(x) for x in re.findall(r"(?:角色|活动|卡池) ID (\d+)",a.text)})
                        self.assertEqual(ids,row["ids"])

    def test_model_typed_dimensions_and_grounding(self):
        cases = [
            ("song.search","蓝色EX25级歌曲",{"colors":[2],"difficulty":"EXPERT","level_operator":"eq","level":25,"integer_level":True},[100001,100002]),
            ("song.search","蓝色EX25.0级歌曲",{"colors":[2],"difficulty":"EXPERT","level_operator":"eq","level":25,"integer_level":False},[100002]),
            ("song.search","激奏顺序是JUST/JUST/COMBO的歌曲",{"mission_mode":"exact","missions":["JUST","JUST","COMBO"]},[100001]),
            ("song.search","纯JUST的歌曲",{"mission_mode":"pure","missions":["JUST"]},[100002]),
            ("song.search","混合激奏的歌曲",{"mission_mode":"mixed"},[100001,100003]),
            ("member_card.search","蓝色SSR或SR成员卡",{"colors":[2],"rarities":[4,3]},[1,2]),
            ("member_card.search","生日卡有哪些",{"rarities":[20]},[3]),
            ("support_card.search","EX SNAP有哪些",{"rarities":[10]},[7]),
            ("member_card.search","LIVE分数提升的成员卡",{"live_categories":["score"]},[1,2,3,6]),
            ("support_card.search","LIVE技能延长的SNAP",{"live_categories":["duration"]},[9]),
            ("member_card.search","得意MyGO的成员卡",{"tags":["1"]},[1,2,3]),
            ("member_card.search","角色=tmr或skk 颜色=蓝的成员卡",{"characters":["高松灯","丰川祥子"],"colors":[2]},[1,2,3]),
        ]
        for name,question,args,ids in cases:
            with self.subTest(question=question):
                cap=CAPABILITIES[name]
                payload={"action":"call_tool","capability":name,"arguments":{"query":"",**args}}
                result=validate_capability_action(payload,cap,question,self.repo,allow_empty_subject=True)
                self.assertIsNotNone(result.spec,result)
                resolved=resolve_query(result.spec,self.repo)
                records=resolved.songs or resolved.cards or resolved.support_cards
                self.assertEqual(sorted(r.id for r in records),ids)
                for key in args:
                    broken=copy.deepcopy(payload);broken["arguments"].pop(key)
                    rejected=validate_capability_action(broken,cap,question,self.repo,allow_empty_subject=True)
                    if args[key] is not False:
                        self.assertIsNone(rejected.spec,(question,key))
        for key,value in (("colors",[True]),("colors",["blue"]),("colors",[[2]]),("missions",["ALL"]),("integer_level",1),("rarities",[10])):
            name="member_card.search" if key=="rarities" else "song.search"
            self.assertIn(key,CAPABILITIES[name].invalid_fields({"query":"",key:value}))

    def test_no_model_command_escape_or_silent_filter_loss(self):
        bad = [
            ("member_card.search","蓝色SSR成员卡",{"query":"","rarity":4}),
            ("member_card.search","高松灯的蓝色成员卡",{"query":"丰川祥子","characters":["高松灯"],"colors":[2]}),
            ("song.search","蓝色的歌曲",{"query":"迷星叫","colors":[2]}),
            ("song.search","蓝色的歌曲",{"query":"","colors":[2],"mission_mode":"pure","missions":["JUST"]}),
            ("song.search","激奏顺序是JUST/JUST/COMBO的歌曲",{"query":"","mission_mode":"exact","missions":["JUST","COMBO","JUST"]}),
            ("member_card.search","蓝色SSR或SR成员卡",{"query":"颜色=蓝 稀有度=SSR/SR"}),
            ("support_card.search","EX SNAP",{"query":"","rarities":[20]}),
            ("member_card.search","生日卡",{"query":"","rarities":[10]}),
            ("member_card.search","蓝色SSR成员卡但只要未拥有的",{"query":"","colors":[2],"rarities":[4]}),
        ]
        for name,q,args in bad:
            with self.subTest(q=q,args=args):
                result=validate_capability_action({"action":"call_tool","capability":name,"arguments":args},CAPABILITIES[name],q,self.repo,allow_empty_subject=True)
                self.assertIsNone(result.spec)

    def test_entity_titles_survive_grammar_and_conditions(self):
        song=replace(self.repo.songs[0],title="蓝色的歌",titles=("蓝色的歌",),localized={})
        self.repo.songs[0]=song
        self.repo.cards[0]=replace(self.repo.cards[0],title="生日的约定",localized={})
        for question,command in [("蓝色的歌的27级歌曲","/查曲 蓝色的歌 lv=27"),("生日的约定的蓝色成员卡","/查卡 生日的约定 颜色=蓝")]:
            with self.subTest(question=question), patch.object(self.parser,"_request",side_effect=AssertionError("model")):
                answer=self.parser.answer_with_outcome("/问 "+question,self.repo)
                direct=resolve_command(command,self.repo)
                self.assertEqual(answer.code.value,direct.status)
                self.assertIsNotNone(answer.result)

    def test_information_cache_reexecutes_current_outcome(self):
        with patch.object(self.parser,"_request",side_effect=AssertionError("model")):
            first=self.parser.answer_with_outcome("/问 现在的卡池有哪些",self.repo)
            self.assertEqual(first.code,OutcomeCode.SUCCESS)
            with patch.object(self.repo,"capture_catalog",return_value=catalog()):
                second=self.parser.answer_with_outcome("/问 现在的卡池有哪些",self.repo)
            self.assertEqual(second.code,OutcomeCode.EMPTY)
            self.assertEqual(second.state.processing_route,"cache")

    def test_extended_model_repair_budget_and_cached_reexecution(self):
        question = "蓝色SSR或SR成员卡"
        good = {"action":"call_tool","capability":"member_card.search","arguments":{"query":"","colors":[2],"rarities":[4,3]}}
        bad = copy.deepcopy(good); bad["arguments"]["colors"] = [3]
        with patch("ournotes_bot.natural_query.query_agent.parse_local_query", return_value=None), patch.object(self.parser,"_request",side_effect=[bad,good]) as model:
            first=self.parser.answer_with_outcome("/问 "+question,self.repo)
            self.assertEqual(first.code,OutcomeCode.SUCCESS)
            self.assertEqual(first.state.repairs,1)
            self.assertEqual(first.state.model_calls,2)
            self.assertEqual(first.state.tool_executions,1)
            self.assertEqual(sorted(c.id for c in first.result.cards),[1,2])
            self.repo.cards=[replace(c,card_type=5) for c in self.repo.cards]
            second=self.parser.answer_with_outcome("/问 "+question,self.repo)
            self.assertEqual(second.code,OutcomeCode.EMPTY)
            self.assertEqual(model.call_count,2)

    def test_wrong_types_and_fabricated_constraints_are_rejected(self):
        cap=CAPABILITIES["song.search"]
        for field in ("difficulty","level_operator","mission_mode"):
            for value in ([],{},True):
                with self.subTest(field=field,value=value):
                    self.assertIn(field,cap.invalid_fields({"query":"",field:value}))
        for name in ("activity.get","gacha.search","character.get"):
            self.assertNotIn(name,CAPABILITIES)
        name="song.search"
        for args in ({"query":"迷星叫"},{"query":"迷星叫","colors":[2]}):
            result=validate_capability_action({"action":"call_tool","capability":name,"arguments":args},CAPABILITIES[name],"迷星叫蓝色歌曲但只要未拥有的",self.repo)
            self.assertIsNone(result.spec)
        legacy=validate_capability_action({"intent":"song","query":"迷星叫"},cap,"迷星叫蓝色歌曲",self.repo)
        self.assertIsNone(legacy.spec)

    def test_missing_unknown_ambiguous_and_zero_stay_distinct(self):
        with patch.object(self.parser,"_request",side_effect=AssertionError("model")), patch.object(self.parser._quota,"reserve",side_effect=AssertionError("quota")):
            missing=self.parser.answer_with_outcome("/问 缺资料曲的蓝色歌曲",self.repo)
            self.assertEqual(missing.code,OutcomeCode.DATA_UNAVAILABLE)
            unknown=self.parser.answer_with_outcome("/问 未知曲的蓝色歌曲",self.repo)
            self.assertEqual(unknown.code,OutcomeCode.UNKNOWN_ENTITY)
            zero=self.parser.answer_with_outcome("/问 100001 HD Note",self.repo)
            self.assertEqual(zero.code,OutcomeCode.SUCCESS)
            self.assertIn("0 Notes",zero.text)
            self.repo.songs.append(replace(self.repo.songs[0],id=100099))
            ambiguous=self.parser.answer_with_outcome("/问 迷星叫的蓝色歌曲",self.repo)
            self.assertEqual(ambiguous.code,OutcomeCode.AMBIGUOUS)

    def test_page_keeps_filters_once_in_local_and_model_plans(self):
        q="蓝色SSR或SR成员卡第2页"
        with patch.object(self.parser,"_request",side_effect=AssertionError("model")), patch.object(self.parser._quota,"reserve",side_effect=AssertionError("quota")):
            local=self.parser.answer_with_outcome("/问 "+q,self.repo)
            self.assertEqual(local.code,OutcomeCode.SUCCESS)
            self.assertEqual(local.result.spec.page,2)
            self.assertEqual(sorted(c.id for c in local.result.cards),[1,2])
            self.assertNotIn("页",local.result.spec.card_request.query)
        action={"action":"call_tool","capability":"member_card.search","arguments":{"query":"","colors":[2],"rarities":[4,3],"page":2}}
        grounded=validate_capability_action(action,CAPABILITIES["member_card.search"],q,self.repo,allow_empty_subject=True)
        self.assertIsNotNone(grounded.spec)
        self.assertEqual(grounded.spec.page,2)
        self.assertEqual(sorted(c.id for c in resolve_query(grounded.spec,self.repo).cards),[1,2])

    def test_negated_range_words_use_shared_level_evidence(self):
        for phrase, ids in (("不高于25级",[100002]),("至少25级",[100001,100002]),("不到25级",[])):
            with self.subTest(phrase=phrase), patch.object(self.parser,"_request",side_effect=AssertionError("model")):
                result=self.parser.answer_with_outcome("/问 蓝色"+phrase+"的歌曲",self.repo)
                self.assertIsNotNone(result.result,result.text)
                self.assertEqual([s.id for s in result.result.songs],ids)
