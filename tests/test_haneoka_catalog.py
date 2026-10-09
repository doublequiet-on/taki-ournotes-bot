import copy
import io
import json
import os
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from ournotes_bot.config import Settings
from ournotes_bot.data import DataError, SongRepository
from ournotes_bot.query.card_catalog import query_cards, detail_text
from ournotes_bot.query.continuation import catalog_version
from ournotes_bot.sources.haneoka import catalog as source
from ournotes_bot.sources.haneoka.catalog_assets import asset_url, is_catalog_asset, download_asset
from ournotes_bot.sources.haneoka.catalog_records import skill_record
from ournotes_bot.sources.haneoka.catalog_text import description, references

FIXTURE = Path(__file__).parent / "fixtures/haneoka_catalog.json"


class CatalogTests(unittest.TestCase):
    def setUp(self):
        fixture = json.loads(FIXTURE.read_bytes())
        self.saved, self.expected = fixture["snapshot"], fixture["expected_yume"]
        self.saved["documents"]["jp"].setdefault("ui-marks", {})
        self.now, self.calls = 1100, []
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "haneoka-catalog-jp-v1.json"
        self.fetch = Mock(side_effect=self.respond)
        self.repo = source.CatalogRepository(self.path, fetch=self.fetch, clock=lambda: self.now)

    def respond(self, url, limit, timeout):
        self.calls.append(url)
        parts = urlsplit(url)
        self.assertEqual(parts.netloc, "haneoka.org")
        server, resource = parts.path.split("/")[-2:]
        self.assertLessEqual(timeout, 20)
        self.assertEqual(limit, 8_000_000)
        ident = self.saved["identities"][server]
        query = parse_qs(parts.query)
        if resource == "release":
            self.assertEqual(query, {"projection": ["identity"]})
            value = ident
        else:
            self.assertEqual(query["release"], [ident["releaseId"]])
            value = self.saved["documents"][server][resource]
            if "id" in query:
                self.assertLessEqual(len(query["id"]), 100)
                value = {"items": {k: value[k] for k in query["id"]}, "missing": []}
        return source.encoded(value), {"x-haneoka-release-id": ident["releaseId"], "x-haneoka-source-id": ident["sourceId"]}

    def records(self):
        return source.validate(self.saved, self.now)[0]

    def main_repo(self):
        repo = SongRepository("", self.path.with_name("old.json"), data_source="haneoka")
        repo._catalog_repository = self.repo
        return repo

    def test_independent_reference_text_values_units_and_floor(self):
        songs, cards, supports = self.records()
        self.assertEqual(len(songs), 2)
        self.assertEqual(next(s for s in songs if s.id == 100091).band, "CRYCHIC")
        self.assertTrue(all(s.traits.color for s in songs))
        for name, rows in (("member", cards), ("support", supports)):
            for card in rows:
                expected = self.expected[name][str(card.id)]
                self.assertEqual([card.performance, card.technic, card.visual], expected["stats"])
                self.assertEqual(card.catalog["stats_level1"], expected["level1"])
                self.assertTrue(card.catalog["detail_loaded"])
                self.assertEqual(len(card.skills), len(expected["skills"]))
                for skill, old in zip(card.skills, expected["skills"]):
                    self.assertEqual(skill.kind, old["kind"])
                    for locale in ("ja", "zh"):
                        self.assertEqual(skill.localized["description"][locale], old[locale])
        self.assertEqual(cards[0].catalog["member_summary"][1], ("得分 +70%", "5秒 · 无额外条件"))
        snap = next(c for c in supports if c.id == 61)
        self.assertEqual(snap.catalog["skill_slots"]["gekisouSupportSkill"], [0, 0])
        self.assertEqual(snap.catalog["categories"]["gekisou"], ["not_applicable"])
        self.assertEqual(len(snap.characters), 5)

    def test_fresh_stale_and_unsaved_cache_never_overwrite_old_source(self):
        old = self.path.with_name("old.json")
        old.write_bytes(b"old source cache")
        self.repo.load()
        raw = self.path.read_bytes()
        self.assertEqual(self.repo.state, "fresh")
        self.fetch.side_effect = OSError("offline")
        self.repo.load()
        self.assertEqual(self.repo.state, "cached")
        self.now += 21601
        records, metadata = self.repo.load()
        self.assertEqual(self.repo.state, "stale")
        self.assertTrue(records[0][0].traits.stale)
        self.assertTrue(records[1][0].catalog["detail_stale"])
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(old.read_bytes(), b"old source cache")
        self.fetch.side_effect = self.respond
        self.saved["identities"]["jp"]["releaseId"] = "r-" + "a" * 20
        with patch.object(source.os, "replace", side_effect=PermissionError("read only")):
            records, metadata = self.repo.load(True)
        self.assertEqual(self.repo.state, "unsaved")
        self.assertEqual(metadata["data_version"], "r-" + "a" * 20)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])
        self.repo.load()
        self.assertEqual(self.repo.state, "unsaved")

    def test_failed_new_release_retains_whole_previous_snapshot(self):
        before, meta = self.repo.load()
        self.saved["identities"]["jp"]["releaseId"] = "r-" + "b" * 20
        self.saved["documents"]["intl"]["catalog"]["sourceId"] = "wrong"
        after, later = self.repo.load(True)
        self.assertEqual(self.repo.state, "stale")
        self.assertEqual(meta, later)
        self.assertEqual(before[1][0].title, after[1][0].title)
        self.assertEqual(after[1][0].catalog["release"], meta["data_version"])

    def test_restart_validates_cache_without_network(self):
        expected, meta = self.repo.load()
        fetch = Mock(side_effect=AssertionError("fresh disk cache must be offline"))
        repo = source.CatalogRepository(self.path, fetch=fetch, clock=lambda: self.now + 1)
        records, metadata = repo.load()
        self.assertEqual(records, expected)
        self.assertEqual(metadata, meta)
        self.assertEqual(repo.state, "cached")
        fetch.assert_not_called()

    def test_more_than_one_batch_is_complete_and_bounded(self):
        for server in ("jp", "intl"):
            docs = self.saved["documents"][server]
            rows = docs["songs"]
            for key in range(200000, 200101):
                rows[str(key)] = {**rows["100014"], "musicId": key}
            docs["catalog"]["resources"]["songs"]["count"] = len(rows)
        records, _ = self.repo.load()
        self.assertEqual(len(records[0]), 103)
        batches = [url for url in self.calls if "/songs?" in url and "&id=" in url]
        self.assertEqual(sorted(len(parse_qs(urlsplit(url).query)["id"]) for url in batches), [3,3,100,100])

    def test_missing_lv5_growth_and_character_links_remain_unknown(self):
        jp = self.saved["documents"]["jp"]
        del jp["characters"]["1"]
        jp["catalog"]["resources"]["characters"]["count"] -= 1
        jp["progression"]["memberCardLevels"] = []
        jp["cards"]["1"]["resolvedSkills"]["live"]["effects"] = []
        card = self.records()[1][0]
        self.assertFalse(card.catalog["character_links_complete"])
        self.assertFalse(card.catalog["skill_index_complete"])
        self.assertIsNone(card.catalog["stats_level1"])
        self.assertIn("参数未确认", card.skills[1].description)

    def test_bad_headers_counts_batches_and_json_fail_closed(self):
        original = self.respond
        for kind in ("headers", "count", "batch", "duplicate", "nan", "oversized"):
            def bad(url, limit, timeout):
                raw, headers = original(url, limit, timeout)
                if kind == "headers": headers["x-haneoka-release-id"] = "wrong"
                if kind == "count" and "/catalog?" in url:
                    value = json.loads(raw); value["resources"]["songs"]["count"] += 1; raw = source.encoded(value)
                if kind == "batch" and "&id=" in url: raw = b'{"items":{},"missing":[]}'
                if kind == "duplicate": raw = b'{"a":1,"a":2}'
                if kind == "nan": raw = b'{"a":NaN}'
                if kind == "oversized": raw = b" " * 8_000_001
                return raw, headers
            with self.subTest(kind=kind):
                self.fetch.side_effect = bad
                with self.assertRaises(DataError): self.repo.load(True)
                self.assertFalse(self.path.exists())

    def test_corrupt_cache_is_not_accepted_offline(self):
        self.repo.load()
        valid = json.loads(self.path.read_bytes())
        variants = [{**valid, "sha256": "0" * 64}, {"snapshot": None, "sha256": "bad"}]
        for changes in ({"source": "yume"}, {"schema": 4}, {"fetched_at": self.now + 1000}, {"fetched_at": True}):
            snapshot = {**valid["snapshot"], **changes}
            variants.append({"snapshot": snapshot, "sha256": source.hashlib.sha256(source.encoded(snapshot)).hexdigest()})
        for value in variants:
            self.path.write_bytes(source.encoded(value))
            repo = source.CatalogRepository(self.path, fetch=Mock(side_effect=OSError()), clock=lambda: self.now)
            with self.assertRaises(DataError): repo.load()

    def test_entity_and_image_identity_and_duplicate_effect_rejected(self):
        original = copy.deepcopy(self.saved)
        for field, value in (("cardId", 2), ("assetId", 2), ("stat", {"performance": 1,"technique": 1,"visual": 1})):
            self.saved = copy.deepcopy(original)
            self.saved["documents"]["jp"]["cards"]["1"][field] = value
            with self.assertRaises(ValueError): self.records()
        self.saved = original
        row = self.saved["documents"]["jp"]["cards"]["1"]
        row["resolvedSkills"]["live"]["effects"].append(copy.deepcopy(row["resolvedSkills"]["live"]["effects"][-1]))
        self.assertFalse(self.records()[1][0].catalog["skill_index_complete"])

    def test_chinese_requires_same_japanese_entity_and_referenced_mechanics(self):
        jp = self.saved["documents"]["jp"]
        intl = self.saved["documents"]["intl"]
        intl["cards"]["1"]["prefix"][0] = "different Japanese title"
        self.assertEqual(self.records()[1][0].title, jp["cards"]["1"]["prefix"][0])
        live = intl["cards"]["1"]["resolvedSkills"]["live"]
        live["effects"][-1]["effectValue"] += 1
        skills = self.records()[1][0].skills
        self.assertEqual(skills[1].description, skills[1].localized["description"]["ja"])
        leader = jp["cards"]["1"]["resolvedSkills"]["leader"]
        peer = intl["cards"]["1"]["resolvedSkills"]["leader"]
        target = leader["effects"][-1]["targetIds"][0]
        next(t for t in intl["skill-reference"]["targets"] if t["raw"]["_id"] == target)["raw"]["_bandID"] = 999
        result, _, _ = skill_record("leaderSkill", leader, peer, references(jp["skill-reference"]), references(intl["skill-reference"]))
        self.assertEqual(result.description, result.localized["description"]["ja"])

    def test_unknown_templates_are_explicit_and_never_executed(self):
        for template in ('{__import__("os").system("whoami")}', '{effects[0].__class__}', '{effects[0].value/0}', '{broken}', '{unclosed'):
            text, complete = description(template, {"effects": [{"value": 123}]})
            self.assertFalse(complete)
            self.assertIn("参数未确认", text)
        self.assertEqual(description('{effects[0].value/100:F1}', {"effects": [{"value": 123}]}), ("1.2", True))

    def test_unknown_slots_tags_and_categories_do_not_become_empty_skills(self):
        row = self.saved["documents"]["jp"]["support-cards"]["61"]
        del row["raw"]["_gekisouSupportSkillId01"]
        repo = self.main_repo(); repo.load()
        card = next(c for c in repo.support_cards if c.id == 61)
        self.assertFalse(card.catalog["detail_loaded"])
        self.assertEqual(card.catalog["categories"]["gekisou"], [None])
        self.assertIn("映射不完整", query_cards("得意=MyGO!!!!!", repo).error)

    def test_main_wiring_details_and_traits_never_call_legacy_network(self):
        repo = self.main_repo()
        with patch("ournotes_bot.sources.yatta.fetch_json", side_effect=AssertionError("old network")), patch.object(repo.song_traits,"refresh",side_effect=AssertionError("mixed release")):
            repo.load()
            repo.card_with_detail(replace(repo.cards[0], skills=()))
            repo.support_card_with_detail(replace(repo.support_cards[0], skills=(),catalog={}))
            repo.refresh_song_traits()
        self.assertTrue(repo.member_skill_index_ready())
        text = detail_text(repo.cards[0])
        self.assertIn("Haneoka JP", text)
        self.assertIn("得意乐曲：映射未确认", text)
        self.assertNotIn("Project Yume", text)
        from ournotes_bot.commands import handle_command
        status = handle_command("/数据状态", repo)
        self.assertIn("Haneoka JP", str(status))
        self.assertNotIn("Project Yume", str(status))
        self.assertEqual(repo.cache_state, "fresh")
        self.assertFalse(repo.cache_file.exists())
        before = catalog_version(repo)
        repo.data_source = "yume"
        self.assertNotEqual(before, catalog_version(repo))

    def test_missing_second_snap_skill_keeps_category_filter_incomplete(self):
        row = self.saved["documents"]["jp"]["support-cards"]["1"]
        for missing in (999999, None):
            with self.subTest(slot=missing):
                row["raw"]["_supportSkillId02"] = missing
                repo = self.main_repo()
                repo.support_cards = [c for c in self.records()[2] if c.id == 1]
                self.assertFalse(repo.support_cards[0].catalog["detail_loaded"])
                answer = query_cards("LIVE=分数提升", repo, support=True)
                self.assertEqual(answer.status, "data_unavailable")
                self.assertIn("索引不完整", answer.error)
                # The separate, fully resolved gekisou slot remains usable.
                self.assertEqual(len(query_cards("击奏=LUCK", repo, support=True).cards), 1)

    def test_config_defaults_and_switches_are_independent(self):
        with patch("ournotes_bot.config.load_dotenv"), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Settings.from_env().data_source, "haneoka")
            os.environ["OURNOTES_DATA_SOURCE"] = "haneoka"
            settings = Settings.from_env()
            self.assertEqual((settings.data_source,settings.chart_source,settings.meta_source,settings.cutoff_source), ("haneoka","haneoka","haneoka-site","haneoka"))
            os.environ["OURNOTES_DATA_SOURCE"] = "unknown"
            with self.assertRaises(ValueError): Settings.from_env()

    def test_member_render_uses_captured_summary_and_preserves_bytes(self):
        from ournotes_bot.rendering.member_list_visuals import render
        cards = self.records()[1][:1]
        captured = cards[0].catalog["member_summary"]
        old_card = replace(cards[0],catalog={**cards[0].catalog,"source":"yume"})
        snapshot = Mock(stale=False); snapshot.for_card.return_value = captured
        with patch("ournotes_bot.visuals._asset",return_value=None), patch("ournotes_bot.rendering.member_list_visuals.get_snapshot",return_value=snapshot) as get:
            before = render([old_card], "")
            get.reset_mock()
            after = render(cards, "")
            get.assert_not_called()
        self.assertEqual(before, after)
        self.assertLessEqual(len(after), 1_500_000)

    def test_images_are_pinned_bounded_isolated_and_do_not_fall_back(self):
        from ournotes_bot.visuals import _asset
        url = self.records()[1][0].full_url
        self.assertTrue(is_catalog_asset(url))
        for bad in (url.replace("haneoka.org", "evil.invalid"), url+"&release=bad", url+"#x", url.replace("/MemberCard/1/", "/MemberCard/../")):
            self.assertFalse(is_catalog_asset(bad))
        buf = io.BytesIO(); Image.new("RGB", (20,20),"red").save(buf,format="PNG")
        raw = buf.getvalue()
        with ExitStack() as stack:
            stack.enter_context(patch("ournotes_bot.visuals.runtime_data_dir", return_value=self.path.parent))
            old = stack.enter_context(patch("ournotes_bot.visuals.urlopen", side_effect=AssertionError("legacy downloader")))
            download = stack.enter_context(patch("ournotes_bot.sources.haneoka.catalog_assets.download_asset",return_value=raw))
            image = _asset(url,(10,10)); self.assertEqual(image.size,(10,10)); image.close()
            download.side_effect = OSError("offline")
            image = _asset(url,(10,10)); self.assertIsNotNone(image); image.close()
            self.assertEqual(download.call_count,1)
            cached = next((self.path.parent/"haneoka-asset-cache").glob("*.png")); cached.write_bytes(b"bad")
            self.assertIsNone(_asset(url,(10,10)))
            old.assert_not_called()
        with patch("ournotes_bot.sources.haneoka.catalog_assets.public_get",return_value=(raw,{"x-haneoka-release-id":"wrong","x-haneoka-source-id":"x"})):
            with self.assertRaises(ValueError): download_asset(url)


if __name__ == "__main__":
    unittest.main()
