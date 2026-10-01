import unittest
from unittest.mock import patch, AsyncMock, Mock
from types import SimpleNamespace

from ournotes_bot.commands import HELP_TEXTS
from ournotes_bot.platforms.qq.menu import GROUP_PANEL_ITEMS, MENU_ITEMS, MENU_NAME, PANEL_ITEMS


def display_width(value: str) -> int:
    return sum(2 if ord(character) > 127 else 1 for character in value)


class MenuLimitsTests(unittest.TestCase):
    def test_menu_setup_does_not_load_game_data_or_ai(self):
        from ournotes_bot.main import main
        with patch('sys.argv', ['ournotes', 'setup-menu']), \
             patch('ournotes_bot.main.Settings.from_env', return_value=SimpleNamespace(app_id='fake', app_secret='fake')), \
             patch('ournotes_bot.main.SongRepository') as repository, \
             patch('ournotes_bot.main.AIQueryParser') as ai, \
             patch('ournotes_bot.platforms.qq.menu.setup_menu', new_callable=AsyncMock, return_value=[]) as setup:
            main()
        setup.assert_awaited_once_with('fake', 'fake')
        repository.assert_not_called()
        ai.assert_not_called()

    def test_qq_menu_limits(self):
        self.assertLessEqual(len(MENU_ITEMS), 5)
        self.assertLessEqual(display_width(MENU_NAME), 10)
        for item in MENU_ITEMS:
            self.assertLessEqual(display_width(item["name"]), 10)

    def test_panel_only_advertises_supported_commands(self):
        self.assertEqual(
            {item["name"] for item in PANEL_ITEMS},
            {"查谱面", "查曲", "查分数表", "查卡", "查支援卡", "问", "数据状态", "帮助", "介绍"},
        )
        for item in PANEL_ITEMS:
            self.assertLessEqual(display_width(item["name"]), 14)
            self.assertLessEqual(display_width(item["desc"]), 30)

    def test_group_panel_covers_chinese_help_within_qq_limits(self):
        names = [item["name"] for item in GROUP_PANEL_ITEMS]
        self.assertEqual(len(names), len(set(names)))
        self.assertLessEqual(len(names), 20)
        self.assertEqual(set(names), {
            "查曲", "查谱面", "查分数表", "查卡", "查支援卡", "查卡面",
            "查支援卡面", "查缩写", "问", "数据状态", "调试数据",
            "帮助", "介绍", "语言",
        })
        for item in GROUP_PANEL_ITEMS:
            self.assertIn("/" + item["name"], HELP_TEXTS["zh"])
            self.assertLessEqual(display_width(item["name"]), 14)
            self.assertLessEqual(display_width(item["desc"]), 30)


class PanelUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_install_updates_only_group_then_is_idempotent(self):
        from ournotes_bot.platforms.qq import menu
        calls = []
        panels = {scope: {"panel_id": scope, "panel": {
            "remark": "ournotes-qq-bot-" + scope, "items": list(PANEL_ITEMS)}}
            for scope in ("c2c", "group")}
        class Response:
            status = 200
            def __init__(self, body): self.body = body
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def json(self, **kwargs): return self.body
        class Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def request(self, method, url, **kwargs):
                calls.append((method, url, kwargs))
                if method == "PUT":
                    panels["group"]["panel"] = kwargs["json"]["panel"]
                    return Response({})
                if url.endswith("/v2/panels"):
                    return Response({"records": [panels[kwargs["params"]["scope"]]]})
                return Response({"menu": {"items": [{"type": "menu", "name": MENU_NAME,
                                                    "sub_menu_items": MENU_ITEMS}]}})
        token = Mock(update_access_token=AsyncMock(), get_string=Mock(return_value="fake"))
        with patch.object(menu, "Token", return_value=token), \
             patch.object(menu.aiohttp, "ClientSession", return_value=Session()):
            await menu.setup_menu("fake", "fake")
            await menu.setup_menu("fake", "fake")
        writes = [(method, url, kwargs) for method, url, kwargs in calls if method != "GET"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][:2], ("PUT", menu.BASE + "/v2/panels/group"))
        self.assertEqual(writes[0][2]["json"]["panel"]["items"], GROUP_PANEL_ITEMS)
        self.assertEqual(panels["c2c"]["panel"]["items"], PANEL_ITEMS)


if __name__ == "__main__":
    unittest.main()
