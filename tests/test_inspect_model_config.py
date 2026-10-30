"""Secret-safe diagnostics with synthetic values only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SAFE_OPEN = bool(getattr(os, 'O_NOFOLLOW', 0) and getattr(os, 'O_NONBLOCK', 0))
SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'inspect_model_config.py'
spec = importlib.util.spec_from_file_location('inspect_model_config', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class InspectModelConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'config.json'

    def config(self, **changes):
        data = {'schema': 'taki-model-config/1', 'profile': {'provider': 'openai', 'model': 'fixture-model', 'config_version': 'fixture-version'}}
        data.update(changes)
        self.path.write_text(json.dumps(data), encoding='utf-8')
        return data

    def cli(self, path=None):
        return subprocess.run([sys.executable, '-B', str(SCRIPT), '--config', str(path or self.path)], capture_output=True, text=True, timeout=5)

    @unittest.skipUnless(SAFE_OPEN, 'requires safe nofollow and nonblocking open')
    def test_free_text_secret_never_emitted_without_inline_secret(self):
        for field in ('provider', 'model', 'config_version'):
            secret = 'synthetic_secret_123456'
            profile = {'provider': 'openai', 'model': 'fixture-model', 'config_version': 'fixture-version'}
            profile[field] = secret
            self.config(profile=profile, nested={'secret': secret})
            result = self.cli()
            self.assertEqual(result.returncode, 0)
            self.assertNotIn(secret, result.stdout + result.stderr)
            data = json.loads(result.stdout)
            self.assertFalse(data['inline_key_present'])
            self.assertNotIn('model', data)
            self.assertNotIn('config_version', data)

    @unittest.skipUnless(SAFE_OPEN, 'requires safe nofollow and nonblocking open')
    def test_allowlisted_provider_and_presence_only(self):
        self.config(secret='SYNTHETIC_PRIVATE_VALUE', groups={'SYNTHETIC_GROUP': {}}, bindings=['SYNTHETIC_BINDING'])
        before = self.path.read_bytes()
        self.path.chmod(0o600)
        result = self.cli()
        self.assertEqual(result.returncode, 0)
        self.assertNotIn('SYNTHETIC_', result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['provider'], 'openai')
        self.assertEqual(data['fingerprint'], hashlib.sha256(before).hexdigest())
        self.assertTrue(data['model_present'] and data['config_version_present'] and data['inline_key_present'])
        if os.name == 'posix': self.assertTrue(data['posix_group_other_mode_clear'])
        self.assertNotIn('owner_only', data)
        self.assertEqual(self.path.read_bytes(), before)

    @unittest.skipUnless(SAFE_OPEN, 'requires safe nofollow and nonblocking open')
    def test_invalid_json_and_schema_are_not_echoed(self):
        for raw in (b'SYNTHETIC_PRIVATE_INVALID', json.dumps({'schema': ['SYNTHETIC_PRIVATE_VALUE']}).encode(), b'\xffSYNTHETIC_PRIVATE_INVALID'):
            self.path.write_bytes(raw)
            result = self.cli()
            self.assertEqual(result.returncode, 1)
            self.assertNotIn('SYNTHETIC_', result.stdout + result.stderr)
            self.assertIn('error', json.loads(result.stdout))

    @unittest.skipUnless(SAFE_OPEN, 'requires safe nofollow and nonblocking open')
    def test_size_limit_and_modes(self):
        self.config(); self.path.chmod(0o640)
        data = module.inspect(self.path)
        if os.name == 'posix': self.assertFalse(data['posix_group_other_mode_clear'])
        self.path.write_bytes(b'x' * (module.MAX_BYTES + 1))
        with self.assertRaises(module.InspectionError): module.inspect(self.path)
        encoded=json.dumps({'schema':'taki-model-config/1','profile':{}}).encode()
        self.path.write_bytes(encoded+b' '*(module.MAX_BYTES-len(encoded)))
        self.assertEqual(module.inspect(self.path)['schema'],'taki-model-config/1')

    @unittest.skipUnless(SAFE_OPEN, 'requires safe nofollow and nonblocking open')
    def test_cli_does_not_load_dotenv(self):
        self.config()
        marker=Path(self.tmp.name)/'.env';marker.write_text('SYNTHETIC_PRIVATE_VALUE=should_not_appear',encoding='utf-8')
        result=subprocess.run([sys.executable,'-B',str(SCRIPT),'--config',str(self.path)],cwd=self.tmp.name,
            capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0)
        self.assertNotIn('SYNTHETIC_PRIVATE_VALUE',result.stdout+result.stderr)
        self.assertEqual(marker.read_text(encoding='utf-8'),'SYNTHETIC_PRIVATE_VALUE=should_not_appear')

    @unittest.skipUnless(SAFE_OPEN and os.name == 'posix', 'requires POSIX nofollow')
    def test_symlink_fifo_and_swap_are_rejected(self):
        self.config(); target = self.path.with_name('target'); self.path.rename(target)
        self.path.symlink_to(target)
        with self.assertRaises(module.InspectionError): module.inspect(self.path)
        self.path.unlink(); os.mkfifo(self.path)
        with self.assertRaises(module.InspectionError): module.inspect(self.path)
        self.path.unlink(); self.config()
        original_open = os.open
        def swap(path, flags):
            Path(path).unlink(); Path(path).symlink_to(target)
            return original_open(path, flags)
        with patch.object(module.os, 'open', side_effect=swap):
            with self.assertRaises(OSError): module.inspect(self.path)

    @unittest.skipUnless(SAFE_OPEN and os.name == 'posix', 'requires POSIX nofollow')
    def test_regular_file_swap_is_rejected(self):
        self.config(); replacement = self.path.with_name('replacement')
        replacement.write_text(json.dumps({'schema':'taki-model-config/1','profile':{}}), encoding='utf-8')
        original_open = os.open
        def swap(path, flags):
            os.replace(replacement, path)
            return original_open(path, flags)
        with patch.object(module.os, 'open', side_effect=swap):
            with self.assertRaises(module.InspectionError): module.inspect(self.path)

    @unittest.skipUnless(SAFE_OPEN and os.name == 'posix', 'requires POSIX nofollow')
    def test_fifo_swap_is_rejected_without_blocking(self):
        self.config();original_open=os.open
        def swap(path,flags):
            Path(path).unlink();os.mkfifo(path)
            return original_open(path,flags)
        with patch.object(module.os,'open',side_effect=swap):
            with self.assertRaises(module.InspectionError):module.inspect(self.path)

    @unittest.skipUnless(SAFE_OPEN and os.name == 'posix', 'requires POSIX nofollow')
    def test_growth_during_read_is_bounded(self):
        self.config()
        original_fdopen=os.fdopen
        class Growing:
            def __init__(self,stream):self.stream=stream
            def __enter__(self):return self
            def __exit__(self,*args):self.stream.close()
            def fileno(self):return self.stream.fileno()
            def read(self,size):
                with self_path.open('ab') as stream:stream.write(b' ' * module.MAX_BYTES)
                return self.stream.read(size)
        self_path=self.path
        with patch.object(module.os,'fdopen',side_effect=lambda fd,mode:Growing(original_fdopen(fd,mode))):
            with self.assertRaisesRegex(module.InspectionError,'configuration_file_too_large'):
                module.inspect(self.path)

    def test_missing_nofollow_fails_before_any_path_access(self):
        with patch.object(module.os, 'O_NOFOLLOW', 0, create=True), patch.object(Path, 'lstat', side_effect=AssertionError('must not read')):
            with self.assertRaisesRegex(module.InspectionError, 'safe_open_unsupported'):
                module.inspect(self.path)

    def test_missing_nonblock_fails_before_any_path_access(self):
        with patch.object(module.os, 'O_NONBLOCK', 0, create=True), patch.object(Path, 'lstat', side_effect=AssertionError('must not read')):
            with self.assertRaisesRegex(module.InspectionError, 'safe_open_unsupported'):
                module.inspect(self.path)


if __name__ == '__main__': unittest.main()
