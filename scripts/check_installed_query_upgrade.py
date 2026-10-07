# L3
# Input: An isolated Python installation containing the built distribution.
# Output: Installed module/resource/history smoke-check result; no game-data fetch.
# Pos: Scripts / release validation; see L2.md.
# Effects: Temporary SQLite fixture only, no external network or production configuration.
"""Run with python -I from outside the checkout after wheel or sdist installation."""
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import tempfile


def main():
    os.environ.update(QQ_APP_ID="", QQ_APP_SECRET="", AI_API_KEY="", MOENOTES_OPEN_SECRET="", BDON_OPENPLATFORM="")
    def offline(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
            raise AssertionError("installed smoke check must stay offline")
    sys.addaudithook(offline)
    root = Path(__file__).resolve().parents[1]
    modules = ("query.continuation", "query.field_query", "query.efficiency_query", "sources.cutoff_history",
               "sources.cutoff_sampler", "rendering.cutoff_trends", "platforms.qq.qq", "query.meta_parameters",
               "query.meta_model", "sources.moenotes_music_data", "sources.moenotes_open",
               "sources.haneoka.chart_data")
    for name in modules:
        module = importlib.import_module("ournotes_bot." + name)
        assert not Path(module.__file__).resolve().is_relative_to(root), "imported source checkout"
    from ournotes_bot.platforms.qq.qq import _prepare_reply
    import hashlib
    reply = _prepare_reply("/查卡 947", None, None)
    assert hashlib.sha256(reply.image).hexdigest() == "ed23f7f33a54c01b5b636cb312f606662d177cfb3d74cdb459d0b6c1315aa81c"
    assert reply.context is None
    distribution = importlib.metadata.distribution("taki-ournotes-bot")
    from ournotes_bot.query.entity_lexicon import _default_alias_file
    alias_file = _default_alias_file().resolve()
    assert alias_file.is_file()
    assert not alias_file.is_relative_to(root)
    # pip --target relocates data-files without rewriting their RECORD paths.
    # Verify actual installed resources through the runtime's resource locator.
    resources = ("query_aliases.json", "THIRD_PARTY.md", "docs/CUTOFF_HISTORY.md",
                 "docs/QUERY_UPGRADE_V1.md", "docs/META_OPEN.md", "deploy/query-upgrade-v1.env.example")
    for relative in resources:
        assert (alias_file.parent / relative).is_file(), f"missing installed resource: {relative}"
    from ournotes_bot.sources.cutoff_history import CutoffHistory, OpenCutoffHistory
    from ournotes_bot.sources.moenotes_open import OpenClient, CONTRACT, TIME_KIND
    from ournotes_bot.query.meta_parameters import MetaRequest
    from ournotes_bot.query.meta_model import evaluate
    from ournotes_bot.sources.moenotes_music_data import MusicSnapshot, project, FORMAT
    from ournotes_bot.sources.moenotes_events import EventSnapshot, EventSong, BoardSnapshot
    from ournotes_bot.query.continuation import parse_operation, ContextStore
    assert parse_operation("/问 第三个").value == 3
    assert ContextStore().capacity == 1024
    with tempfile.TemporaryDirectory() as folder:
        store = CutoffHistory(Path(folder) / "history.sqlite3", min_free_mb=0)
        song = EventSong("1", "100001", "合成安装测试", collect_status="collecting")
        event = EventSnapshot("jp", "1", "合成安装测试", "nowOn", None, None, (song,))
        stamp = 1790937673000
        board = BoardSnapshot(song, (2**100, 0), stamp, stamp, stamp)
        assert store.record(event, board) == "valid"
        assert store.read(event, song, (1, 2)).points[0].scores == (2**100, 0)
        from dataclasses import replace
        formal = OpenCutoffHistory(Path(folder) / "formal.sqlite3", min_free_mb=0)
        captured = replace(board, source="open", time_kind=TIME_KIND, contract_version=CONTRACT)
        assert formal.record(event, captured) == "valid"
        assert formal.read(event, song, (1, 2)).points[0].source == "open"
        data = {"format": FORMAT, "provenance": {"region": "tw"}, "bands": [], "songs": [
            {"id": 100107, "title": {"ja": "合成安装测试"}, "charts": [
                {"scoreId": 10700, "difficulty": "easy", "notes": {"judged": 0}}]}]}
        snapshot = MusicSnapshot(project(data), "0" * 64, 1, 1)
        row = evaluate(snapshot.data["songs"][0], snapshot.data["songs"][0]["charts"][0], snapshot, MetaRequest(ranking="notes"))
        assert row.metric == 0 and row.score is None
        assert "configured=False" in repr(OpenClient())
    print(json.dumps({"installed_version": distribution.version, "modules": len(modules),
                      "isolated": True, "network": False, "resources": True, "history": True}))


if __name__ == "__main__":
    main()
