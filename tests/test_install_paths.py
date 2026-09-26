from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ournotes_bot.config import Settings, runtime_data_dir
from ournotes_bot.visuals import _font


class InstallPathTests(unittest.TestCase):
    def test_installed_copy_uses_user_data_directory(self):
        data_home_variable = "LOCALAPPDATA" if os.name == "nt" else "XDG_DATA_HOME"
        with tempfile.TemporaryDirectory() as directory, \
             patch("ournotes_bot.config.SOURCE_ROOT", None), \
             patch("ournotes_bot.config.load_dotenv"), \
             patch.dict(os.environ, {data_home_variable: directory}, clear=True):
            expected = Path(directory) / "ournotes-qq-bot"
            self.assertEqual(runtime_data_dir(), expected)
            self.assertEqual(Settings.from_env().cache_file, expected / "ournotes-cache.json")

    def test_relative_cache_path_uses_configuration_directory(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch("ournotes_bot.config.CONFIG_ROOT", Path(directory)), \
             patch("ournotes_bot.config.load_dotenv"), \
             patch.dict(os.environ, {"OURNOTES_CACHE_FILE": "data/custom.json"}, clear=True):
            self.assertEqual(Settings.from_env().cache_file, Path(directory) / "data" / "custom.json")

    def test_missing_cjk_font_has_actionable_error(self):
        with patch("ournotes_bot.visuals.FONT_PATHS", []):
            with self.assertRaisesRegex(RuntimeError, "Noto Sans CJK"):
                _font(24)


if __name__ == "__main__":
    unittest.main()
