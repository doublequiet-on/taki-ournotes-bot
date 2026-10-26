"""Architecture mutations must fail even when old edges are grandfathered."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("taki_architecture", ROOT / "scripts/check_architecture.py")
gate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gate
spec.loader.exec_module(gate)


class ArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write("__init__", "")
        self.write("commands", "")
        self.policy = {"schema": 1, "debts": {}, "exceptions": []}

    def write(self, module, code):
        path = self.root / "src/ournotes_bot" / (module.replace(".", "/") + ".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")

    def check(self):
        (self.root / "architecture_exceptions.json").write_text(json.dumps(self.policy), encoding="utf-8")
        return gate.check(self.root)[:2]

    def test_real_checkout_is_within_reviewed_edges(self):
        new, stale, _ = gate.check(ROOT)
        self.assertEqual(new, set())
        self.assertEqual(stale, set())

    def test_core_rejects_sdk_lazy_type_and_relative_imports(self):
        cases = ("import botpy", "def f():\n import botpy.http",
                 "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n from botpy import Client",
                 "from ..platforms.qq import qq")
        for code in cases:
            with self.subTest(code=code):
                self.write("query.new", code)
                self.assertIn("core-no-qq", {v.rule for v in self.check()[0]})

    def test_source_query_and_bootstrap_new_edges_fail(self):
        for module, code, rule in (
            ("sources.new", "from ..query import new", "source-direction"),
            ("query.new", "from .. import commands", "query-direction"),
            ("query.new", "import ournotes_bot.main", "no-bootstrap-backedge"),
            ("sources.new", "import ournotes_bot.ai_client", "source-direction"),
        ):
            with self.subTest(module=module, code=code):
                self.write(module, code)
                self.assertIn(rule, {v.rule for v in self.check()[0]})

    def test_new_pure_modules_cannot_hide_imports_through_helpers(self):
        self.write("domain.records", "from . import helpers")
        self.write("domain.helpers", "from .. import data")
        self.write("data", "")
        self.assertIn(gate.Violation("pure-contract", "ournotes_bot.domain.helpers", "ournotes_bot.data"), self.check()[0])
        for module in ("query.contracts", "query.songs.contracts", "query.pagination", "rendering.contracts", "sources.errors"):
            self.write(module, "import urllib.request")
            self.assertTrue(any(v.source == "ournotes_bot." + module and v.rule == "pure-contract" for v in self.check()[0]))

    def test_haneoka_foundations_cannot_import_concrete_adapters(self):
        self.write("sources.haneoka.chart_data", "")
        for module in ("sources.haneoka.transport", "sources.haneoka.identity"):
            self.write(module, "from . import chart_data")
            self.assertIn("source-foundation", {v.rule for v in self.check()[0]})

    def test_public_facilities_are_not_a_rendering_or_query_bypass(self):
        self.write("sources.haneoka.transport", "")
        for module, rule in (("query.new", "query-direction"), ("rendering.new", "rendering-direction")):
            self.write(module, "from ..sources.haneoka.transport import public_get")
            self.assertIn(rule, {v.rule for v in self.check()[0]})

    def test_catalog_activity_and_assets_no_longer_depend_on_chart_implementation(self):
        edges, _ = gate.collect(ROOT / "src")
        owners = {"catalog", "catalog_records", "catalog_assets", "event_metadata", "event_cutoffs", "transport", "identity"}
        self.assertFalse(any(edge.source in {"ournotes_bot.sources.haneoka." + name for name in owners}
                             and edge.target == "ournotes_bot.sources.haneoka.chart_data" for edge in edges))

    def test_private_access_is_symbol_exact_for_import_alias_and_getattr(self):
        self.write("commands", "")
        for code in ("from ..commands import _split_page as page",
                     "from .. import commands as c\nc._split_page('x')",
                     "import ournotes_bot.commands as c\ngetattr(c, '_split_page')('x')"):
            self.write("query.new", code)
            self.assertIn(gate.Violation("private-boundary", "ournotes_bot.query.new", "ournotes_bot.commands", "_split_page"), self.check()[0])

    def test_dynamic_imports_are_not_a_bypass(self):
        for code in ("import importlib as loader\nloader.import_module('botpy.http')",
                     "from importlib import import_module as load\nload('botpy')",
                     "__import__('ournotes_bot.platforms.qq.qq')"):
            self.write("query.new", code)
            self.assertIn("core-no-qq", {v.rule for v in self.check()[0]})
        self.write("query.new", "import importlib\nimportlib.import_module(name)")
        self.assertIn("dynamic-import", {v.rule for v in self.check()[0]})

    def test_debt_never_covers_another_source_or_private_symbol(self):
        self.policy["debts"] = {"old": {"reason": "Existing pagination", "owner": "Query", "remove_when": "Pagination extraction"}}
        self.policy["exceptions"] = [["query-direction", "query.old", "commands", "", "old"],
                                     ["private-boundary", "query.old", "commands", "_split_page", "old"]]
        self.write("commands", "")
        self.write("query.old", "from ..commands import _split_page")
        self.assertEqual(self.check(), (set(), set()))
        self.write("query.old", "from ..commands import _split_page, _new_secret")
        self.write("query.new", "from ..commands import _split_page")
        new, stale = self.check()
        self.assertFalse(stale)
        self.assertIn(gate.Violation("private-boundary", "ournotes_bot.query.old", "ournotes_bot.commands", "_new_secret"), new)
        self.assertIn(gate.Violation("query-direction", "ournotes_bot.query.new", "ournotes_bot.commands"), new)
        self.write("query.old", "")
        self.assertTrue(self.check()[1], "removed debt must be removed from the policy")

    def test_invalid_policy_is_rejected(self):
        self.policy["debts"] = {"old": {"reason": "existing", "owner": "Query", "remove_when": "migration"}}
        for rule, source, target in (("query-direction", "query.*", "commands"),
                                     ("core-no-qq", "query.old", "platforms.qq"),
                                     ("pure-contract", "domain.records", "data")):
            self.policy["exceptions"] = [[rule, source, target, "", "old"]]
            with self.subTest(rule=rule), self.assertRaises(ValueError):
                self.check()
        self.policy["exceptions"] = [["query-direction", "query.old", "commands", "", "old"]] * 2
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.check()

    def test_cli_is_nonzero_for_a_real_source_mutation(self):
        self.write("query.new", "import botpy")
        self.check()
        run = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/check_architecture.py"), "--root", str(self.root)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        self.assertIn("NEW core-no-qq", run.stdout)

    def test_publish_waits_for_architecture_and_existing_jobs(self):
        # No YAML package is needed by this development-only gate.
        workflow = (ROOT / ".github/workflows/checks.yml").read_text(encoding="utf-8")
        publish = workflow.split("  publish-linux-bundle:", 1)[1]
        needs = next(line.strip() for line in publish.splitlines() if line.strip().startswith("needs:"))
        self.assertEqual(needs, "needs: [architecture, test-and-package, offline-linux-bundle, koishi-probe, koishi-combination]")
        self.assertIn("scripts/check_architecture.py", workflow.split("  test-and-package:", 1)[0])
        self.assertIn("python-version: ['3.10', '3.12']", workflow.split("  test-and-package:", 1)[0])

    def test_updaters_reject_failed_workflow_before_named_jobs(self):
        # Architecture adds matrix jobs outside Linux's historical fixed list.
        # Both existing controllers must reject the failed overall workflow.
        from types import SimpleNamespace
        from unittest.mock import Mock
        from deploy.linux_backend import LinuxBackend, Rejected
        from scripts.update_bot import ci_passed

        sha = "a" * 40
        run = {"id": 123, "head_sha": sha, "head_branch": "main", "event": "push",
               "name": "Checks", "path": ".github/workflows/checks.yml", "run_number": 1,
               "run_attempt": 1, "status": "completed", "conclusion": "failure"}
        get = Mock(return_value={"workflow_runs": [run]})
        with self.assertRaisesRegex(Rejected, "ci_failed"):
            LinuxBackend.check_ci(SimpleNamespace(_json=get), sha)
        self.assertEqual(get.call_count, 1, "must reject before looking at the old job list")
        self.assertFalse(ci_passed(sha, get=get))
