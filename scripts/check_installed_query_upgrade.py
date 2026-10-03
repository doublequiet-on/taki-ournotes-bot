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
    os.environ.update(QQ_APP_ID="", QQ_APP_SECRET="", AI_API_KEY="")
    def offline(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
            raise AssertionError("installed smoke check must stay offline")
    sys.addaudithook(offline)
    root = Path(__file__).resolve().parents[1]
    modules = ("query.continuation", "query.field_query", "query.efficiency_query", "sources.cutoff_history",
               "sources.cutoff_sampler", "rendering.cutoff_trends", "platforms.qq.qq")
    for name in modules:
        module = importlib.import_module("ournotes_bot." + name)
        assert not Path(module.__file__).resolve().is_relative_to(root), "imported source checkout"
    distribution = importlib.metadata.distribution("taki-ournotes-bot")
    files = {Path(str(p)).name for p in distribution.files}
    assert {"query_aliases.json", "CUTOFF_HISTORY.md", "QUERY_UPGRADE_V1.md", "query-upgrade-v1.env.example"} <= files
    from ournotes_bot.query.entity_lexicon import _default_alias_file
    assert _default_alias_file().is_file()
    assert not _default_alias_file().resolve().is_relative_to(root)
    from ournotes_bot.sources.cutoff_history import CutoffHistory
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
    print(json.dumps({"installed_version": distribution.version, "modules": len(modules),
                      "isolated": True, "network": False, "resources": True, "history": True}))


if __name__ == "__main__":
    main()
