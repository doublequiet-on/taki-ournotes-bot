import json
import tempfile
import unittest
from pathlib import Path

from ournotes_bot.commands import resolve_command
from ournotes_bot.data import SongRepository, Song, Chart
from ournotes_bot.query.continuation import capture_context, execute_followup, Operation
from ournotes_bot.sources.haneoka.site_meta import SiteMetaRepository, parse


def documents():
    identity = dict(schema="haneoka-resource-release-identity-v1", server="jp",
                    releaseId="r-0123456789abcdef0123", sourceId="synthetic")
    songs, meta = {}, {}
    for i in range(2):
        key = str(100001 + i)
        songs[key] = dict(musicId=int(key), musicTitle=["合成曲" + str(i)],
                         difficulty=[dict(difficulty=3, difficultyName="expert", displayLevel=25)])
        meta[key] = {"3": {
            "chart": dict(mode="normal", metaStatus="available", scoreKind="chart-relative-factor",
                          eff=3-i, score=7-i, time=90, sr=.5,
                          referenceId="normal-reference", reference=dict(durationSeconds=90)),
            "gekisou": dict(mode="gekisou", metaStatus="available", scoreKind="gekisou-relative",
                            eff=5+i, score=10+i, referenceId="gekisou-reference",
                            reference=dict(durationSeconds=90)),
        }}
    return identity, songs, meta


class SiteMetaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.identity, self.songs, self.meta = documents()
        self.clock = [1000.]
        self.calls = []
        self.repo = SiteMetaRepository(Path(self.tmp.name)/"meta.json", fetch=self.fetch, clock=lambda: self.clock[0])

    def fetch(self, url, limit, timeout):
        self.calls.append(url)
        value = self.identity if "/release?" in url else self.songs if "/songs?" in url else self.meta
        if "/release?" not in url:
            self.assertIn("release=" + self.identity["releaseId"], url)
        return json.dumps(value).encode(), {"x-haneoka-release-id": self.identity["releaseId"],
                                           "x-haneoka-source-id": self.identity["sourceId"]}

    def bot(self):
        bot = SongRepository("", Path(self.tmp.name)/"main.json", meta_source="haneoka-site", data_source="haneoka")
        bot.song_meta = self.repo
        bot.songs = [Song(int(key), row['musicTitle'][0], tuple(row['musicTitle']), "MyGO!!!!!", "", "", "", "", "",
                          (Chart("EXPERT", 25, 25, 100, ""),)) for key, row in self.songs.items()]
        return bot

    def test_version_check_does_not_redownload_unchanged_release(self):
        first = self.repo.get()
        self.assertEqual(len(first.rows), 4)
        self.assertEqual(len(self.calls), 3)
        self.repo.get()
        self.assertEqual(len(self.calls), 3)
        self.clock[0] += 300
        again = self.repo.get()
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(again.fetched_at, first.fetched_at)
        self.assertFalse(again.stale)

    def test_new_release_failure_and_recovery_preserve_same_source_cache(self):
        first = self.repo.get()
        self.clock[0] += 300
        self.identity['releaseId'] = 'r-aaaaaaaaaaaaaaaaaaaa'
        self.meta['100001']['3']['chart']['eff'] = float('nan')
        failed = self.repo.get()
        self.assertTrue(failed.stale)
        self.assertEqual(failed.release, first.release)
        self.clock[0] += 60
        self.meta['100001']['3']['chart']['eff'] = 9
        self.assertEqual(self.repo.get().release, self.identity['releaseId'])

    def test_scenarios_sorted_independently_and_selection_retains_scene(self):
        bot = self.bot()
        answer = resolve_command('/查分数表', bot)
        self.assertEqual(len(answer.meta.panels), 2)
        self.assertEqual([r.song_id for r in answer.meta.panels[0].rows], [100001, 100002])
        self.assertEqual([r.song_id for r in answer.meta.panels[1].rows], [100002, 100001])
        self.assertEqual(answer.meta.panels[1].cells[0][0], '1')
        self.assertEqual(answer.meta.panels[1].selection_numbers[0], 3)
        self.assertIn('300.00%', answer.meta.text)
        self.assertIn('600.00%', answer.meta.text)
        context = capture_context(answer, bot)
        chosen, _ = execute_followup(context, Operation('select', 3), bot)
        self.assertEqual(len(chosen.meta.rows), 1)
        self.assertEqual(chosen.meta.rows[0].scene, 'gekisou')
        self.assertIn('激奏', chosen.meta.title)

    def test_scene_selector_does_not_consume_mission_filter(self):
        bot = self.bot()
        from ournotes_bot.query.efficiency_query import parse_efficiency
        spec = parse_efficiency('/查分数表 激奏 激奏=JUST', bot, direct=True)
        self.assertEqual(spec.meta_scene, 'gekisou')
        self.assertEqual(parse_efficiency(spec.command_label(), bot, direct=True).meta_scene, 'gekisou')
        self.assertIn('JUST', spec.song_filter.query)

    def test_unknown_and_missing_metrics_are_not_zero_or_other_scene(self):
        self.meta['100001']['3']['gekisou']['eff'] = None
        self.meta['100002']['3']['gekisou']['scoreKind'] = 'new-unknown-model'
        answer = resolve_command('/查分数表 激奏', self.bot()).meta
        self.assertFalse(answer.rows)
        self.assertEqual(answer.status, 'data_unavailable')
        self.assertIn('2 条谱面', answer.text)
        self.assertNotIn('300%', answer.text)

    def test_source_header_mismatch_rejected(self):
        self.repo.fetch = lambda *args: (json.dumps(self.identity).encode(), {})
        self.assertIsNone(self.repo.get())

    def test_stale_expiry_future_cache_and_mixed_models_are_not_ranked(self):
        self.repo.get()
        self.repo.fetch = lambda *args: (_ for _ in ()).throw(OSError('offline'))
        self.clock[0] += 86401
        self.assertIsNone(self.repo.get())
        saved = json.loads(self.repo.path.read_text(encoding='utf8'))
        saved['fetched_at'] = '2999-01-01T00:00:00+00:00'
        self.repo.path.write_text(json.dumps(saved),encoding='utf8')
        restored = SiteMetaRepository(self.repo.path,fetch=self.repo.fetch,clock=lambda:self.clock[0])
        self.assertIsNone(restored.get())
        self.repo = SiteMetaRepository(self.repo.path.with_name('other.json'),fetch=self.fetch,clock=lambda:self.clock[0])
        self.meta['100001']['3']['chart']['referenceId'] = 'incompatible'
        result = resolve_command('/查分数表 普通',self.bot())
        self.assertEqual(result.status,'data_unavailable')
        self.assertFalse(result.meta.rows)

    def test_published_reference_values_without_local_formula(self):
        fixture = json.loads((Path(__file__).parent/'fixtures/haneoka_site_meta.json').read_text(encoding='utf8'))
        snapshot = parse(fixture['identity'], fixture['songs'], fixture['meta'], fixture['fetched_at'])
        normal, gekisou = snapshot.rows
        self.assertEqual((normal.score, normal.eff), (7.78400582, 3.74423061))
        self.assertEqual((gekisou.score, gekisou.eff), (14.31736276, 6.88687921))
