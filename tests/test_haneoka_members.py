import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ournotes_bot.sources.haneoka.haneoka_members import MemberRepository, parse, fetch_json, UNKNOWN


class HaneokaMemberTests(unittest.TestCase):
    def setUp(self):
        self.saved = json.loads((Path(__file__).parent / "fixtures/haneoka_member_list.json").read_text(encoding="utf-8"))
        self.saved["fetched_at"] = 1000
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "member.json"

    def fetch(self, path):
        return self.saved["identity"] if path.startswith("release?") else self.saved["documents"][path.split("?")[0]]

    def new_cards(self):
        return json.loads((Path(__file__).parent / "fixtures/haneoka_member_new_cards.json").read_text(encoding="utf-8"))

    def local_card(self, saved, key="64"):
        row = saved["documents"]["cards"][key]
        yume = saved["project_yume_identities"][key]
        return SimpleNamespace(id=yume["id"], asset_id=yume["id"], rarity=yume["rarity"],
            card_type=yume["attribute"], catalog={"character_ids": [yume["character"]],
            "skill_evidence": [{"kind": "liveSkill", "id": row["liveSkillId"]},
                               {"kind": "gekisouSkill", "id": row["gekisouSkillId"]}]})

    def test_new_leaders_keep_independent_targets_and_birthday_dual_effect(self):
        saved = self.new_cards()
        snapshot = parse(saved)
        self.assertEqual(snapshot.summaries["61"][0],
            ("表演值提升", "蓝色成员 +102%\n夢限大みゅーたいぷ成员 +48%"))
        self.assertEqual(snapshot.summaries["62"][0],
            ("表现值提升", "夢限大みゅーたいぷ成员 +132%\nJUST激奏成员 +18%"))
        self.assertEqual(snapshot.summaries["64"], (
            ("表演值提升", "一家Dumb Rock!成员 +85%\n紫色成员 +40%"),
            ("得分 +115%", "5秒 · 无额外条件"),
            ("LUCK条增量 +200%", "LUCK激奏期间\nBAD以下扣血 -20%")))
        for key in ("61", "62", "64"):
            self.assertEqual(snapshot.for_card(self.local_card(saved, key)), snapshot.summaries[key])

    def test_new_contracts_reject_changed_targets_and_damage_mechanics(self):
        saved = self.new_cards()
        next(row for row in saved["documents"]["skill-reference"]["targets"]
             if row["raw"]["_id"] == 9)["raw"]["_cardType"] = 5
        self.assertEqual(parse(saved).summaries["61"][0], UNKNOWN)
        saved = self.new_cards()
        saved["documents"]["gekisou-skills"]["22"]["effects"][1]["raw"]["_skillEffectType"] = 999
        self.assertEqual(parse(saved).summaries["64"][2], UNKNOWN)

    def test_new_card_refreshes_recent_snapshot_without_relaxing_identity(self):
        complete = self.new_cards()
        recent = copy.deepcopy(complete)
        recent["fetched_at"] = 1000
        del recent["documents"]["cards"]["64"]
        self.path.write_text(json.dumps(recent), encoding="utf-8")
        self.saved = complete
        fetch = Mock(side_effect=self.fetch)
        repo = MemberRepository(self.path, fetch=fetch, clock=lambda: 1100)
        card = self.local_card(complete)
        snapshot = repo.get([card])
        self.assertEqual(fetch.call_count, 6)
        self.assertEqual(snapshot.for_card(card), snapshot.summaries["64"])
        card.asset_id = 1
        self.assertEqual(repo.get([card]).for_card(card)[0][0], "映射未确认")
        self.assertEqual(fetch.call_count, 6)  # shared 300-second retry throttle

    def test_missing_new_card_refresh_failure_keeps_old_cards_and_retries_bounded(self):
        complete = self.new_cards()
        recent = copy.deepcopy(complete)
        recent["fetched_at"] = 1000
        del recent["documents"]["cards"]["64"]
        self.path.write_text(json.dumps(recent), encoding="utf-8")
        fetch = Mock(side_effect=OSError("offline"))
        repo = MemberRepository(self.path, fetch=fetch, clock=lambda: 1100)
        card = self.local_card(complete)
        snapshot = repo.get([card])
        self.assertTrue(snapshot.stale)
        self.assertIn("61", snapshot.cards)
        self.assertEqual(snapshot.for_card(card)[0][0], "映射未确认")
        repo.get([card])
        fetch.assert_called_once()

    def test_public_samples_and_units(self):
        rows = parse(self.saved).summaries
        self.assertEqual(rows["51"][1:], (("得分 +120→140%", "5秒 · LIFE≥700取后值"), ("JUST +2→4", "第1次起 · LIFE≥700取后值")))
        self.assertEqual(rows["52"][2], ("LUCK条增量 +300%", "LUCK激奏期间"))
        self.assertEqual(rows["28"][2], ("LUCK点数 +40pt", "触发LUCKY RUSH时"))
        self.assertEqual(rows["43"][2], ("LUCK起始条100%", "概率43→65% · LIFE≥700"))
        self.assertEqual(rows["53"][1], ("得分 +150%", "5秒 · PERFECT以上"))
        self.assertEqual(rows["53"][2], ("COMBO +5", "保持激奏COMBO≥25"))
        self.assertEqual(rows["31"][2], ("JUST 每1次 +1", "JUST激奏 · 最多4次"))

    def test_leader_attributes_targets_and_additional_bonus_stay_separate(self):
        rows = parse(self.saved).summaries
        self.assertEqual(rows["60"][0], ("技巧值 +132%", "Ave Mujica成员\n演出【判定】成员另+18%"))
        self.assertEqual(rows["51"][0], ("表演值 +132%", "MyGO!!!!!成员\n演出【生命值】成员另+18%"))
        self.assertEqual(rows["53"][0], ("全属性 +47%", "MyGO!!!!!成员"))
        self.assertEqual(rows["1"][0], ("表演值 +72%", "MyGO!!!!!成员"))
        self.assertTrue(all(len(row) == 3 for row in rows.values()))
        self.assertNotIn("150%", str(rows["60"][0]))

    def test_changed_leader_targets_are_not_reinterpreted(self):
        targets = self.saved["documents"]["skill-reference"]["targets"]
        next(t for t in targets if t["raw"]["_id"] == 50)["raw"]["_bandID"] = 2
        self.assertEqual(parse(self.saved).summaries["60"][0], UNKNOWN)
        self.assertNotEqual(parse(self.saved).summaries["60"][1], UNKNOWN)

    def test_old_cache_without_leader_retains_live_and_gekisou_if_upgrade_fails(self):
        del self.saved["documents"]["leader-skills"]
        self.path.write_text(json.dumps(self.saved), encoding="utf-8")
        fetch = Mock(side_effect=OSError("offline"))
        repo = MemberRepository(self.path, fetch=fetch, clock=lambda: 1000)
        snapshot = repo.get()
        self.assertFalse(snapshot.leader_ready)
        self.assertTrue(snapshot.stale)
        self.assertEqual(snapshot.summaries["60"][0], UNKNOWN)
        self.assertNotEqual(snapshot.summaries["60"][1], UNKNOWN)
        self.assertNotEqual(snapshot.summaries["60"][2], UNKNOWN)
        fetch.assert_called_once()

    def test_actual_magnitude_is_used_and_changed_mechanics_fail_closed(self):
        skill = self.saved["documents"]["skills"]["7"]
        skill["effects"][0]["effectValue"] = skill["effects"][0]["raw"]["_effectValue"] = 12300
        self.assertEqual(parse(self.saved).summaries["51"][1][0], "得分 +123→140%")
        skill["effects"][0]["triggerType"] = 999
        self.assertEqual(parse(self.saved).summaries["51"][1], UNKNOWN)

    def test_invalid_empty_nonfinite_wrong_server_and_duplicates(self):
        mutations = [lambda s: s["identity"].update(server="intl"),
                     lambda s: s["documents"].update(cards={}),
                     lambda s: s["documents"]["cards"]["51"].update(cardId=52),
                     lambda s: s["documents"]["skills"]["7"]["effects"][0].update(effectValue=float("inf")),
                     lambda s: s["documents"]["skills"]["7"]["effects"].append(s["documents"]["skills"]["7"]["effects"][0]),
                     lambda s: s["documents"]["skills"]["7"].update(effects=[])]
        for mutate in mutations:
            saved = copy.deepcopy(self.saved)
            mutate(saved)
            with self.assertRaises((ValueError, KeyError)):
                parse(saved)

    def test_identity_conflicts_cannot_attach_numbers_to_other_card(self):
        snapshot = parse(self.saved)
        row = snapshot.cards["51"]
        card = SimpleNamespace(id=51, asset_id=row["assetId"], rarity=row["rarity"], card_type=row["cardType"],
                               catalog={"character_ids": [row["characterId"]], "skill_evidence": [
                                   {"kind": "liveSkill", "id": row["liveSkillId"]},
                                   {"kind": "gekisouSkill", "id": row["gekisouSkillId"]}]})
        self.assertEqual(snapshot.for_card(card), snapshot.summaries["51"])
        card.asset_id += 1
        self.assertEqual(snapshot.for_card(card)[0][0], "映射未确认")

    def test_cache_refresh_concurrency_failure_and_no_cache(self):
        clock = Mock(return_value=1000)
        fetch = Mock(side_effect=self.fetch)
        repo = MemberRepository(self.path, fetch=fetch, clock=clock)
        with ThreadPoolExecutor(max_workers=4) as pool:
            snapshots = list(pool.map(lambda _: repo.get(), range(4)))
        self.assertEqual(fetch.call_count, 6)
        self.assertTrue(all(s.release == snapshots[0].release for s in snapshots))
        before = self.path.read_bytes()
        reopened = MemberRepository(self.path, fetch=Mock(side_effect=AssertionError("cache hit")), clock=clock)
        self.assertFalse(reopened.get().stale)
        clock.return_value += 86401
        fetch.side_effect = OSError("offline")
        self.assertTrue(repo.get().stale)
        self.assertEqual(self.path.read_bytes(), before)
        count = fetch.call_count
        repo.get()
        self.assertEqual(fetch.call_count, count)
        self.assertIsNone(MemberRepository(Path(self.temp.name)/"none.json", fetch=fetch, clock=clock).get())
        clock.return_value += 301
        fetch.side_effect = self.fetch
        self.saved["identity"]["releaseId"] = "r-new"
        self.assertEqual(repo.get().release, "r-new")
        self.assertFalse(repo.get().stale)

    def test_incomplete_refresh_preserves_previous_snapshot(self):
        clock = Mock(return_value=1000)
        repo = MemberRepository(self.path, fetch=self.fetch, clock=clock)
        repo.get()
        before = self.path.read_bytes()
        clock.return_value += 86401
        del self.saved["documents"]["cards"]["51"]
        self.assertTrue(repo.get().stale)
        self.assertIn("51", repo.get().cards)
        self.assertEqual(self.path.read_bytes(), before)

    def test_html_duplicate_keys_and_unexpected_resource_rejected(self):
        for content_type, raw in (("text/html", b"<html>error</html>"), ("application/json", b'{"a":1,"a":2}')):
            response = Mock(headers={"Content-Type": content_type})
            response.read.return_value = raw
            manager = Mock()
            manager.__enter__ = Mock(return_value=response)
            manager.__exit__ = Mock(return_value=False)
            with patch("ournotes_bot.sources.haneoka.haneoka_members.urlopen", return_value=manager):
                with self.assertRaises(ValueError):
                    fetch_json("cards?release=r-test")
        with self.assertRaises(ValueError):
            fetch_json("support-cards?release=r-test")


if __name__ == "__main__":
    unittest.main()
