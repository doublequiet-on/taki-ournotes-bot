# L3
# Input: setup_menu 的 QQ app_id／app_secret；面板、菜单定义与 API 地址为模块配置。
# Output: setup_menu 返回 list[str] 操作消息，由 main 打印；远端菜单／面板变更属于副作用。
# Pos: Platform / QQ 的显式菜单管理入口，由 main 的 setup-menu 模式进入；见 ../../../L2-QQ.md。
# Effects/Dependencies: 依赖 aiohttp 与 botpy Token，直接读取远端菜单／面板并按内容执行必要更新。

"""Install discoverable QQ commands without removing unrelated menu entries."""

from __future__ import annotations

import aiohttp
from botpy.robot import Token


BASE = "https://api.bot.qq.com"
MENU_NAME = "Our Notes"
MENU_ITEMS = [
    {"type": "send_message", "name": "查谱面", "send_message": "/查谱面 "},
    {"type": "send_message", "name": "查曲", "send_message": "/查曲 "},
    {"type": "send_message", "name": "查卡", "send_message": "/查卡 "},
    {"type": "send_message", "name": "查支援卡", "send_message": "/查支援卡 "},
    {"type": "send_message", "name": "帮助", "send_message": "/帮助"},
]
PANEL_ITEMS = [
    # QQ stores the command name without '/', then adds the slash in the client UI.
    {"type": "command", "name": "查谱面", "desc": "查完整谱面、颜色与激奏"},
    {"type": "command", "name": "查曲", "desc": "按颜色、激奏、乐队查歌曲"},
    {"type": "command", "name": "查分数表", "desc": "按颜色、激奏、难度查效率"},
    {"type": "command", "name": "查榜线", "desc": "四服歌曲榜线与历史曲线"},
    {"type": "command", "name": "查卡", "desc": "条件查列表，卡牌ID看详情"},
    {"type": "command", "name": "查支援卡", "desc": "SNAP列表与完整技能详情"},
    {"type": "command", "name": "问", "desc": "资料查询、字段短答与续查"},
    {"type": "command", "name": "下一页", "desc": "继续上一份查询列表"},
    {"type": "command", "name": "上一页", "desc": "返回上一页列表"},
    {"type": "command", "name": "选", "desc": "输入当前页编号看详情"},
    {"type": "command", "name": "详情", "desc": "查看已选择项目的详情"},
    {"type": "command", "name": "难度", "desc": "切换已选歌曲难度，如EX"},
    {"type": "command", "name": "数据状态", "desc": "查看当前数据版本"},
    {"type": "command", "name": "帮助", "desc": "查看指令说明与示例"},
    {"type": "command", "name": "介绍", "desc": "了解Taki与资料来源"},
]
GROUP_PANEL_ITEMS = [
    # Follow the Chinese /帮助 order; QQ inserts '/' before the command name.
    {"type": "command", "name": "查曲", "desc": "歌名、颜色、激奏、等级筛选"},
    {"type": "command", "name": "查谱面", "desc": "歌名或ID，可选难度"},
    {"type": "command", "name": "查分数表", "desc": "乐队、颜色、激奏、难度筛选"},
    {"type": "command", "name": "查榜线", "desc": "四服歌曲榜线与历史曲线"},
    {"type": "command", "name": "查卡", "desc": "成员卡列表或ID详情"},
    {"type": "command", "name": "查支援卡", "desc": "SNAP列表或ID详情"},
    {"type": "command", "name": "查卡面", "desc": "输入成员卡ID，只看卡面"},
    {"type": "command", "name": "查支援卡面", "desc": "输入支援卡ID，只看卡面"},
    {"type": "command", "name": "查缩写", "desc": "查角色或乐队的常用昵称"},
    {"type": "command", "name": "问", "desc": "用自然语言查询资料"},
    {"type": "command", "name": "下一页", "desc": "继续上一份查询列表"},
    {"type": "command", "name": "上一页", "desc": "返回上一页列表"},
    {"type": "command", "name": "选", "desc": "输入当前页编号看详情"},
    {"type": "command", "name": "详情", "desc": "查看已选择项目的详情"},
    {"type": "command", "name": "难度", "desc": "切换已选歌曲难度，如EX"},
    {"type": "command", "name": "数据状态", "desc": "查看数据同步和缓存状态"},
    {"type": "command", "name": "调试数据", "desc": "查看本次进程AI调用次数"},
    {"type": "command", "name": "帮助", "desc": "全部指令、条件与示例"},
    {"type": "command", "name": "介绍", "desc": "Taki简介和资料来源"},
    {"type": "command", "name": "语言", "desc": "查看英文、日文指令"},
]


async def setup_menu(app_id: str, app_secret: str) -> list[str]:
    token = Token(app_id, app_secret)
    await token.update_access_token()
    headers = {"Authorization": token.get_string()}
    messages = []
    async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as session:
        async def api(method: str, path: str, **kwargs):
            async with session.request(method, BASE + path, **kwargs) as response:
                body = await response.json(content_type=None)
                if response.status >= 400:
                    raise RuntimeError(f"QQ {method} {path}: HTTP {response.status}: {body}")
                return body

        for scope in ("c2c", "group"):
            listing = await api("GET", "/v2/panels", params={"scope": scope, "limit": 50})
            records = listing.get("records") or []
            remark = f"ournotes-qq-bot-{scope}"
            current = next((row for row in records if (row.get("panel") or {}).get("remark") == remark), None)
            panel_items = GROUP_PANEL_ITEMS if scope == "group" else PANEL_ITEMS
            panel = {"items": panel_items, "remark": remark}
            if current:
                if current.get("panel", {}).get("items") != panel_items:
                    await api("PUT", f"/v2/panels/{current['panel_id']}", json={"panel": panel})
                    messages.append(f"{scope} 指令面板已更新")
                else:
                    messages.append(f"{scope} 指令面板已存在")
            else:
                await api("POST", "/v2/panels", json={"scope": scope, "target_type": "all", "panel": panel})
                messages.append(f"{scope} 指令面板已创建")

        menu = await api("GET", "/v2/menu")
        items = list((menu.get("menu") or {}).get("items") or [])
        changed = False
        ours = {"type": "menu", "name": MENU_NAME, "sub_menu_items": MENU_ITEMS}
        existing = next((i for i, item in enumerate(items) if item.get("name") == MENU_NAME), None)
        if existing is not None:
            current_menu = items[existing]
            if current_menu.get("type") != "menu" or current_menu.get("sub_menu_items") != MENU_ITEMS:
                items[existing] = ours
                changed = True
        elif len(items) < 10:
            items.append(ours)
            changed = True
        else:
            messages.append(f"单聊菜单已达 10 项上限，未添加{MENU_NAME}")
        if changed:
            await api("PUT", "/v2/menu", json={"menu": {"items": items}})
            messages.append("单聊菜单已更新")
        else:
            messages.append("单聊菜单已存在")
    return messages
