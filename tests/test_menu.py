import unittest
from unittest.mock import patch, AsyncMock, Mock
from types import SimpleNamespace

from ournotes_bot.commands import HELP_TEXTS
from ournotes_bot.menu import GROUP_PANEL_ITEMS, MENU_ITEMS, MENU_NAME, PANEL_ITEMS


def display_width(value: str) -> int:
    return sum(2 if ord(character) > 127 else 1 for character in value)


class MenuLimitsTests(unittest.TestCase):
    def test_menu_setup_does_not_load_game_data_or_ai(self):
        from ournotes_bot.main import main
        with patch('sys.argv', ['ournotes', 'setup-menu']), \
             patch('ournotes_bot.main.Settings.from_env', return_value=SimpleNamespace(app_id='fake', app_secret='fake')), \
             patch('ournotes_bot.main.SongRepository') as repository, \
             patch('ournotes_bot.main.AIQueryParser') as ai, \
             patch('ournotes_bot.menu.setup_menu', new_callable=AsyncMock, return_value=[]) as setup:
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


if __name__ == "__main__":
    unittest.main()
