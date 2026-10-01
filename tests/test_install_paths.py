from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ournotes_bot.config import Settings, runtime_data_dir
from ournotes_bot.query import entity_lexicon
from ournotes_bot.visuals import _font


class InstallPathTests(unittest.TestCase):
    def test_source_aliases_still_use_repository_file(self):
        expected = Path(__file__).resolve().parents[1] / "query_aliases.json"
        self.assertEqual(entity_lexicon._default_alias_file(), expected)

    def test_installed_aliases_use_target_or_prefix_share(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module_file = root / "target/ournotes_bot/query/entity_lexicon.py"
            for base in (root / "target", root / "prefix"):
                candidate = base / "share/ournotes-qq-bot/query_aliases.json"
                candidate.parent.mkdir(parents=True)
                candidate.write_text('{}', encoding="utf-8")
                with patch.object(entity_lexicon, "__file__", str(module_file)), \
                     patch.object(entity_lexicon.sys, "prefix", str(root / "prefix")):
                    self.assertEqual(entity_lexicon._default_alias_file(), candidate)
                candidate.unlink()

    def test_configured_alias_file_still_overrides_default(self):
        with tempfile.TemporaryDirectory() as directory:
            configured = Path(directory) / "aliases.json"
            configured.write_text('{}', encoding="utf-8")
            with patch.dict(os.environ, {"OURNOTES_ALIAS_FILE": str(configured)}):
                self.assertEqual(entity_lexicon._alias_file(), configured.resolve())

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
