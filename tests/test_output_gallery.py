"""Offline exporter acceptance: real pagination, immutable inputs and original bytes."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("output_gallery", ROOT / "scripts/export_output_gallery.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class OutputGalleryTests(unittest.TestCase):
    def test_observation_preserves_bytes_and_captures_native_encoder(self):
        with tempfile.TemporaryDirectory() as directory:
            manifests = []
            for mode in (None, 'visual', 'timing'):
                output = Path(directory) / str(mode)
                command = [sys.executable, '-B', str(ROOT/'scripts/export_output_gallery.py'),
                           '--only','song-list-17-page1','--output',str(output)]
                if mode: command += ['--observe',mode]
                run = subprocess.run(command,cwd=ROOT,capture_output=True,encoding='utf-8',timeout=120)
                self.assertEqual(run.returncode,0,run.stdout+run.stderr)
                manifests.append(json.loads((output/'manifest.json').read_bytes()))
            samples = [m['samples'][0] for m in manifests]
            self.assertEqual(len({r['files'][0]['sha256'] for r in samples}),1)
            visual = samples[1]['observation']
            self.assertEqual(visual['canvases'][0]['size'],samples[1]['files'][0]['encoded_size'])
            self.assertEqual(samples[1]['files'][0]['canvas_indices'],[0])
            self.assertEqual(len(visual['canvases'][0]['pixel_sha256']),64)
            self.assertGreater(len(visual['drawn_text']),17)
            self.assertEqual(len(visual['layouts'][0]['records']),17)
            self.assertTrue(visual['fonts'])
            self.assertTrue(visual['encoding_attempts'])
            self.assertGreater(visual['process_peak_memory']['bytes'],0)
            self.assertFalse((Path(directory)/'timing'/'canvases').exists())
            self.assertNotIn('pixel_sha256',samples[2]['observation']['canvases'][0])

    def test_source_archive_has_explicitly_unavailable_git_metadata(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(MODULE,'ROOT',Path(directory)):
            self.assertEqual(MODULE.version_info(),(None,False,None))

    def test_missing_baseline_does_not_prevent_export_version_capture(self):
        with patch.object(MODULE,'BASELINE','0'*40):
            version,available,changes=MODULE.version_info()
        self.assertFalse(available)
        self.assertIsNone(changes)
        # An archive has no HEAD either; a shallow checkout retains its HEAD.
        self.assertEqual(version,MODULE.git('rev-parse','HEAD',optional=True))

    def test_output_cannot_overlap_read_only_inputs(self):
        source = Path(tempfile.gettempdir()) / "gallery-input"
        for output in (source, source / "output", source.parent):
            with self.assertRaises(ValueError):
                MODULE.guard_paths(output, [source])

    def test_offline_pages_art_fallback_and_original_bytes(self):
        fixture = ROOT / "tests/fixtures/haneoka_catalog.json"
        before = hashlib.sha256(fixture.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            command = [sys.executable, "-B", str(ROOT / "scripts/export_output_gallery.py"), "--output", str(output)]
            for case in ("song-list-17-page1", "song-list-17-page2", "member-art-missing", "easter-947-normal", "text-help"):
                command += ["--only", case]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, encoding="utf-8", timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            manifest = json.loads((output / "manifest.json").read_bytes())
            self.assertEqual(manifest["network"], "blocked")
            self.assertTrue(manifest["inputs_unchanged"])
            rows = {r["id"]: r for r in manifest["samples"]}
            self.assertEqual(len(rows), 5)
            self.assertEqual(rows["song-list-17-page1"]["continuation_choices"], 17)
            self.assertEqual(rows["song-list-17-page2"]["continuation_choices"], 17)
            self.assertEqual(rows["song-list-17-page2"]["context_pages"], 1)
            self.assertEqual(rows["member-art-missing"]["mode"], "text")
            self.assertIn("卡面图片暂不可用", rows["member-art-missing"]["files"][0]["text"])
            for row in rows.values():
                self.assertEqual(row["status"], "success")
                for item in row["files"]:
                    self.assertTrue((output / item["text_file"]).is_file())
                    if item["image_file"]:
                        raw = (output / item["image_file"]).read_bytes()
                        self.assertEqual(item["sha256"], hashlib.sha256(raw).hexdigest())
                        self.assertEqual(len(raw), item["bytes"])
                        with Image.open(output / item["image_file"]) as image:
                            self.assertEqual(list(image.size), item["encoded_size"])
            egg_file = output / rows["easter-947-normal"]["files"][0]["image_file"]
            self.assertEqual(egg_file.read_bytes(), (ROOT / "src/ournotes_bot/platforms/qq/assets/card-947.jpg").read_bytes())
            page = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn("合成测试数据，不代表游戏实况", page)
            self.assertNotIn("<script src=", page)
            self.assertEqual(set(p.name for p in output.iterdir() if p.is_file()),
                             {"index.html", "manifest.json", "inventory.md", "field-map.md", "sketch-worksheet.md", "theme-reference.md", "benchmark.json", "benchmark.md"})
        self.assertEqual(before, hashlib.sha256(fixture.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
