from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .commands import handle_command
from .ai_query import AIQueryParser, is_ai_request
from .config import Settings
from .data import DataError, SongRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Taki · Our Notes QQ 查询机器人")
    subparsers = parser.add_subparsers(dest="mode", required=True)
    subparsers.add_parser("sync", help="立即下载并构建数据缓存")
    query = subparsers.add_parser("query", help="在命令行测试查询")
    query.add_argument("message", nargs="+", help='例如：查谱 迷星叫')
    subparsers.add_parser("repl", help="启动本地交互测试台")
    subparsers.add_parser("bot", help="连接 QQ 官方机器人并开始服务")
    subparsers.add_parser("setup-menu", help="安装单聊菜单与单聊/群聊快捷指令面板")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = Settings.from_env()
    if args.mode == "setup-menu":
        # Publishing command metadata must not refresh game caches or initialize AI.
        if not settings.app_id or not settings.app_secret:
            print("缺少 QQ_APP_ID 或 QQ_APP_SECRET", file=sys.stderr)
            raise SystemExit(2)
        from .menu import setup_menu
        for result in asyncio.run(setup_menu(settings.app_id, settings.app_secret)):
            print(result)
        return
    repository = SongRepository(settings.data_base, settings.cache_file, settings.cache_ttl_hours)
    ai_parser = AIQueryParser(settings)
    def answer(message: str) -> str | None:
        if is_ai_request(message):
            return ai_parser.answer(message, repository)
        return handle_command(message, repository)
    try:
        repository.load(refresh=args.mode == "sync")
    except DataError as exc:
        print(f"数据初始化失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if args.mode == "sync":
        repository.refresh_song_traits()
        print(
            f"同步完成：{len(repository.songs)} 首曲目、{len(repository.cards)} 张成员卡、"
            f"{len(repository.support_cards)} 张支援卡；成员卡技能索引 "
            f"{repository.metadata.get('member_card_detail_count', 0)}/{len(repository.cards)}，"
            f"版本 {repository.metadata.get('data_version')}"
        )
        return
    if args.mode == "query":
        reply = answer(" ".join(args.message))
        print(reply or "未识别该指令，发送“帮助”查看用法。")
        return
    if args.mode == "repl":
        print("Ournotes 本地测试台已启动。输入“帮助”查看指令，输入 exit 退出。")
        while True:
            try:
                message = input("ournotes> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if message.casefold() in {"exit", "quit", "退出"}:
                return
            reply = answer(message)
            print(reply or "未识别该指令，输入“帮助”查看用法。")
        return
    if not settings.app_id or not settings.app_secret:
        print("缺少 QQ_APP_ID 或 QQ_APP_SECRET；请设置环境变量，或在运行目录的 .env 中填写。", file=sys.stderr)
        raise SystemExit(2)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from .qq import run_bot

    run_bot(settings.app_id, settings.app_secret, repository, settings)


if __name__ == "__main__":
    main()
