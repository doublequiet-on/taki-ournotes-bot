import unittest

from ournotes_bot.menu import MENU_ITEMS, MENU_NAME, PANEL_ITEMS


def display_width(value: str) -> int:
    return sum(2 if ord(character) > 127 else 1 for character in value)


class MenuLimitsTests(unittest.TestCase):
    def test_qq_menu_limits(self):
        self.assertLessEqual(len(MENU_ITEMS), 5)
        self.assertLessEqual(display_width(MENU_NAME), 10)
        for item in MENU_ITEMS:
            self.assertLessEqual(display_width(item["name"]), 10)

    def test_panel_only_advertises_supported_commands(self):
        self.assertEqual(
            {item["name"] for item in PANEL_ITEMS},
            {"查谱面", "查曲", "查卡", "查支援卡", "问", "数据状态", "帮助"},
        )
        for item in PANEL_ITEMS:
            self.assertLessEqual(display_width(item["name"]), 14)
            self.assertLessEqual(display_width(item["desc"]), 30)


if __name__ == "__main__":
    unittest.main()
