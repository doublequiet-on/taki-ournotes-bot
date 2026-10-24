# L3
# Input: 含 wheel／sdist 的发布目录，或 check(path) 接收的单个归档路径。
# Output: CLI 输出通过提示或失败；check(path) 成功返回 None，失败抛异常；检查 commands.py、别名及 sdist 必需文档的存在性，并排查禁止条目；仅允许指定彩蛋静态图片。
# Pos: L2.md 的发布包内容检查入口，由 CI 和人工发布验证调用。
# Effects/Dependencies: 使用标准库只读归档并输出结果；通过检查不证明业务运行正常。

"""Check that release archives carry aliases but no local runtime data."""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


PRIVATE_NAMES = {
    ".env", "ournotes-cache.json", "ai-quota.json", "ai-metrics.json",
    "update-notices.sqlite3",
}
BUNDLED_MEDIA = ("ournotes_bot", "platforms", "qq", "assets", "card-947.jpg")
BUNDLED_JSON = {
    ("ournotes_bot", "sources", "haneoka", "visual_skill_contracts.json"),
    ("ournotes_bot", "sources", "haneoka", "game_assets.json"),
}
MEDIA_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mp3"}


def check(path: Path) -> None:
    if path.name.endswith(".whl"):
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            names = [member.name for member in archive.getmembers() if member.isfile()]
    else:
        raise ValueError("unsupported release archive")

    aliases = False
    code = False
    documents: set[str] = set()
    for name in names:
        parts = PurePosixPath(name).parts
        leaf = parts[-1]
        aliases |= leaf == "query_aliases.json"
        code |= len(parts) >= 2 and parts[-2:] == ("ournotes_bot", "commands.py")
        documents.add(leaf)
        runtime_dir = ("asset-cache" in parts or "runtime" in parts
                       or ("data" in parts and not path.name.endswith(".whl")))
        private_env = leaf.startswith(".env.") and leaf != ".env.example"
        if (leaf in PRIVATE_NAMES or private_env or (leaf.endswith(".json") and leaf != "query_aliases.json" and parts[-4:] not in BUNDLED_JSON)
                or leaf.endswith((".log", ".pyc", ".sqlite", ".db", ".sqlite3", ".sqlite3-journal", ".sqlite3-wal", ".sqlite3-shm"))
                or (PurePosixPath(leaf).suffix.lower() in MEDIA_SUFFIXES
                    and parts[-len(BUNDLED_MEDIA):] != BUNDLED_MEDIA) or runtime_dir):
            raise ValueError(f"runtime or private file in {path.name}: {name}")
    if not any(PurePosixPath(name).parts[-len(BUNDLED_MEDIA):] == BUNDLED_MEDIA for name in names):
        raise ValueError(f"missing bundled card-947 image in {path.name}")
    if not aliases or not code:
        raise ValueError(f"missing query aliases or bot code in {path.name}")
    required = {"continuation.py", "field_query.py", "cutoff_history.py", "cutoff_sampler.py", "cutoff_trends.py",
                "knowledge.py", "knowledge_files.py", "knowledge_embeddings.py", "knowledge_retrieval.py",
                "knowledge_service.py", "knowledge_cli.py", "QUERY_CHAT_PACKAGE2B.md", "knowledge-draft.md",
                "runtime_tasks.py", "notice_gateway.py", "notice_reports.py", "data_owner.py", "backend.py", "backend_protocol.py", "backend_bootstrap.py", "delivery_ledger.py", "replies.py", "KOISHI_BACKEND_V1.md",
                "CUTOFF_HISTORY.md", "QUERY_UPGRADE_V1.md", "query-upgrade-v1.env.example",
                "moenotes_open.py", "META_OPEN.md", "THIRD_PARTY.md",
                "transport.py", "identity.py", "chart_paths.py", "errors.py", "json_validation.py",
                "NotoSansSC.ttf", "Roboto.ttf", "NotoSansSC-OFL.txt", "Roboto-OFL.txt",
                "game_assets.json", "visual_skill_contracts.json"}
    retired = {"efficiency_query.py", "haneoka_meta_query.py", "meta_parameters.py", "meta_model.py",
               "song_meta.py", "site_meta.py", "moenotes_music_data.py", "verify_meta_reference.py"}
    if retired & documents:
        raise ValueError(f"retired score-table modules in {path.name}: {sorted(retired & documents)}")
    development_gate = {"check_architecture.py", "test_architecture.py", "architecture_exceptions.json"}
    if development_gate & documents:
        raise ValueError(f"development architecture gate in {path.name}")
    if not required.issubset(documents):
        raise ValueError(f"missing query upgrade modules or instructions in {path.name}")
    if not {"event_cutoffs.py", "HANEOKA_CUTOFFS.md"} <= documents:
        raise ValueError(f"missing Haneoka cutoff preview resources in {path.name}")
    if path.name.endswith(".tar.gz"):
        needed = {"README.md", "THIRD_PARTY.md", "LOCAL_QQ_TEST.md", "昵称词表维护规范.md", ".env.example",
                  "更新日志.md"}
        if not needed.issubset(documents):
            raise ValueError(f"missing source documentation in {path.name}")


def main() -> None:
    folder = Path(sys.argv[1]) if len(sys.argv) == 2 else Path("dist")
    archives = sorted(list(folder.glob("*.whl")) + list(folder.glob("*.tar.gz")))
    if not any(path.name.endswith(".whl") for path in archives) or not any(
        path.name.endswith(".tar.gz") for path in archives
    ):
        raise SystemExit("expected both wheel and source archive")
    for path in archives:
        check(path)
        print(f"Checked {path.name}")


if __name__ == "__main__":
    main()
