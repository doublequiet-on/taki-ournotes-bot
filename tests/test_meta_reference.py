"""Independent pinned frontend outputs; the reference is never computed by Taki."""
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest


class MetaReferenceTests(unittest.TestCase):
    def test_pinned_four_scenes_orders_and_frontiers(self):
        root = Path(__file__).resolve().parents[1]
        module_spec = importlib.util.spec_from_file_location("meta_reference_check", root / "scripts" / "verify_meta_reference.py")
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        fixture = root / "tests" / "fixtures"
        raw = (fixture / "moenotes_meta_minimal.json").read_bytes()
        reference = json.loads((fixture / "moenotes_meta_reference.json").read_bytes())
        result = module.verify(json.loads(raw), reference, sha=hashlib.sha256(raw).hexdigest())
        self.assertEqual(result["cases"], 4)
        self.assertEqual(result["order_lists"], 88)
        self.assertEqual(result["frontier_sets"], 8)
        self.assertEqual(result["status"], "passed")


if __name__ == "__main__":
    unittest.main()
