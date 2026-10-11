"""Catalog publication must not make hot status reads wait for source I/O."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from ournotes_bot.commands import handle_command, resolve_command
from ournotes_bot.data import DataError, SongRepository
from ournotes_bot.query.continuation import catalog_version
from ournotes_bot.query.entity_lexicon import alias_version
from ournotes_bot.sources.haneoka import catalog


class ObservedLock:
    """Signal a named worker's lock attempt before it can wait on the real lock."""

    def __init__(self, lock, attempts):
        self.lock, self.attempts = lock, attempts

    def __enter__(self):
        event = self.attempts.get(threading.current_thread().name)
        if event is not None:
            event.set()
        self.lock.acquire()
        return self

    def __exit__(self, *args):
        self.lock.release()


class CatalogConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.saved = json.loads((Path(__file__).parent / "fixtures/haneoka_catalog.json").read_bytes())["snapshot"]
        self.saved["documents"]["jp"].setdefault("ui-marks", {})
        self.now = 1100.
        self.catalog_calls, self.meta_calls = [], []
        self.source = catalog.CatalogRepository(Path(self.temp.name) / "catalog.json",
                                                fetch=self.fetch_catalog, clock=lambda: self.now)
        self.repo = SongRepository("", Path(self.temp.name) / "main.json",
                                   data_source="haneoka")
        self.repo._catalog_repository = self.source

    def fetch_catalog(self, url, limit, timeout):
        self.catalog_calls.append(url)
        server, resource = urlsplit(url).path.split("/")[-2:]
        identity = self.saved["identities"][server]
        value = identity if resource == "release" else self.saved["documents"][server][resource]
        ids = parse_qs(urlsplit(url).query).get("id")
        if ids:
            value = {"items": {key: value[key] for key in ids}, "missing": []}
        return catalog.encoded(value), {"x-haneoka-release-id": identity["releaseId"],
                                        "x-haneoka-source-id": identity["sourceId"]}


    def advance(self):
        self.saved = copy.deepcopy(self.saved)
        self.now += 100
        # The later publication deliberately has a lexically smaller release.
        self.saved["identities"]["jp"]["releaseId"] = "r-00000000000000000000"
        for server in ("jp", "intl"):
            songs = self.saved["documents"][server]["songs"]
            songs.pop(next(reversed(songs)))
            self.saved["documents"][server]["catalog"]["resources"]["songs"]["count"] = len(songs)

    def start(self, action, *, name=None):
        done, outcome = threading.Event(), {}

        def run():
            try:
                outcome["result"] = action()
            except BaseException as exc:
                outcome["error"] = exc
            finally:
                done.set()

        thread = threading.Thread(target=run, daemon=True, name=name)
        thread.start()
        return thread, done, outcome

    def finish(self, worker):
        thread, done, outcome = worker
        thread.join(5)
        self.assertFalse(thread.is_alive(), "worker did not stop")
        if "error" in outcome:
            raise outcome["error"]
        return outcome.get("result")

    def cleanup_workers(self, release, *workers):
        release.set()
        for worker in workers:
            if worker is not None:
                worker[0].join(5)
        self.assertFalse(any(worker[0].is_alive() for worker in workers if worker is not None))
        for worker in workers:
            if worker is not None and "error" in worker[2]:
                raise worker[2]["error"]

    def previous_version(self):
        # Original hash contract: list repr, metadata, both source settings, aliases.
        digest = hashlib.sha256()
        for value in (self.repo.metadata, self.repo.songs, self.repo.cards, self.repo.support_cards,
                      self.repo.chart_source, self.repo.data_source):
            digest.update(repr(value).encode("utf-8"))
        digest.update(str(alias_version()).encode("ascii"))
        return digest.hexdigest()

    def test_hot_status_completes_while_refresh_http_is_paused(self):
        self.repo.load()
        expected = handle_command("/数据状态", self.repo)
        entered, release = threading.Event(), threading.Event()

        def paused(url, limit, timeout):
            if "/jp/release?" in url:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("refresh fixture was not released")
            return self.fetch_catalog(url, limit, timeout)

        reader = None
        with patch.object(self.source, "fetch", side_effect=paused):
            writer = self.start(self.repo.refresh)
            try:
                self.assertTrue(entered.wait(3), "refresh did not reach HTTP")
                reader = self.start(lambda: handle_command("/数据状态", self.repo))
                self.assertTrue(reader[1].wait(1), "hot status waited for unrelated catalog HTTP")
                self.assertFalse(writer[1].is_set())
                actual = self.finish(reader)
                self.assertEqual(actual, expected)
            finally:
                self.cleanup_workers(release, writer, reader)

    def test_hot_catalog_reads_do_not_wait_for_cache_write(self):
        self.repo.load()
        before = self.repo.capture_catalog()
        old_status, old_version = handle_command("/数据状态", self.repo), catalog_version(self.repo)
        self.advance()
        entered, release = threading.Event(), threading.Event()
        original = self.source._write

        def paused(candidate):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("cache write fixture was not released")
            original(candidate)

        reader = None
        with patch.object(self.source, "_write", side_effect=paused):
            writer = self.start(self.repo.refresh)
            try:
                self.assertTrue(entered.wait(3))
                reader = self.start(lambda: (self.repo.capture_catalog(),
                                            handle_command("/数据状态", self.repo), catalog_version(self.repo)))
                self.assertTrue(reader[1].wait(1), "hot read waited for disk I/O")
                self.assertEqual(self.finish(reader), (before, old_status, old_version))
                self.assertFalse(writer[1].is_set())
            finally:
                self.cleanup_workers(release, writer, reader)
        after = self.repo.capture_catalog()
        self.assertEqual((len(after.songs), after.metadata["data_version"], after.cache_state),
                         (1, "r-00000000000000000000", "fresh"))
        self.assertEqual(after.last_successful_sync_at, after.metadata["cached_at"])

    def test_related_readers_wait_for_complete_publication(self):
        self.repo.load()
        self.advance()
        entered, release = threading.Event(), threading.Event()
        readers = []
        names = ("view", "version", "status")
        attempts = {name: threading.Event() for name in names}
        self.repo._song_lock = ObservedLock(self.repo._song_lock, attempts)
        original = SongRepository.__setattr__

        def paused(repo, name, value):
            original(repo, name, value)
            if repo is self.repo and name == "songs":
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("publication fixture was not released")

        actions = (self.repo.capture_catalog, lambda: catalog_version(self.repo),
                   lambda: handle_command("/数据状态", self.repo))
        with patch.object(SongRepository, "__setattr__", paused):
            writer = self.start(self.repo.refresh)
            try:
                self.assertTrue(entered.wait(3))
                for name, action in zip(names, actions):
                    worker = self.start(action, name=name)
                    readers.append(worker)
                    self.assertTrue(attempts[name].wait(1), f"{name} did not capture publication")
                    self.assertFalse(worker[1].is_set(), f"{name} consumed partial publication")
            finally:
                self.cleanup_workers(release, writer, *readers)
        expected = tuple(action() for action in actions)
        self.assertEqual(tuple(self.finish(worker) for worker in readers), expected)
        self.assertEqual(expected[1], self.previous_version())

    def test_refresh_is_serialized_through_publication(self):
        self.repo.load()
        entered, release, second_attempt = threading.Event(), threading.Event(), threading.Event()
        self.repo._catalog_refresh_lock = ObservedLock(self.repo._catalog_refresh_lock,
                                                      {"second-refresh": second_attempt})
        original = self.source.load
        calls = []

        def paused(refresh=False):
            result = original(refresh)
            calls.append(result[1]["data_version"])
            if len(calls) == 1:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("first load fixture was not released")
            return result

        second = None
        with patch.object(self.source, "load", side_effect=paused):
            first = self.start(self.repo.refresh)
            try:
                self.assertTrue(entered.wait(3))
                self.advance()
                second = self.start(self.repo.refresh, name="second-refresh")
                self.assertTrue(second_attempt.wait(1))
                self.assertEqual(len(calls), 1, "second preparation overtook first publication")
                self.assertFalse(second[1].is_set())
            finally:
                self.cleanup_workers(release, first, second)
        self.assertEqual(calls, ["r-2d7fe14470e2e90b2aae", "r-00000000000000000000"])
        self.assertEqual(self.repo.metadata["data_version"], calls[-1])
        self.assertEqual(self.source._read()["identities"]["jp"]["releaseId"], calls[-1])

    def test_failed_refresh_keeps_verified_facts_and_recovers(self):
        self.repo.load()
        before = self.repo.capture_catalog()
        with patch.object(self.source, "fetch", side_effect=OSError("offline")):
            self.repo.refresh()
        stale = self.repo.capture_catalog()
        self.assertEqual(stale.cache_state, "stale")
        self.assertEqual(stale.metadata, before.metadata)
        self.assertEqual([s.id for s in stale.songs], [s.id for s in before.songs])
        self.assertEqual(stale.last_successful_sync_at, before.last_successful_sync_at)
        self.advance()
        self.repo.refresh()
        self.assertEqual(self.repo.cache_state, "fresh")
        self.assertEqual(len(self.repo.songs), 1)

    def test_unsaved_publication_keeps_last_success_and_disk_recovers(self):
        self.repo.load()
        old = self.repo.capture_catalog()
        old_disk = self.source.path.read_bytes()
        self.advance()
        with patch.object(self.source, "_write", side_effect=OSError("disk full")):
            self.repo.refresh()
        current = self.repo.capture_catalog()
        self.assertEqual((current.cache_state, len(current.songs)), ("unsaved", 1))
        self.assertEqual(current.last_successful_sync_at, old.last_successful_sync_at)
        self.assertNotEqual(current.metadata["cached_at"], old.metadata["cached_at"])
        self.assertEqual(self.source.path.read_bytes(), old_disk)
        self.repo.load()  # Hot unsaved state must not claim a successful save.
        self.assertEqual(self.repo.cache_state, "unsaved")
        self.repo.refresh()
        self.assertEqual(self.repo.cache_state, "fresh")
        self.assertEqual(self.repo.last_successful_sync_at, self.repo.metadata["cached_at"])
        self.assertNotEqual(self.source.path.read_bytes(), old_disk)

    def test_cold_failure_and_unexpected_exception_release_refresh_lock(self):
        with patch.object(self.source, "fetch", side_effect=OSError("offline")):
            with self.assertRaises(DataError):
                self.repo.load()
        empty = self.repo.capture_catalog()
        self.assertEqual((empty.songs, empty.metadata, empty.cache_state), ([], {}, "unknown"))
        self.repo.load()
        old = self.repo.capture_catalog()
        with patch.object(self.source, "load", side_effect=LookupError("unexpected")):
            with self.assertRaises(LookupError):
                self.repo.refresh()
        self.assertEqual(self.repo.capture_catalog(), old)
        worker = self.start(self.repo.refresh)
        self.finish(worker)
        self.assertEqual(self.repo.cache_state, "fresh")

    def test_captured_records_survive_later_refresh_including_nested_mappings(self):
        self.repo.load()
        view = self.repo.capture_catalog()
        expected = copy.deepcopy(view)
        self.assertEqual(catalog_version(self.repo), self.previous_version())
        self.advance()
        self.repo.refresh()
        self.assertEqual(view, expected)
        current = self.repo.capture_catalog()
        self.assertIsNot(current.songs, view.songs)
        self.assertIsNot(current.metadata, view.metadata)
        self.assertIsNot(current.cards[0].catalog, view.cards[0].catalog)
        self.assertIsNot(current.cards[0].localized, view.cards[0].localized)
        self.assertEqual(catalog_version(self.repo), self.previous_version())

    def test_version_hash_uses_captured_references_without_holding_publication_lock(self):
        self.repo.load()
        entered, release = threading.Event(), threading.Event()

        class PausedMetadata(dict):
            def __repr__(metadata):
                if threading.current_thread().name == "hash-reader":
                    entered.set()
                    if not release.wait(5):
                        raise RuntimeError("hash fixture was not released")
                return dict.__repr__(metadata)

        self.repo.metadata = PausedMetadata(self.repo.metadata)
        expected = self.previous_version()
        reader = self.start(lambda: catalog_version(self.repo), name="hash-reader")
        writer = None
        try:
            self.assertTrue(entered.wait(1))
            self.advance()
            writer = self.start(self.repo.refresh)
            self.assertTrue(writer[1].wait(1), "publication waited for catalog hashing")
            self.finish(writer)
            self.assertFalse(reader[1].is_set())
        finally:
            self.cleanup_workers(release, reader, writer)
        self.assertEqual(self.finish(reader), expected)
        self.assertNotEqual(catalog_version(self.repo), expected)

    def test_concurrent_cold_loads_initialize_one_source(self):
        entered, release, second_attempt = threading.Event(), threading.Event(), threading.Event()
        self.repo._catalog_repository = None
        self.repo._catalog_refresh_lock = ObservedLock(self.repo._catalog_refresh_lock,
                                                      {"second-load": second_attempt})

        def construct(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("constructor fixture was not released")
            return self.source

        second = None
        with patch.object(catalog, "CatalogRepository", side_effect=construct) as factory:
            first = self.start(self.repo.load)
            try:
                self.assertTrue(entered.wait(1))
                second = self.start(self.repo.load, name="second-load")
                self.assertTrue(second_attempt.wait(1))
                self.assertEqual(factory.call_count, 1)
                self.assertFalse(second[1].is_set())
            finally:
                self.cleanup_workers(release, first, second)
            self.assertEqual(factory.call_count, 1)
        self.assertEqual(self.repo.cache_state, "cached")
        self.assertEqual(len(self.repo.songs), 2)


if __name__ == "__main__":
    unittest.main()
