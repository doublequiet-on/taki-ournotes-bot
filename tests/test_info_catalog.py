"""Facts, identities and old-cache behavior of the pinned information catalog."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ournotes_bot.data import SongRepository
from ournotes_bot.domain.info_catalog import CardRef
from ournotes_bot.sources.haneoka.catalog import CatalogRepository
from ournotes_bot.sources.haneoka.catalog_assets import asset_url, is_catalog_asset
from ournotes_bot.sources.haneoka.info_catalog import convert_info


RELEASE = "r-00000000000000000000"
IDENTITY = {"releaseId": RELEASE, "sourceId": "fixture-jp"}
PREFIX = "/assets/jp/Assets/AddressableResources/"


def texts(japanese, chinese=""):
    return [japanese, "", "", chinese, ""]


def documents():
    return {"jp": {
        "characters": {"1": {"characterId": 1, "characterName": texts("燈"), "bandId": 1,
            "displayOrder": 5, "birthday": {"month": 11, "day": 22}, "bandPart": "Vo.",
            "height": texts("155cm"), "description": texts("<color=#fff>紹介</color><br><size=20>説明</size>"),
            "profileImage": PREFIX + "Character/Image/1/character_thumbnail.png",
            "faceImage": PREFIX + "Character/Image/1/character_face_icon.png"}},
        "bands": {"1": {"bandId": 1, "bandName": texts("MyGO!!!!!")}},
        "gacha": {"9": {"id": "9", "kind": "gacha", "title": texts("募集"), "category": "ad",
            "displayCondition": 1, "limited": False, "startAt": [0, None], "endAt": [0, None],
            "featured": [{"resourceType": 2, "resourceId": 1, "pickup": True},
                         {"resourceType": 3, "resourceId": 1, "pickup": True},
                         {"resourceType": 2, "resourceId": 1, "pickup": True},
                         {"resourceType": 2, "resourceId": 2, "pickup": False}]}},
        "events": {"2": {"id": "2", "kind": "game-event", "title": texts("催事"),
            "startAt": [1000000], "endAt": [2000000], "displayEndAt": [3000000], "musicId": 10,
            "effects": [{"targets": {"memberCard": {"resourceType": 2, "resourceId": 1}}}],
            "rewards": [{"resourceType": 3, "resourceId": 1}, {"resourceType": 1, "resourceId": 1}],
            "pickupCards": [{"resourceType": 2, "resourceId": 2}]}}
    }, "intl": {}}


class InfoConversionTests(unittest.TestCase):
    def setUp(self):
        self.docs = documents()

    def convert(self):
        return convert_info(self.docs, IDENTITY, "2026-10-15T00:00:00+00:00")

    def test_roster_does_not_depend_on_cards_and_uses_source_fields(self):
        info = self.convert()
        person = info.characters[0]
        self.assertEqual(person.id, 1)
        self.assertEqual(person.birthday, (11, 22))
        self.assertEqual(person.fields["height"]["zh"], "155cm")
        self.assertEqual(person.fields["bandPart"]["zh"], "Vo.")
        self.assertEqual(person.fields["description"]["zh"], "紹介\n説明")
        self.assertEqual(person.fields["school"]["zh"], "")
        self.assertTrue(is_catalog_asset(person.image_url))
        self.assertEqual(person.release, RELEASE)

    def test_card_types_and_relation_roles_stay_distinct(self):
        info = self.convert()
        gacha, event = info.gachas[0], info.activities[0]
        self.assertEqual(gacha.pickup_cards, (CardRef(2, 1), CardRef(3, 1)))
        self.assertEqual(gacha.featured_cards, (CardRef(2, 1), CardRef(2, 2), CardRef(3, 1)))
        self.assertEqual(event.bonus_cards, (CardRef(2, 1),))
        self.assertEqual(event.reward_cards, (CardRef(3, 1),))
        self.assertEqual(event.pickup_cards, (CardRef(2, 2),))
        self.assertEqual(event.song_ids, (10,))
        self.assertEqual((event.start_at, event.end_at, event.display_end_at), (1000, 2000, 3000))

    def test_zero_times_do_not_claim_permanent_or_account_eligibility(self):
        gacha = self.convert().gachas[0]
        self.assertIsNone(gacha.start_at)
        self.assertIsNone(gacha.end_at)
        self.assertEqual(gacha.display_condition, 1)
        self.assertEqual(gacha.category, "ad")
        self.assertIs(gacha.limited, False)

    def test_missing_and_confirmed_empty_collections_and_relations_differ(self):
        del self.docs["jp"]["gacha"]
        self.docs["jp"]["events"] = {}
        info = self.convert()
        self.assertNotIn("gachas", info.available)
        self.assertIn("activities", info.available)
        self.assertEqual(info.activities, ())
        self.docs = documents()
        del self.docs["jp"]["events"]["2"]["rewards"]
        self.docs["jp"]["events"]["2"]["effects"] = []
        event = self.convert().activities[0]
        self.assertNotIn("reward_cards", event.available_relations)
        self.assertIn("bonus_cards", event.available_relations)
        self.assertEqual(event.bonus_cards, ())

    def test_unresolved_role_flags_do_not_become_confirmed_empty_relations(self):
        for row in self.docs["jp"]["gacha"]["9"]["featured"]:
            row.pop("pickup")
        self.docs["jp"]["events"]["2"]["rewards"].append({"unresolvedRewardId": 99, "resourceType": 0})
        info = self.convert()
        self.assertNotIn("pickup_cards", info.gachas[0].available_relations)
        self.assertIn("featured_cards", info.gachas[0].available_relations)
        self.assertNotIn("reward_cards", info.activities[0].available_relations)
        self.assertEqual(info.activities[0].reward_cards, (CardRef(3, 1),))

    def test_translations_require_entity_and_field_original_identity(self):
        self.docs["intl"] = copy.deepcopy(self.docs["jp"])
        peer = self.docs["intl"]["characters"]["1"]
        peer["characterName"] = texts("燈", "灯")
        peer["height"] = texts("155cm", "155厘米")
        self.assertEqual(self.convert().characters[0].name, "灯")
        self.assertEqual(self.convert().characters[0].fields["height"]["zh"], "155厘米")
        peer["bandId"] = 2
        self.assertEqual(self.convert().characters[0].name, "燈")
        peer["bandId"] = 1
        peer["height"] = texts("165cm", "165厘米")
        self.assertEqual(self.convert().characters[0].fields["height"]["zh"], "155cm")
        self.docs["intl"]["gacha"]["9"]["title"] = texts("別募集", "其他卡池")
        self.assertEqual(self.convert().gachas[0].title, "募集")

    def test_bad_identity_timestamp_and_card_references_are_rejected(self):
        for field, value in (("id", "10"), ("startAt", [-1]), ("displayCondition", True),
                             ("featured", [{"resourceType": 2, "resourceId": True}])):
            with self.subTest(field=field):
                self.docs = documents()
                self.docs["jp"]["gacha"]["9"][field] = value
                with self.assertRaises(ValueError):
                    self.convert()

    def test_optional_unapproved_art_is_not_downloadable(self):
        self.docs["jp"]["characters"]["1"]["profileImage"] = "https://foreign.test/a.png"
        self.assertEqual(self.convert().characters[0].image_url, "")
        for path in ("Character/Image/1/character_sprite.png", "Image/Event/event_1.png", "Gacha/Logo/gacha_1.png"):
            self.assertTrue(is_catalog_asset(asset_url(PREFIX + path, RELEASE)))
        with self.assertRaises(ValueError):
            asset_url(PREFIX + "Character/Image/1/../../secret.png", RELEASE)


class InfoPublicationTests(unittest.TestCase):
    def test_old_cache_refresh_and_failed_refresh_capture_one_release(self):
        saved = json.loads((Path(__file__).parent / "fixtures/haneoka_catalog.json").read_bytes())["snapshot"]
        with tempfile.TemporaryDirectory() as temp:
            source = CatalogRepository(Path(temp) / "catalog.json", clock=lambda: 1100)
            source.saved = saved
            repo = SongRepository("", Path(temp) / "main.json", data_source="haneoka")
            repo._catalog_repository = source
            repo.load()
            old = repo.capture_catalog()
            self.assertTrue(old.characters)
            self.assertEqual(old.metadata["info_catalog_available"],
                             {"characters": True, "gachas": False, "activities": False})
            fresh = copy.deepcopy(saved)
            fresh["identities"]["jp"]["releaseId"] = RELEASE
            fresh["documents"]["jp"].update(gacha=documents()["jp"]["gacha"], events=documents()["jp"]["events"])
            with patch.object(source, "_collect", return_value=fresh):
                repo.refresh()
            current = repo.capture_catalog()
            self.assertEqual(current.gachas[0].release, RELEASE)
            self.assertEqual(current.characters[0].release, RELEASE)
            self.assertEqual(current.cards[0].catalog["release"], RELEASE)
            self.assertNotEqual(old.characters[0].release, RELEASE)
            self.assertEqual(old.gachas, ())
            broken = copy.deepcopy(fresh)
            broken["documents"]["jp"]["gacha"]["9"]["startAt"] = [-1]
            with patch.object(source, "_collect", return_value=broken):
                repo.refresh()
            stale = repo.capture_catalog()
            self.assertEqual(stale.cache_state, "stale")
            self.assertEqual(stale.gachas, current.gachas)
            self.assertEqual(stale.characters, current.characters)
            self.assertEqual(stale.metadata, current.metadata)


if __name__ == "__main__":
    unittest.main()
