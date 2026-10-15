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
    modules = ("query.continuation", "query.field_query", "sources.cutoff_history",
               "sources.cutoff_sampler", "rendering.cutoff_trends", "platforms.qq.qq",
               "sources.moenotes_open",
               "sources.haneoka.chart_data", "sources.haneoka.catalog", "sources.haneoka.catalog_records",
               "sources.haneoka.catalog_text", "sources.haneoka.catalog_assets", "sources.haneoka.event_cutoffs",
               "sources.haneoka.event_metadata",
               "query.activity_query", "query.character_query", "query.gacha_query", "query.info_query",
               "query.contracts.info", "domain.info_catalog", "sources.haneoka.info_catalog",
               "rendering.info_layout", "sources.errors", "sources.json_validation",
               "sources.haneoka.transport", "sources.haneoka.identity", "sources.haneoka.chart_paths")
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
                 "docs/IMAGE_OUTPUT.md", "docs/QUERY_UPGRADE_V1.md", "docs/META_OPEN.md", "docs/HANEOKA_CATALOG.md", "docs/HANEOKA_CUTOFFS.md",
                 "docs/HANEOKA_META_CONTRACT.md", "docs/HANEOKA_RETIREMENT.md", "docs/HANEOKA_RESEARCH.md", "docs/HANEOKA_UNIFIED.md",
                 "deploy/query-upgrade-v1.env.example")
    for relative in resources:
        assert (alias_file.parent / relative).is_file(), f"missing installed resource: {relative}"
    from ournotes_bot.sources.cutoff_history import CutoffHistory, OpenCutoffHistory
    from ournotes_bot.sources.moenotes_open import OpenClient, CONTRACT, TIME_KIND
    from ournotes_bot.sources.moenotes_events import EventSnapshot, EventSong, BoardSnapshot
    from ournotes_bot.query.continuation import parse_operation, ContextStore
    from ournotes_bot.sources.errors import ChartDataError
    from ournotes_bot.sources.chart_data import ChartDataError as ExistingChartDataError
    from ournotes_bot.sources.haneoka.identity import release_identity
    assert ChartDataError is ExistingChartDataError
    assert release_identity({"schema": "haneoka-resource-release-identity-v1", "server": "intl",
                             "releaseId": "r-0123456789abcdef0123", "sourceId": "installed-smoke"}, "intl")["server"] == "intl"
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
        from ournotes_bot.sources.cutoff_history import HaneokaCutoffHistory
        from ournotes_bot.sources.haneoka.event_cutoffs import CONTRACT as HC, TIME_KIND as HT
        haneoka = HaneokaCutoffHistory(Path(folder) / "haneoka.sqlite3", min_free_mb=0)
        assert haneoka.record(event, replace(board, scores=(100, 0), source="haneoka", time_kind=HT, contract_version=HC)) == "valid"
        assert haneoka.read(event, song, (1, 2)).points[0].source == "haneoka"
        assert "configured=False" in repr(OpenClient())
    from ournotes_bot.rendering.haneoka_theme import font
    from ournotes_bot.rendering.song_layout import render_list
    from ournotes_bot.sources.haneoka.game_assets import MANIFEST
    from ournotes_bot.sources.haneoka.visual_skill_contracts import contracts
    from ournotes_bot.data import Song
    assert font(24).getlength('完整图片')>0 and font(24,numeric=True).getlength('100')>0
    assert 'skin001.png' in MANIFEST and contracts()
    parts,ranges=render_list((Song(1,'安装测试',('安装测试',),'乐队','','','','','',()),),'安装测试',load=lambda *a:None)
    assert parts[0].image and ranges==((0,1),)
    from ournotes_bot.query.contracts.info import InfoAnswer, InfoView, InfoRow, info_text
    from ournotes_bot.rendering.info_layout import render_info
    views = (InfoView('资料安装测试', rows=(InfoRow('完整结果', ('离线正文',), command='/查角色 1'),)),)
    pages = render_info(InfoAnswer(info_text(views), views=views), load=lambda *a: None)
    assert len(pages) == 1 and pages[0].image and '/查角色 1' in pages[0].text
    print(json.dumps({"installed_version": distribution.version, "modules": len(modules),
                      "isolated": True, "network": False, "resources": True, "history": True}))


if __name__ == "__main__":
    main()
