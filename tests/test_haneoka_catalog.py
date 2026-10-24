import copy
import io
import json
import os
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from ournotes_bot.config import Settings
from ournotes_bot.commands import _catalog_unknowns, handle_command
from ournotes_bot.data import DataError, SongRepository
from ournotes_bot.query.card_catalog import query_cards, detail_text
from ournotes_bot.query.continuation import catalog_version
from ournotes_bot.sources.haneoka import catalog as source
from ournotes_bot.sources.haneoka.catalog_assets import asset_url, is_catalog_asset, download_asset
from ournotes_bot.sources.haneoka.catalog_records import skill_record
from ournotes_bot.sources.haneoka.catalog_text import description, references

FIXTURE = Path(__file__).parent / "fixtures/haneoka_catalog.json"


class CatalogTests(unittest.TestCase):
    def test_natural_current_view_checks_release_and_never_falls_back(self):
        self.repo.load(refresh=True)
        before=copy.deepcopy(self.repo.saved)
        self.repo.fetch=Mock(side_effect=self.probe_responses())
        with patch.object(self.repo,'_collect',side_effect=AssertionError('unchanged release redownload')):
            records,metadata,info=self.repo.current_with_info()
        self.assertTrue(records[0]);self.assertEqual(self.repo.fetch.call_count,2)
        self.assertEqual(self.repo.saved,before)
        self.repo.fetch=Mock(side_effect=OSError('fixture network failure'))
        with self.assertRaises(OSError): self.repo.current_with_info()
        self.assertEqual(self.repo.saved,before)

    def probe_responses(self, versions=None):
        identities = versions or self.saved['identities']
        def respond(url,limit,timeout):
            self.assertEqual(limit,512000)
            server=urlsplit(url).path.split('/')[-2]
            ident=identities[server]
            return source.encoded(ident),{'x-haneoka-release-id':ident['releaseId'],'x-haneoka-source-id':ident['sourceId']}
        return respond

    def test_current_new_release_does_not_consume_legacy_refresh_or_persistence(self):
        self.repo.load(refresh=True)
        before=copy.deepcopy(self.repo.saved);disk=self.path.read_bytes();state=self.repo.state
        candidate=copy.deepcopy(before);candidate['identities']['jp']['releaseId']='r-'+'b'*20
        candidate['identities']['jp']['sourceId']='new-query-source'
        candidate['documents']['jp']['catalog']['sourceId']='new-query-source'
        self.repo.fetch=Mock(side_effect=self.probe_responses(candidate['identities']))
        with patch.object(self.repo,'_collect',return_value=candidate) as collect:
            self.repo.current_with_info();self.repo.current_with_info()
        collect.assert_called_once()
        self.assertEqual(self.repo.saved,before)
        self.assertEqual(self.path.read_bytes(),disk)
        self.assertEqual(self.repo.state,state)

    def test_admin_sync_unchanged_checks_both_versions_without_full_download(self):
        self.repo.load(refresh=True)
        self.repo.fetch=Mock(side_effect=self.probe_responses())
        with patch.object(self.repo,'_collect',side_effect=AssertionError('full refresh')):
            *_,report=self.repo.synchronize_with_info()
        self.assertEqual(report['code'],'upstream_unchanged')
        self.assertEqual(self.repo.fetch.call_count,2)
        self.assertTrue(report['persisted'])
        self.assertEqual(set(report['added_counts'].values()),{0})

    def test_admin_sync_text_version_and_unsaved_result(self):
        self.repo.load(refresh=True)
        candidate=copy.deepcopy(self.repo.saved)
        candidate['identities']['intl']['releaseId']='r-'+'b'*20
        candidate['fetched_at']=self.now+1
        self.now+=2
        self.repo.fetch=Mock(side_effect=self.probe_responses(candidate['identities']))
        with patch.object(self.repo,'_collect',return_value=candidate),patch.object(self.repo,'_write',side_effect=OSError('disk full')):
            *_,report=self.repo.synchronize_with_info()
        self.assertEqual(report['code'],'updated_unsaved')
        self.assertTrue(report['published']);self.assertFalse(report['persisted'])
        self.assertNotEqual(report['before_version']['intl'],report['after_version']['intl'])

    def test_admin_sync_failure_retains_cache_and_does_not_claim_publication(self):
        self.repo.load(refresh=True)
        with patch.object(self.repo,'_collect',side_effect=OSError('offline')):
            *_,report=self.repo.synchronize_with_info(True)
        self.assertEqual(report['code'],'refresh_failed_cached')
        self.assertFalse(report['published']);self.assertTrue(report['data_available'])

    def test_admin_sync_without_valid_cache_reports_unavailable(self):
        with patch.object(self.repo,'_collect',side_effect=OSError('offline')):
            records,_,_,report=self.repo.synchronize_with_info(True)
        self.assertIsNone(records)
        self.assertEqual(report['code'],'no_available_data')

    def test_admin_sync_retries_unsaved_snapshot_without_fetching_catalog(self):
        self.repo.load(refresh=True)
        self.repo._durable_fetched_at=None
        self.repo.state='unsaved'
        self.repo.fetch=Mock(side_effect=self.probe_responses())
        with patch.object(self.repo,'_collect',side_effect=AssertionError('catalog refetch')):
            *_,report=self.repo.synchronize_with_info()
        self.assertTrue(report['persisted']);self.assertEqual(report['code'],'upstream_unchanged')

    def test_admin_sync_repairs_corrupt_disk_from_valid_same_version_memory(self):
        self.repo.load(refresh=True)
        self.path.write_text('broken',encoding='utf-8')
        self.repo.fetch=Mock(side_effect=self.probe_responses())
        with patch.object(self.repo,'_collect',side_effect=AssertionError('catalog refetch')):
            *_,report=self.repo.synchronize_with_info()
        self.assertTrue(report['persisted']);self.assertEqual(self.repo._read(),self.repo.saved)

    def setUp(self):
        fixture = json.loads(FIXTURE.read_bytes())
        self.saved, self.expected = fixture["snapshot"], fixture["expected_yume"]
        self.saved["documents"]["jp"].setdefault("ui-marks", {})
        self.saved["documents"]["intl"].setdefault("ui-marks", {})
        self.saved["documents"]["intl"].setdefault("progression", {k:[] for k in ("memberCardLevels","memberCardRanks","supportCardLevels","supportCardRanks")})
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
            value = ({"entries": {}} if resource in {"events","gacha"} else self.saved["documents"][server][resource])
            if "id" in query:
                self.assertLessEqual(len(query["id"]), 100)
                value = {"items": {k: value[k] for k in query["id"]}, "missing": []}
        return source.encoded(value), {"x-haneoka-release-id": ident["releaseId"], "x-haneoka-source-id": ident["sourceId"]}

    def records(self):
        return source.validate(self.saved, self.now)[0]

    def test_download_budget_counts_all_decoded_responses_at_exact_boundary(self):
        sizes = []

        def measured(url, limit, timeout):
            raw, headers = self.respond(url, limit, timeout)
            sizes.append(len(raw))
            return raw, headers

        self.repo.fetch = measured
        candidate = self.repo._collect()
        total = sum(sizes)
        with patch.object(source, "MAX_DOWNLOAD_BYTES", total):
            self.assertEqual(self.repo._collect(), candidate)
        with patch.object(source, "MAX_DOWNLOAD_BYTES", total - 1):
            with self.assertRaisesRegex(ValueError, "Catalog download budget"):
                self.repo._collect()

    def test_download_budget_does_not_expand_persisted_cache_limit(self):
        candidate = self.repo._collect()
        self.repo._write(candidate)
        original = self.path.read_bytes()
        with patch.object(source, "MAX_BYTES", len(original) - 1):
            with self.assertRaisesRegex(ValueError, "Catalog cache size limit"):
                self.repo._write(candidate)
            with self.assertRaisesRegex(ValueError, "Catalog cache size limit"):
                self.repo._read()
        self.assertEqual(self.path.read_bytes(), original)

    def test_automatic_summary_refreshes_same_id_and_survives_restart(self):
        from ournotes_bot.rendering.catalog_layout import summaries
        bot, candidate = self.status_sequence()
        for version, value in enumerate((12300, 17600), 1):
            for server in ('jp', 'intl'):
                skill = candidate['documents'][server]['cards']['51']['resolvedSkills']['live']
                skill['description'] = [f'新版机制{version}：{{effects[0].value/100}}%', '', '',
                    f'新机制{version}：得分提升{{effects[0].value/100}}%，仅LIFE≥700时，最多2次']
                for effect in skill['effects']:
                    effect['effectValue'] = value
                    effect['raw']['_effectValue'] = value
            with patch.object(self.repo, '_collect', return_value=copy.deepcopy(candidate)):
                bot.refresh()
            card = next(c for c in bot.cards if c.id == 51)
            i = next(i for i,s in enumerate(card.skills) if s.kind == 'liveSkill')
            brief = card.catalog['compact_skills'][i]
            self.assertTrue(brief['automatic'])
            self.assertEqual(brief['levels']['5'], f'新机制{version}：得分+{value/100:g}%，仅LIFE≥700时，最多2次')
            self.assertIn(brief['levels']['5'], [s[2] for s in summaries(card)])
        with patch.object(self.repo, '_collect', side_effect=OSError('offline')):
            bot.refresh()
        self.assertEqual(bot.cache_state, 'stale')
        restarted = source.CatalogRepository(self.path, clock=lambda: self.now)
        records, _ = restarted.load()
        card = next(c for c in records[1] if c.id == 51)
        self.assertEqual(card.catalog['compact_skills'][i], brief)

    def test_automatic_summary_does_not_hide_unknown_parameters(self):
        for server in ('jp', 'intl'):
            skill = self.saved['documents'][server]['cards']['51']['resolvedSkills']['live']
            skill['description'] = ['新机制{unknown.parameter}']
        card = next(c for c in self.records()[1] if c.id == 51)
        i = next(i for i,s in enumerate(card.skills) if s.kind == 'liveSkill')
        self.assertIsNone(card.catalog['compact_skills'][i])
        self.assertFalse(card.catalog['compact_summary_complete'])
        self.assertIn('参数未确认', card.skills[i].description)

    def test_new_card_and_skill_ids_generate_without_registered_fingerprint(self):
        for server in ('jp', 'intl'):
            docs = self.saved['documents'][server]
            card = copy.deepcopy(docs['cards']['51'])
            card['cardId'] = card['raw']['_id'] = 999
            card['raw']['_liveSkillID'] = 999
            skill = card['resolvedSkills']['live']
            skill['id'] = 999
            skill['description'] = ['新机制：{effects[0].value/100}%']
            for effect in skill['effects']:
                effect['raw']['_liveSkillID'] = 999
            docs['cards']['999'] = card
            docs['catalog']['resources']['cards']['count'] += 1
        card = next(c for c in self.records()[1] if c.id == 999)
        i = next(i for i,s in enumerate(card.skills) if s.kind == 'liveSkill')
        self.assertTrue(card.catalog['compact_skills'][i]['automatic'])
        self.assertEqual(card.catalog['compact_skills'][i]['levels']['5'], card.skills[i].description)
        self.assertTrue(card.catalog['compact_summary_complete'])

    def main_repo(self):
        repo = SongRepository("", self.path.with_name("old.json"), data_source="haneoka")
        repo._catalog_repository = self.repo
        return repo

    def test_status_counts_explicit_unknowns_and_reports_latest_additions(self):
        bot = self.main_repo()
        self.repo.saved = copy.deepcopy(self.saved)
        bot.load()
        initial = handle_command('/数据状态', bot)
        self.assertIn('最近新增：暂无可比的上次主资料快照', initial)
        self.assertIn('可统计的未确认项：', initial)
        previous = copy.deepcopy(self.saved)
        candidate = copy.deepcopy(self.saved)
        candidate['fetched_at'] = 1100
        candidate['documents']['jp']['characters']['999'] = {'characterId': 999}
        summary = source.added_since(previous, candidate)
        self.assertEqual(summary['ids']['characters'], [999])
        candidate['change_summary'] = summary
        self.assertEqual(source.added_since(candidate, candidate), summary)
        self.repo.saved['change_summary'] = summary
        # Model a cache reload, rather than mutating an already published snapshot.
        reloaded_source = source.CatalogRepository(self.path, clock=lambda: self.now)
        reloaded_source.saved = copy.deepcopy(self.repo.saved)
        bot._catalog_repository = reloaded_source
        bot.load()
        reply = handle_command('/数据状态', bot)
        self.assertIn('最近记录新增（1970-01-01 08:18 UTC+08:00）', reply)
        self.assertIn('人物 1：999', reply)

    def test_first_comparison_without_new_ids_is_not_claimed_as_first_load(self):
        previous = copy.deepcopy(self.saved)
        current = copy.deepcopy(self.saved)
        self.assertIsNone(source.added_since(None, current))
        summary = source.added_since(previous, current)
        self.assertTrue(all(not ids for ids in summary['ids'].values()))
        self.assertEqual(source.added_since({**previous, 'change_summary': summary}, current), summary)

    def test_status_unknown_counts_follow_explicit_fields(self):
        bot = self.main_repo()
        self.repo.saved = copy.deepcopy(self.saved)
        bot.load()
        view = bot.capture_catalog()
        song = view.songs[0]
        card = view.cards[0]
        support = view.support_cards[0]
        known = replace(view,
                        songs=[replace(song, charts=(replace(song.charts[0], notes=0), *song.charts[1:])),
                               *view.songs[1:]],
                        cards=[replace(card, catalog={**card.catalog, 'skill_index_complete': True,
                                                      'compact_summary_complete': True,
                                                      'member_summary_complete': True, 'tags_complete': True}),
                               *view.cards[1:]],
                        support_cards=[replace(support, catalog={**support.catalog, 'skill_index_complete': True}),
                                       *view.support_cards[1:]])
        before = _catalog_unknowns(known)
        changed = replace(known,
                          songs=[replace(known.songs[0], charts=(replace(known.songs[0].charts[0], notes=None),
                                                                *known.songs[0].charts[1:])), *known.songs[1:]],
                          cards=[replace(known.cards[0], catalog={**known.cards[0].catalog,
                                                                  'skill_index_complete': False,
                                                                  'member_summary_complete': False,
                                                                  'compact_summary_complete': False,
                                                                  'tags_complete': False}), *known.cards[1:]],
                          support_cards=[replace(known.support_cards[0], catalog={**known.support_cards[0].catalog,
                                                                                   'skill_index_complete': False}),
                                         *known.support_cards[1:]])
        after = _catalog_unknowns(changed)
        for key in ('notes', 'member_skills', 'member_summaries', 'member_tags', 'support_skills'):
            self.assertEqual(after[key], before[key] + 1, key)
        legacy = {k: v for k, v in changed.cards[0].catalog.items() if k != 'compact_summary_complete'}
        self.assertEqual(_catalog_unknowns(replace(changed, cards=[replace(changed.cards[0], catalog=legacy),
                                                                 *changed.cards[1:]])), after)

    def test_addition_survives_cache_reload_and_failed_refresh(self):
        # Windows sandbox Temp can deny os.replace; use the writable checkout.
        self.path = Path.cwd() / 'runtime' / f'status-{uuid.uuid4().hex}.json'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: self.path.unlink(missing_ok=True))
        self.repo = source.CatalogRepository(self.path, clock=lambda: self.now)
        bot = self.main_repo()
        self.repo.saved = copy.deepcopy(self.saved)
        bot.load()
        candidate = copy.deepcopy(self.saved)
        self.now = candidate['fetched_at'] + 100
        candidate['fetched_at'] = self.now
        people = candidate['documents']['jp']['characters']
        added = copy.deepcopy(next(iter(people.values())))
        added['characterId'] = 999
        added['characterName'] = ['追加人物']
        people['999'] = added
        candidate['documents']['jp']['catalog']['resources']['characters']['count'] = len(people)
        with patch.object(self.repo, '_collect', return_value=candidate):
            bot.refresh()
        self.assertEqual(bot.cache_state, 'fresh')
        self.assertIn('人物 1：999 追加人物', handle_command('/数据状态', bot))
        with patch.object(self.repo, '_collect', side_effect=OSError('offline')):
            bot.refresh()
        self.assertEqual(bot.cache_state, 'stale')
        self.assertIn('人物 1：999 追加人物', handle_command('/数据状态', bot))
        unsaved = copy.deepcopy(candidate)
        self.now += 100
        unsaved['fetched_at'] = self.now
        another = copy.deepcopy(added)
        another['characterId'] = 1000
        another['characterName'] = ['未保存人物']
        unsaved['documents']['jp']['characters']['1000'] = another
        unsaved['documents']['jp']['catalog']['resources']['characters']['count'] += 1
        with patch.object(self.repo, '_collect', return_value=unsaved), \
             patch.object(self.repo, '_write', side_effect=OSError('disk full')):
            bot.refresh()
        self.assertEqual(bot.cache_state, 'unsaved')
        self.assertIn('已取得但尚未保存的新增', handle_command('/数据状态', bot))
        reloaded = self.main_repo()
        reloaded._catalog_repository = source.CatalogRepository(self.path, clock=lambda: self.now)
        reloaded.load()
        self.assertIn('人物 1：999 追加人物', handle_command('/数据状态', reloaded))
        self.assertNotIn('1000 未保存人物', handle_command('/数据状态', reloaded))

    def status_sequence(self):
        self.path = Path.cwd() / 'runtime' / f'status-{uuid.uuid4().hex}.json'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: self.path.unlink(missing_ok=True))
        self.repo = source.CatalogRepository(self.path, clock=lambda: self.now)
        self.repo._write(self.saved)
        bot = self.main_repo()
        bot.load()
        candidate = copy.deepcopy(self.saved)
        self.now += 100
        candidate['fetched_at'] = self.now
        people = candidate['documents']['jp']['characters']
        person = copy.deepcopy(next(iter(people.values())))
        person.update(characterId=999, characterName=['连续刷新测试人物'])
        people['999'] = person
        candidate['documents']['jp']['catalog']['resources']['characters']['count'] = len(people)
        return bot, candidate

    def test_saved_additions_stay_saved_after_identical_refresh_save_failure(self):
        bot, candidate = self.status_sequence()
        with patch.object(self.repo, '_collect', return_value=candidate):
            bot.refresh()
        successful = bot.last_successful_sync_at
        summary = bot.metadata['change_summary']
        repeated = copy.deepcopy(candidate)
        self.now += 100
        repeated['fetched_at'] = self.now
        with patch.object(self.repo, '_collect', return_value=repeated), \
             patch.object(self.repo, '_write', side_effect=OSError('disk full')):
            bot.refresh()
        self.assertEqual(bot.cache_state, 'unsaved')
        self.assertEqual(bot.metadata['change_summary'], summary)
        self.assertTrue(bot.metadata['change_summary_saved'])
        self.assertNotIn('尚未保存的新增', handle_command('/数据状态', bot))
        self.assertEqual(bot.last_successful_sync_at, successful)
        with patch.object(self.repo, '_collect', side_effect=OSError('offline')):
            bot.refresh()
        self.assertTrue(bot.metadata['change_summary_saved'])
        self.assertEqual(bot.last_successful_sync_at, successful)

    def test_unsaved_additions_remain_pending_after_network_failure_and_recover(self):
        bot, candidate = self.status_sequence()
        successful = bot.last_successful_sync_at
        with patch.object(self.repo, '_collect', return_value=candidate), \
             patch.object(self.repo, '_write', side_effect=OSError('disk full')):
            bot.refresh()
        for operation in ('refresh', 'load'):
            with patch.object(self.repo, '_collect', side_effect=OSError('offline')):
                getattr(bot, operation)()
            self.assertFalse(bot.metadata['change_summary_saved'])
            self.assertIn('已取得但尚未保存的新增', handle_command('/数据状态', bot))
            self.assertEqual(bot.last_successful_sync_at, successful)
        restarted = self.main_repo()
        restarted._catalog_repository = source.CatalogRepository(self.path, clock=lambda: self.now)
        restarted.load()
        self.assertIsNone(restarted.metadata['change_summary'])
        self.assertEqual(restarted.last_successful_sync_at, successful)
        with patch.object(self.repo, '_collect', return_value=candidate):
            bot.refresh()
        self.assertTrue(bot.metadata['change_summary_saved'])
        self.assertNotIn('尚未保存的新增', handle_command('/数据状态', bot))
        self.assertNotEqual(bot.last_successful_sync_at, successful)

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

    def install_parameterized_leader(self, card_key, sample_key):
        sample = json.loads((FIXTURE.parent / "haneoka_leader_parameters.json").read_text(encoding="utf8"))
        jp = self.saved["documents"]["jp"]
        skill = sample["leaders"][sample_key]
        skill["skillName"] = ["合成测试队长技能"]
        card = jp["cards"][card_key]
        card["resolvedSkills"]["leader"] = skill
        card["raw"]["_leaderSkillID"] = card["leaderSkillId"] = skill["id"]
        targets = {r["raw"]["_id"]: r for r in jp["skill-reference"]["targets"]}
        targets.update({r["raw"]["_id"]: r for r in sample["targets"]})
        jp["skill-reference"]["targets"] = list(targets.values())
        jp["bands"].update(sample["bands"])
        jp["catalog"]["resources"]["bands"]["count"] = len(jp["bands"])
        return skill

    def test_data_refresh_automatically_rebuilds_summaries_in_the_same_snapshot(self):
        bot = self.main_repo()
        bot.load(refresh=True)
        previous = bot.cards[0]
        skill = self.install_parameterized_leader(str(previous.id), "65")
        self.now += 1800
        bot.refresh()
        current = next(c for c in bot.cards if c.id == previous.id)
        self.assertEqual(current.catalog["member_summary"][0],
                         ("表现值提升", "红色成员 +102%\nmillsage成员 +48%"))
        self.assertNotEqual(previous.catalog["member_summary"][0], current.catalog["member_summary"][0])
        skill["effects"][0]["effectValue"] = skill["effects"][0]["raw"]["_effectValue"] = 11000
        self.now += 1800
        bot.refresh()
        current = next(c for c in bot.cards if c.id == previous.id)
        self.assertIn("红色成员 +110%", current.catalog["member_summary"][0][1])
        self.assertEqual(current.catalog["release"], bot.metadata["data_version"])

    def test_startup_rebuilds_new_summary_from_raw_cache_without_network(self):
        key = next(iter(self.saved["documents"]["jp"]["cards"]))
        self.install_parameterized_leader(key, "66")
        self.repo.load(refresh=True)
        fresh = source.CatalogRepository(self.path, fetch=Mock(side_effect=AssertionError("no download")), clock=lambda: self.now)
        bot = self.main_repo()
        bot._catalog_repository = fresh
        bot.load()
        current = next(c for c in bot.cards if c.id == int(key))
        self.assertEqual(current.catalog["member_summary"][0],
                         ("技巧值提升", "millsage成员 +132%\nCOMBO激奏成员 +18%"))

    def test_failed_refresh_keeps_last_verified_summary_and_marks_stale(self):
        key = next(iter(self.saved["documents"]["jp"]["cards"]))
        self.install_parameterized_leader(key, "65")
        bot = self.main_repo()
        bot.load(refresh=True)
        before = next(c for c in bot.cards if c.id == int(key)).catalog["member_summary"]
        old_cache = self.path.read_bytes()
        self.now += 1800
        self.fetch.side_effect = OSError("offline")
        bot.refresh()
        current = next(c for c in bot.cards if c.id == int(key))
        self.assertEqual(current.catalog["member_summary"], before)
        self.assertTrue(current.catalog["detail_stale"])
        self.assertEqual(self.path.read_bytes(), old_cache)

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
            self.assertEqual((settings.data_source,settings.chart_source,settings.cutoff_source), ("haneoka","haneoka","haneoka"))
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
        self.assertLessEqual(len(after), 3_000_000)

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
